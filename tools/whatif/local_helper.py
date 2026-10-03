"""Local helper behind the "Update CSV" section of the predictions page.

    python tools/whatif/local_helper.py

A web page cannot press keys in RASAero or write files on your computer, so this small
server does it and the page's buttons talk to it. It listens on 127.0.0.1 only, accepts
requests only from the team's Pages site and from itself, and does exactly three things:

  sweep   run tools/whatif/rasaero_sweep.py (RASAero "Run Test" for alpha 0 to 30)
  update  turn the 31 alpha files into the aero CSV, rebuild the page and 3D flight
          (tools/whatif/build_site.py, as the GitHub Action does), restoring the old
          CSV if the build fails
  cancel  stop the job that is running

It also serves the rebuilt site at http://127.0.0.1:8765/predictions/ so the updated page
can be looked at before anything is committed. Publishing is a normal commit and push of
the CSV; the page lists the commands when an update has finished.

Needs Python 3.9+ with flight_sim installed (and pyautogui for the sweep, Windows only).
Run it with the Python that has them, or pass --python.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ALPHA_COUNT = 31
DEFAULT_PORT = 8765
DEFAULT_ORIGIN = "https://tamusrt.github.io"
Runner = Callable[[list, Callable[[str], None], threading.Event], int]


@dataclass
class Context:
    """Where everything is, read from aero_modeling/whatif_config.json."""

    repo: Path
    config: Path
    key: str
    name: str
    alpha_dir: Path
    csv: Path
    cdx: Path | None
    site: Path
    page_url: str
    python: str
    window: str
    pages_origin: str
    port: int = DEFAULT_PORT


def load_context(config_path: Path, repo: Path, python: str, key: str | None = None, port: int | None = None) -> Context:
    """Read the config; the rocket to update is ``key`` or the config's default."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    rockets = config["rockets"]
    key = key or config.get("default") or next(iter(rockets))
    if key not in rockets:
        raise SystemExit(f"{config_path}: no rocket {key!r}")
    spec = rockets[key]
    if "alpha_dir" not in spec:
        raise SystemExit(f"{config_path}: rocket {key!r} needs an 'alpha_dir' (where RASAero's files go)")
    base = config_path.parent
    helper = config.get("helper", {})
    default = key == (config.get("default") or next(iter(rockets)))
    return Context(
        repo=repo,
        config=config_path,
        key=key,
        name=spec.get("name", key),
        alpha_dir=base / spec["alpha_dir"],
        csv=base / spec["aero"],
        cdx=base / spec["rasaero"] if "rasaero" in spec else None,
        site=repo / "site",
        page_url="/predictions/" if default else f"/predictions/{key.lower()}/",
        python=python,
        window=helper.get("rasaero_window", "RASAero"),
        pages_origin=helper.get("pages_origin", DEFAULT_ORIGIN),
        port=port or helper.get("port", DEFAULT_PORT),
    )


def iso(timestamp: float | None) -> str | None:
    """UTC ISO time of a file time, or None."""
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def subprocess_runner(cmd: list, on_line: Callable[[str], None], cancel: threading.Event, cwd: Path | None = None) -> int:
    """Run ``cmd``, pass each output line to ``on_line``, stop it if ``cancel`` is set."""
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    process = subprocess.Popen(  # noqa: S603  (the commands are fixed by this file)
        [str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, cwd=cwd, env=env
    )

    def watch() -> None:
        while process.poll() is None:
            if cancel.wait(0.2):
                process.terminate()
                return

    threading.Thread(target=watch, daemon=True).start()
    assert process.stdout is not None
    for line in process.stdout:
        on_line(line.rstrip())
    return process.wait()


def import_probe(python: str, module: str) -> bool:
    """Whether ``python`` can import ``module``."""
    try:
        return subprocess.run([python, "-c", f"import {module}"], capture_output=True, timeout=60, check=False).returncode == 0  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return False


class Helper:
    """The jobs, one at a time, and the status the page polls."""

    def __init__(self, ctx: Context, runner: Runner | None = None, probe: Callable[[str], bool] | None = None) -> None:
        self.ctx = ctx
        self.runner = runner or (lambda cmd, on_line, cancel: subprocess_runner(cmd, on_line, cancel, ctx.repo))
        probe = probe or (lambda module: import_probe(ctx.python, module))
        self.sweep_problem: str | None = None
        self.update_problem: str | None = None
        if sys.platform != "win32" and runner is None:
            self.sweep_problem = "The RASAero sweep only runs on Windows."
        elif not probe("pyautogui"):
            self.sweep_problem = "pyautogui is missing: run  pip install pyautogui  with the Python that runs the helper."
        if not probe("flight_sim.whatif.ras_csv"):
            self.update_problem = (
                "The Python that runs the helper cannot import flight_sim.whatif.ras_csv: "
                "install the flight_sim folder (pip install -e <path>) from the branch that has it."
            )
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.busy: str | None = None
        self.progress = 0
        self.error: str | None = None
        self.finished: dict | None = None
        self.log: deque = deque(maxlen=300)

    # ---- files ----
    def alpha_files(self) -> dict:
        """alpha number -> path for every non-empty alphaN.txt (N = 0..30) in the folder."""
        found = {}
        for n in range(ALPHA_COUNT):
            path = self.ctx.alpha_dir / f"alpha{n}.txt"
            if path.is_file() and path.stat().st_size > 0:
                found[n] = path
        return found

    def _rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.ctx.repo.resolve()).as_posix()
        except ValueError:
            return str(path)

    def status(self) -> dict:
        """Everything the page shows."""
        files = self.alpha_files()
        times = [p.stat().st_mtime for p in files.values()]
        csv, cdx = self.ctx.csv, self.ctx.cdx
        index = self.ctx.site / self.ctx.page_url.strip("/") / "index.html"
        publish = [f"git add {self._rel(csv)}"]
        if cdx is not None:
            publish.append(f"git add {self._rel(cdx)}")
        publish += [f'git commit -m "Update {self.ctx.key} RASAero CSV"', "git push"]
        with self.lock:
            return {
                "helper": 1,
                "rocket": self.ctx.name,
                "busy": self.busy,
                "progress": self.progress,
                "error": self.error,
                "finished": self.finished,
                "log": list(self.log)[-30:],
                "alpha": {"dir": self._rel(self.ctx.alpha_dir), "count": len(files), "newest": iso(max(times, default=None)), "oldest": iso(min(times, default=None))},
                "csv": {"path": self._rel(csv), "modified": iso(csv.stat().st_mtime) if csv.exists() else None},
                "cdx": {"path": self._rel(cdx) if cdx else "the RASAero .CDX1", "modified": iso(cdx.stat().st_mtime) if cdx and cdx.exists() else None},
                "site": {"built": iso(index.stat().st_mtime) if index.exists() else None},
                "sweepAvailable": self.sweep_problem is None,
                "sweepProblem": self.sweep_problem,
                "updateAvailable": self.update_problem is None,
                "updateProblem": self.update_problem,
                "publish": publish,
            }

    # ---- jobs ----
    def start(self, job: str) -> str | None:
        """Start ``job`` in the background; returns an error text, or None when it started."""
        problem = {"sweep": self.sweep_problem, "update": self.update_problem}[job]
        if problem:
            return problem
        with self.lock:
            if self.busy:
                return f"Still busy with the {self.busy}."
            self.busy, self.error, self.progress = job, None, 0
            self.log.clear()
            self.cancel_event = threading.Event()
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return None

    def cancel(self) -> None:
        """Ask the running job to stop."""
        self.cancel_event.set()

    def _say(self, line: str) -> None:
        with self.lock:
            self.log.append(line)
            match = re.match(r"Finished (\d+)", line)
            if match:
                self.progress = int(match.group(1)) + 1

    def _run(self, job: str) -> None:
        error = None
        try:
            error = self._sweep() if job == "sweep" else self._update()
        except Exception as exc:  # noqa: BLE001  (shown to the person, never swallowed)
            error = f"{type(exc).__name__}: {exc}"
        if self.cancel_event.is_set() and error:
            error = "Cancelled."
        with self.lock:
            self.busy = None
            self.error = error
            if error is None:
                self.finished = {"job": job, "time": iso(datetime.now(timezone.utc).timestamp())}
                self.log.append("Done." if job == "update" else f"Done: {ALPHA_COUNT} RASAero files written.")

    def _step(self, cmd: list) -> int:
        return self.runner(cmd, self._say, self.cancel_event)

    def _sweep(self) -> str | None:
        ctx = self.ctx
        code = self._step([ctx.python, "-u", HERE / "rasaero_sweep.py", "--out", ctx.alpha_dir, "--window", ctx.window, "--countdown", "5"])
        if code != 0:
            last = [line for line in self.log if line.strip()][-1:] or ["no output"]
            return f"The RASAero sweep stopped: {last[0]}"
        if len(self.alpha_files()) < ALPHA_COUNT:
            return f"The sweep ended but only {len(self.alpha_files())} of {ALPHA_COUNT} files exist."
        return None

    def _update(self) -> str | None:
        ctx = self.ctx
        files = self.alpha_files()
        if len(files) < ALPHA_COUNT:
            missing = [n for n in range(ALPHA_COUNT) if n not in files]
            return f"Only {len(files)} of {ALPHA_COUNT} RASAero files are in {ctx.alpha_dir} (missing alpha {', '.join(map(str, missing[:6]))}{' ...' if len(missing) > 6 else ''}). Run step 1 first."
        if ctx.cdx and ctx.cdx.exists() and min(p.stat().st_mtime for p in files.values()) < ctx.cdx.stat().st_mtime - 60:
            self._say(f"Warning: some RASAero files are older than {ctx.cdx.name}; if the rocket changed since, run step 1 again.")
        old = ctx.csv.read_bytes() if ctx.csv.exists() else None
        temp = ctx.csv.with_name(ctx.csv.name + ".tmp")
        self._say("Turning the RASAero files into the CSV")
        code = self._step([ctx.python, "-m", "flight_sim.whatif.ras_csv", "--in", ctx.alpha_dir, "--out", temp])
        if code != 0:
            temp.unlink(missing_ok=True)
            return "Converting the RASAero files failed; the CSV is unchanged."
        os.replace(temp, ctx.csv)
        self._say("Rebuilding the model and rerunning the 6-DOF sim")
        code = self._step([ctx.python, HERE / "build_site.py", "--config", ctx.config, "--site", ctx.site])
        if code != 0:
            if old is not None:
                ctx.csv.write_bytes(old)
            return "The rebuild failed, so the old CSV was put back. See the lines above."
        return None


def make_handler(helper: Helper) -> type:
    """The request handler class bound to ``helper``."""
    ctx = helper.ctx

    class Handler(SimpleHTTPRequestHandler):
        """JSON api under /api, the built site for everything else."""

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, directory=str(ctx.site), **kwargs)

        def log_message(self, format, *args) -> None:  # noqa: A002
            """Keep the console for the jobs."""

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def _origins(self) -> set:
            return {ctx.pages_origin, f"http://127.0.0.1:{ctx.port}", f"http://localhost:{ctx.port}"}

        def _host_ok(self) -> bool:
            return (self.headers.get("Host") or "") in {f"127.0.0.1:{ctx.port}", f"localhost:{ctx.port}"}

        def _json(self, code: int, body: dict, origin: str | None = None) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            if origin:
                self._cors(origin)
            self.end_headers()
            self.wfile.write(data)

        def _cors(self, origin: str) -> None:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

        def _guard(self, need_origin: bool) -> str | None:
            """The request's allowed origin ('' when there is none); None after sending a refusal."""
            origin = self.headers.get("Origin")
            if not self._host_ok():
                self._json(403, {"error": "wrong Host header"})
                return None
            if origin is None and not need_origin:
                return ""
            if origin not in self._origins():
                self._json(403, {"error": "origin not allowed"})
                return None
            return origin

        def do_OPTIONS(self) -> None:  # noqa: N802
            origin = self._guard(need_origin=True)
            if origin is None:
                return
            self.send_response(204)
            self._cors(origin)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            origin = self._guard(need_origin=False)
            if origin is None:
                return
            if path == "/api/status":
                self._json(200, helper.status(), origin or None)
            elif path == "/":
                self.send_response(302)
                self.send_header("Location", ctx.page_url)
                self.end_headers()
            elif path.startswith("/api/"):
                self._json(404, {"error": "unknown request"}, origin or None)
            else:
                super().do_GET()

        def do_POST(self) -> None:  # noqa: N802
            origin = self._guard(need_origin=True)
            if origin is None:
                return
            job = urlparse(self.path).path.removeprefix("/api/")
            if job == "cancel":
                helper.cancel()
                self._json(200, {"cancelled": True}, origin)
            elif job in ("sweep", "update"):
                error = helper.start(job)
                self._json(409 if error else 200, {"error": error} if error else {"started": job}, origin)
            else:
                self._json(404, {"error": "unknown request"}, origin)

    return Handler


def serve(helper: Helper, port: int) -> ThreadingHTTPServer:
    """Bind 127.0.0.1:``port`` (0 picks a free one) and return the server."""
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(helper))
    server.daemon_threads = True
    helper.ctx.port = server.server_address[1]
    return server


def main(argv: list | None = None) -> int:
    """Command line entry; returns the exit code."""
    repo = HERE.parents[1]
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", maxsplit=1)[0])
    parser.add_argument("--config", type=Path, default=repo / "aero_modeling" / "whatif_config.json")
    parser.add_argument("--rocket", help="key in the config (default: its default rocket)")
    parser.add_argument("--port", type=int, help=f"default {DEFAULT_PORT}")
    parser.add_argument("--python", default=sys.executable, help="Python that has flight_sim (and pyautogui)")
    parser.add_argument("--no-build", action="store_true", help="do not build the site on start when it is missing")
    args = parser.parse_args(argv)

    ctx = load_context(args.config.resolve(), repo, args.python, args.rocket, args.port)
    helper = Helper(ctx)
    ctx.site.mkdir(parents=True, exist_ok=True)
    if not args.no_build and helper.update_problem is None and not (ctx.site / ctx.page_url.strip("/") / "index.html").exists():
        print("Building the page once so there is something to show ...", flush=True)
        subprocess_runner([ctx.python, HERE / "build_site.py", "--config", ctx.config, "--site", ctx.site], print, threading.Event(), repo)
    server = serve(helper, ctx.port)
    url = f"http://127.0.0.1:{ctx.port}{ctx.page_url}"
    print(f"Helper for {ctx.name} running at {url}")
    print(f"  RASAero files: {ctx.alpha_dir}")
    print(f"  CSV:           {ctx.csv}")
    for problem in (helper.sweep_problem, helper.update_problem):
        if problem:
            print(f"  NOTE: {problem}")
    print("Leave this window open and use the Update CSV section at the bottom of the page. Ctrl+C stops it.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
