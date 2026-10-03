"""Opens the built site in a real browser and clicks through it, the way a person would.

    python tools/whatif/tests/browser_test.py --site site [--require-predictions]
    python tools/whatif/tests/browser_test.py --synthetic        # a made-up History site, no build needed

The other tests run the pages' code with stand-ins for the browser. This one uses Chromium
(through Playwright) on the real files, so it catches what only a real browser shows: a script
that stops, a chart that never draws, a tab that opens empty, a 3D scene that stays blank.

What it checks, in order:

* the History tab draws its chart, and has all five tabs;
* the dashed Jarvis lines are on the chart when ``jarvis_by_commit.json`` is there, and the
  Jarvis button takes them off and puts them back;
* the Predictions tab opens inside the page, with its table, its chart and the weather table,
  Jarvis's apogee is not wildly far from OpenRocket's, and the units picked in the page reach it;
* the Vision tab opens, has its canvas, and the 3D scene is drawn (not blank);
* the big red Error bar at the top is there when the site's ``build_status.json`` says something broke, and when a tab
  cannot be loaded (a 404, a page that was not built);
* no tab throws a script error or loads a file that is missing.

A page that has not been built is fine unless ``--require-predictions`` is given: then it is a
failure. The workflow gives that flag only when the build step before it worked. Exit code 1 on
any failure. ``--shots DIR`` saves a picture of each tab, for the workflow to keep.

``--report FILE`` writes what failed as JSON, each failure with the area it belongs to (history, predictions, vision
or jarvis), for ``tools/whatif/site_status.py``: a problem in Predictions or Vision makes the site show their last good
versions with an Error bar, and a problem in History keeps the site from being published.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import re
import socketserver
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
OPENROCKET_TESTS = HERE.parents[1] / "openrocket" / "tests"
# Files that may be missing without it being a problem (the page copes with each).
MAY_BE_MISSING = ("/api/status", "jarvis_by_commit.json", "build_status.json", "favicon.ico")
BROWSER_ARGS = ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader"]
TABS = ("history", "flight", "changelog", "predictions", "vision")


class Report:
    """Collects ok / failed / skipped lines and prints them as they happen."""

    def __init__(self) -> None:
        self.failed: list[dict[str, str]] = []
        self.skipped: list[str] = []

    def check(self, name: str, area: str, test) -> None:
        try:
            detail = test()
        except Exception as error:  # noqa: BLE001  (a failing check is the point)
            lines = str(error).splitlines() or [type(error).__name__]
            self.failed.append({"name": name, "area": area, "message": "; ".join(line.strip() for line in lines)[:600]})
            print(f"  FAIL {name}\n       " + "\n       ".join(lines))
            return
        if isinstance(detail, str) and detail.startswith("skipped"):
            self.skipped.append(name)
            print(f"  skip {name}: {detail[8:].lstrip(': ')}")
        else:
            print(f"  ok   {name}" + (f" ({detail})" if detail else ""))

    def write(self, path: Path) -> None:
        path.write_text(json.dumps({"failed": self.failed, "skipped": self.skipped}, indent=1), encoding="utf-8")


def serve(folder: Path) -> tuple[socketserver.TCPServer, int]:
    """Serve the site on a free local port, quietly, in a background thread."""

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

    handler = functools.partial(Quiet, directory=str(folder))
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def make_synthetic(target: Path) -> None:
    """A small History site, as the History test builds it, with a made-up Jarvis file beside it."""
    sys.path.insert(0, str(OPENROCKET_TESTS))
    import test_site  # type: ignore[import-not-found]

    test_site.build(target)
    data = json.loads((target / "data.json").read_text(encoding="utf-8"))
    designs = {}
    for file, sims in data["designs"].items():
        out = {}
        for sim, rows in sims.items():
            out[sim] = {
                r["sha"]: {k: v * 1.04 for k, v in r["m"].items() if v is not None}
                for r in rows
                if r["ok"] and r.get("sha")
            }
        designs[file] = {"motor": "test.rse", "note": "Each point is made up for this test", "skipped": 0, "sims": out}
    (target / "jarvis_by_commit.json").write_text(json.dumps({"designs": designs}), encoding="utf-8")


def numbers(text: str) -> float | None:
    """The number in a table cell such as '30,413' or '+3.6%'."""
    match = re.search(r"[-+]?\d[\d,]*\.?\d*", text.replace("−", "-"))
    return float(match.group(0).replace(",", "")) if match else None


def area_of(where: str) -> str:
    """Which part of the site an address (or a path) belongs to."""
    if "/predictions/viewer/" in where or "three.js" in where:
        return "vision"
    return "predictions" if "/predictions/" in where else "history"


# Run in every page and frame before its own scripts: say which page a script error came from.
WATCH = """(() => {
  const say = what => console.error('SRT_ERROR\\t' + location.pathname + '\\t' + what);
  addEventListener('error', e => { if (e.message) say(e.message); });
  addEventListener('unhandledrejection', e => say('unhandled promise: ' + ((e.reason && e.reason.message) || e.reason)));
})();"""


class Browser:
    """One Chromium page on the served site, with every error it meets written down by area."""

    def __init__(self, page, base: str, site: Path, shots: Path | None) -> None:
        self.page, self.base, self.site, self.shots = page, base, site, shots
        self.problems: list[tuple[str, str]] = []
        page.add_init_script(WATCH)
        page.on("response", self._response)
        page.on("requestfailed", self._failed)
        page.on("console", self._console)

    def _console(self, message) -> None:
        if message.type != "error" or "Failed to load resource" in message.text:
            return
        if message.text.startswith("SRT_ERROR\t"):
            _, path, what = message.text.split("\t", 2)
            self.problems.append((area_of(path), f"a script stopped: {what}"))
        else:
            self.problems.append((area_of(message.location.get("url", "")), f"the page logged an error: {message.text}"))

    def _may_be_missing(self, url: str) -> bool:
        """A file the page copes with having none of: the optional ones, and a tab that was not built."""
        path = url.split("?")[0].split("#")[0]
        if path.endswith(MAY_BE_MISSING):
            return True
        tail = path.split("/", 3)[-1]  # the file's place in the site
        return tail in ("predictions/index.html", "predictions/viewer/index.html") and not (self.site / tail).is_file()

    def _response(self, response) -> None:
        if response.status >= 400 and not self._may_be_missing(response.url):
            self.problems.append((area_of(response.url), f"a file is missing ({response.status}): {response.url.split('/', 3)[-1]}"))

    def _failed(self, request) -> None:
        # "aborted" is the browser cancelling a request itself (a page left, a reply nobody read), not a failure;
        # the web fonts have fallbacks, so a computer that cannot reach Google Fonts is not a failure either.
        if request.failure and "ERR_ABORTED" in request.failure:
            return
        if "fonts.g" in request.url:
            return
        if not self._may_be_missing(request.url):
            self.problems.append((area_of(request.url), f"a file could not be loaded: {request.url.split('/', 3)[-1]} ({request.failure})"))

    def open(self, hash_: str) -> None:
        self.page.goto("about:blank")  # a hash change alone would not reload the page: start clean
        self.page.goto(f"{self.base}/index.html#{hash_}")

    def frame(self, part: str):
        for _ in range(100):
            for frame in self.page.frames:
                if part in frame.url:
                    return frame
            self.page.wait_for_timeout(100)
        raise AssertionError(f"no frame for {part}")

    def shot(self, name: str) -> None:
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(self.shots / f"{name}.png"))

    def hplot_names(self) -> list[tuple[str, str]]:
        return self.page.evaluate("(document.getElementById('hplot').data || []).map(t => [t.name || '', (t.line && t.line.dash) || ''])")


def check_history(b: Browser) -> str:
    jarvis = b.site / "jarvis_by_commit.json"
    sel = ""
    if jarvis.is_file():  # tick the simulations Jarvis has numbers for, whichever design they are in
        designs = json.loads(jarvis.read_text(encoding="utf-8"))["designs"]
        file = next(iter(designs))
        sel = "&sel=" + ",".join(quote(f"{file}|{sim}", safe="") for sim in list(designs[file]["sims"])[:2])
    b.open("tab=history&metric=apogee&view=abs&units=metric" + sel)
    b.page.wait_for_selector("#hplot .main-svg", timeout=30000)
    for tab in TABS:
        assert b.page.is_visible(f"#tab-{tab}"), f"the {tab} tab is missing"
    count = len(b.hplot_names())
    assert count >= 1, "the chart has no lines"
    b.shot("history")
    return f"{count} lines"


def check_jarvis_lines(b: Browser) -> str:
    if not (b.site / "jarvis_by_commit.json").is_file():
        return "skipped: no jarvis_by_commit.json (it is made when the site is built on main)"
    b.page.wait_for_selector("#jarvis-toggle", timeout=20000)
    dashed = [n for n, dash in b.hplot_names() if n.endswith("(Jarvis)") and dash == "dash"]
    assert dashed, f"no dashed Jarvis line on the chart: {b.hplot_names()}"
    b.shot("history_jarvis")
    b.page.click("#jarvis-toggle")
    b.page.wait_for_function("!document.getElementById('hplot').data.some(t => (t.name || '').endsWith('(Jarvis)'))", timeout=10000)
    b.page.click("#jarvis-toggle")
    b.page.wait_for_function("document.getElementById('hplot').data.some(t => (t.name || '').endsWith('(Jarvis)'))", timeout=10000)
    return f"{len(dashed)} dashed, toggle works"


def check_predictions(b: Browser, require: bool) -> str:
    built = (b.site / "predictions" / "index.html").is_file()
    b.page.click("#tab-predictions")
    if not built:
        assert not require, "the Predictions page should have been built but is not there"
        b.page.wait_for_selector("#view-predictions .framemsg", timeout=10000)
        b.page.wait_for_function("!document.getElementById('bigerror').hidden", timeout=10000)
        return "skipped: not built; the page explains that, with the Error bar"
    frame = b.frame("/predictions/index.html")
    frame.wait_for_selector("#plot .main-svg", timeout=30000)
    frame.wait_for_function("document.querySelectorAll('#climb tr').length > 2", timeout=30000)
    rows = frame.evaluate("[...document.querySelectorAll('#climb tr')].map(r => [...r.children].map(c => c.innerText.trim()))")
    quick = next((r for r in rows if r[0].endswith("Jarvis")), None)
    other = next((r for r in rows if r[0].endswith("OpenRocket")), None)
    assert quick, f"no Jarvis row in the table: {rows}"
    apogee = numbers(quick[1])
    assert apogee and apogee > 100, f"Jarvis's apogee reads {quick[1]!r}"
    note = "no OpenRocket row to compare with"
    if other and numbers(other[1]):
        ratio = apogee / numbers(other[1])
        assert 0.5 < ratio < 2.0, f"Jarvis apogee {quick[1]} against OpenRocket {other[1]}: far apart"
        note = f"Jarvis {ratio - 1:+.1%} from OpenRocket"
    weather = frame.evaluate("document.querySelectorAll('#weather tr').length")
    assert weather >= 3, "the weather table has no rows"
    assert "Motor:" in frame.evaluate("document.getElementById('runinfo').innerText"), "the motor line is missing"
    b.shot("predictions")
    return f"{len(rows) - 1} table rows, {weather - 1} weather rows, {note}"


def check_units_reach_predictions(b: Browser) -> str:
    if not (b.site / "predictions" / "index.html").is_file():
        return "skipped: Predictions not built"
    frame = b.frame("/predictions/index.html")
    for units, mark in (("metric", "(m)"), ("imperial", "(ft)")):
        b.page.select_option("#units", units)
        frame.wait_for_function(f"document.querySelector('#climb th:nth-child(2)').innerText.includes('{mark}')", timeout=10000)
    return "metric and imperial both arrive"


def check_vision(b: Browser, require: bool) -> str:
    built = (b.site / "predictions" / "viewer" / "index.html").is_file()
    b.page.click("#tab-vision")
    if not built:
        assert not require, "Vision should have been built but is not there"
        b.page.wait_for_selector("#view-vision .framemsg", timeout=10000)
        b.page.wait_for_function("!document.getElementById('bigerror').hidden", timeout=10000)
        return "skipped: not built; the page explains that, with the Error bar"
    frame = b.frame("/predictions/viewer/index.html")
    frame.wait_for_selector("canvas", timeout=30000)
    frame.wait_for_function("window.__viewer !== undefined", timeout=30000)
    assert frame.evaluate("document.querySelector('input[data-k=pad]').checked"), "the launch pad should be on to start with"
    b.page.wait_for_timeout(1500)  # a few frames
    holder = b.page.query_selector("#view-vision iframe")
    picture = holder.screenshot()
    # a scene that is not drawn is one flat colour, and a flat picture squeezes to almost nothing
    assert len(picture) > 20000, f"the 3D scene looks blank ({len(picture)} bytes of picture)"
    b.shot("vision")
    return f"{len(picture) // 1000} kB picture"


def check_back_to_history(b: Browser) -> str:
    b.page.click("#tab-history")
    b.page.wait_for_selector("#hplot .main-svg", timeout=10000)
    assert "tab=history" in b.page.evaluate("location.hash"), "the address did not follow the tab"
    return ""


def check_no_problems(b: Browser, area: str) -> str:
    mine = [text for where, text in b.problems if where == area]
    assert not mine, "\n".join(dict.fromkeys(mine))
    return ""


def check_error_bar(b: Browser) -> str:
    """The big Error bar follows build_status.json: shown with what broke when there is a problem, hidden when not."""
    status = b.site / "build_status.json"
    problems = json.loads(status.read_text(encoding="utf-8")).get("problems", []) if status.is_file() else []
    b.open("tab=history&metric=apogee&view=abs")
    b.page.wait_for_selector("#hplot .main-svg", timeout=30000)
    if not problems:
        b.page.wait_for_timeout(500)  # the status file is asked for as the page opens
        assert b.page.evaluate("document.getElementById('bigerror').hidden"), "an Error bar is showing but nothing is wrong"
        return "no bar when all is well"
    b.page.wait_for_function("!document.getElementById('bigerror').hidden", timeout=10000)
    text = b.page.evaluate("document.getElementById('bigerror').innerText")
    assert "error" in text.lower() and problems[0]["message"][:40] in text, f"the Error bar does not say what broke: {text!r}"
    size = b.page.evaluate("parseFloat(getComputedStyle(document.querySelector('#bigerror .be-title')).fontSize)")
    assert size >= 24, f"the Error bar's heading is only {size}px"
    b.shot("error_bar")
    return f"{len(problems)} problem(s) shown at the top"


def run(site: Path, args: argparse.Namespace) -> int:
    from playwright.sync_api import sync_playwright

    require, shots = args.require_predictions, Path(args.shots) if args.shots else None
    server, port = serve(site)
    report = Report()
    print(f"browser test: {site}")
    with sync_playwright() as p:
        browser = p.chromium.launch(args=BROWSER_ARGS)
        page = browser.new_page(viewport={"width": 1400, "height": 900}, color_scheme="dark")
        if args.plotly:  # for a computer that cannot reach the internet: the same library from a file
            page.route("https://cdn.plot.ly/**", lambda r: r.fulfill(body=Path(args.plotly).read_bytes(), content_type="application/javascript"))
        if args.three:
            page.route("https://cdnjs.cloudflare.com/**", lambda r: r.fulfill(body=Path(args.three).read_bytes(), content_type="application/javascript"))
        b = Browser(page, f"http://127.0.0.1:{port}", site, shots)
        report.check("History tab draws its chart and has every tab", "history", lambda: check_history(b))
        report.check("dashed Jarvis lines, and the button that turns them off", "jarvis", lambda: check_jarvis_lines(b))
        report.check("Predictions tab opens inside the page with its tables and chart", "predictions", lambda: check_predictions(b, require))
        report.check("units chosen in the page reach the Predictions tab", "predictions", lambda: check_units_reach_predictions(b))
        report.check("Vision tab draws the 3D scene", "vision", lambda: check_vision(b, require))
        report.check("back on History, the address follows", "history", lambda: check_back_to_history(b))
        for area, what in (("history", "History"), ("predictions", "Predictions"), ("vision", "Vision")):
            report.check(f"no script errors and no missing files in {what}", area, lambda area=area: check_no_problems(b, area))
        if args.synthetic:  # a made-up failure, to see the Error bar
            (site / "build_status.json").write_text(json.dumps({"ok": False, "commit": "abcdef0", "run_url": "https://example.test/run", "problems": [
                {"area": "vision", "message": "the 3D scene looks blank", "shown": "last_good", "since": "2026-10-01 12:00 UTC"}]}), encoding="utf-8")
        report.check("the Error bar at the top says what broke", "history", lambda: check_error_bar(b))
        browser.close()
    server.shutdown()
    if args.report:
        report.write(Path(args.report))
    print(f"{len(report.failed)} failed, {len(report.skipped)} skipped")
    return 1 if report.failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", help="the built site folder (the one that gets published)")
    parser.add_argument("--synthetic", action="store_true", help="build a made-up History site and test that")
    parser.add_argument("--require-predictions", action="store_true", help="fail when the Predictions page or Vision is missing")
    parser.add_argument("--shots", help="save a picture of each tab in this folder")
    parser.add_argument("--report", help="write what failed, by area, to this JSON file (for site_status.py)")
    parser.add_argument("--plotly", help="a local copy of Plotly, for a computer with no internet")
    parser.add_argument("--three", help="a local copy of three.js, for a computer with no internet")
    args = parser.parse_args(argv)
    if bool(args.site) == bool(args.synthetic):
        parser.error("give --site or --synthetic")
    if args.synthetic:
        with tempfile.TemporaryDirectory(prefix="browser_test_") as folder:
            site = Path(folder) / "site"
            make_synthetic(site)
            return run(site, args)
    return run(Path(args.site), args)


if __name__ == "__main__":
    sys.exit(main())
