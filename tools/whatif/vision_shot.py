"""Take a picture of the whole flight in VISION, for the JARVIS message.

    python tools/whatif/vision_shot.py --site site --out site/predictions/vision.png

Serves the built site, opens VISION (site/predictions/viewer/index.html, the default condition) in
Chromium, presses "Whole flight", moves the time slider to the end so the whole path is drawn, and
saves the 3D picture. Needs playwright with Chromium installed (the workflow installs it for the
browser check). Writes nothing and exits 0 if there is no VISION page; exits 1 if the picture fails.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import sys
import threading
from pathlib import Path


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args) -> None:  # noqa: A002
        pass



def launch_browser(chromium, args):
    """The Chrome already installed on the computer (GitHub's runners have one, so nothing has to be downloaded or
    installed), or Playwright's own Chromium when there is none. SRT_BROWSER=bundled always uses Playwright's."""
    if os.environ.get("SRT_BROWSER", "") != "bundled":
        try:
            return chromium.launch(channel="chrome", args=args)
        except Exception:  # noqa: BLE001  (no Chrome here, or one Playwright cannot drive)
            pass
    return chromium.launch(args=args)

def serve(folder: Path) -> tuple[http.server.ThreadingHTTPServer, int]:
    handler = functools.partial(_Quiet, directory=str(folder))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def shoot(site: Path, out: Path, width: int, height: int) -> int:
    viewer = site / "predictions" / "viewer" / "index.html"
    if not viewer.is_file():
        print(f"No VISION page at {viewer}; no picture.", flush=True)
        return 0
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    server, port = serve(site)
    errors: list[str] = []
    try:
        with sync_playwright() as p:
            browser = launch_browser(p.chromium, ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=2)
            local_three = os.environ.get("THREE_JS_PATH")  # only for trying this without internet
            if local_three:
                page.route("**/three.min.js", lambda r: r.fulfill(path=local_three, content_type="text/javascript"))
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/predictions/viewer/index.html", wait_until="load")
            page.wait_for_selector("#stage canvas", timeout=30000)
            page.click('#views button[data-v="wide"]')
            # just the flight: no world axes, body axes or forces, and no scale note
            page.evaluate("""() => {
                for (const k of ['world', 'body', 'aero']) {
                    const box = document.querySelector('input[data-k="' + k + '"]');
                    if (box && box.checked) box.click();
                }
                const note = document.getElementById('scaleNote');
                if (note) note.style.visibility = 'hidden';
            }""")
            # the whole path is drawn up to the slider's time: put it at the end
            page.eval_on_selector("#scrub", "s => { s.value = s.max; s.dispatchEvent(new Event('input')); }")
            page.wait_for_timeout(4000)  # the camera eases out to the whole flight
            out.parent.mkdir(parents=True, exist_ok=True)
            page.locator("#stage").screenshot(path=str(out))
            browser.close()
    finally:
        server.shutdown()
    if errors:
        out.unlink(missing_ok=True)  # a picture of a broken scene must not go out as if it were fine
        print("VISION had script errors: " + "; ".join(errors), flush=True)
        return 1
    print(f"VISION picture: {out}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--site", type=Path, default=Path("site"))
    p.add_argument("--out", type=Path, default=Path("site/predictions/vision.png"))
    p.add_argument("--width", type=int, default=1100)
    p.add_argument("--height", type=int, default=650)
    a = p.parse_args(argv)
    return shoot(a.site, a.out, a.width, a.height)


if __name__ == "__main__":
    sys.exit(main())
