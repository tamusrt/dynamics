"""After the site is built and clicked through: keep the last good Predictions, Vision and EDITH when a push breaks them.

    python tools/whatif/site_status.py --site site --config aero_modeling/whatif_config.json \\
        --live-url https://tamusrt.github.io/dynamics --flightsim success --predictions failure \\
        --report browser_report.json --commit <sha> --run-url <url> [--edith]

It looks at how the build went (the outcome of the flight_sim checkout and of the Predictions build) and at
the report of ``tools/whatif/tests/browser_test.py``, and decides what is wrong. For each problem with
Predictions or Vision it then:

* downloads the page that is live now (the last good one), and puts it in the site in place of the new one,
  with a large red Error bar added at the top of the page (it hides itself when the page is inside the History
  page, which shows its own bar); if there is no earlier page, the new one is kept as built;
* writes ``build_status.json`` into the site. The History page reads it and shows the big Error bar at the top
  of every tab, saying what broke and what is shown instead.

EDITH (the Monte Carlo simulation, its own page under ``predictions/edith/``) is handled the same way, but only
when ``--edith`` says this build was meant to make it. Without the flag nothing about EDITH is checked or put back,
so a site that does not run EDITH never shows an old EDITH page.

A problem with the History tab itself or with the build is not covered: there the exit code is 1, so the
workflow does not publish, and the site that is live now stays as it is. Problems with the dashed Jarvis lines
only go in the bar. Exit code 0 otherwise.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

PAGES = ("predictions", "vision")  # what can be put back from the last good version
EDITH = "edith"  # also put back, but only when the build was meant to make it (--edith)
BLOCKING = ("history",)  # a problem here keeps the site from being published
MARK = re.compile(r"<!--srt-error-start-->.*?<!--srt-error-end-->", re.DOTALL)
SINCE = re.compile(r"<!--srt-good-since:([^>]*?)-->")
BUILD_FAILED = {
    "failure": "The Predictions build failed, so the new Predictions page and Vision could not be made "
    "(see the step \"Build the predictions page\" in the run).",
    "skipped": "The Predictions build did not run, so the new Predictions page and Vision could not be made.",
}
EDITH_MISSING = (
    "EDITH (the Monte Carlo simulation) did not finish, so its page is missing or out of date, and the "
    "flights in Vision may be missing too "
    "(see the step \"Build the predictions page\" in the run)."
)
FLIGHTSIM_FAILED = (
    "The flight_sim code could not be checked out, so the new Predictions page and Vision could not be made "
    "(check FLIGHT_SIM_REF and FLIGHT_SIM_TOKEN in the repository settings)."
)


def page_files(config: dict, area: str) -> list[str]:
    """Where the area's page is in the site, for every rocket (the default rocket is at predictions/)."""
    default = config.get("default") or next(iter(config["rockets"]))
    files = []
    for key in config["rockets"]:
        folder = "predictions" if key == default else f"predictions/{key.lower()}"
        leaf = {"predictions": "index.html", "vision": "viewer/index.html", EDITH: "edith/index.html"}[area]
        files.append(f"{folder}/{leaf}")
    return files


def problems_found(
    flightsim: str, predictions: str, report: dict | None, browser: str = "success", edith_missing: bool = False
) -> list[dict]:
    """What is wrong, as [{area, message}], from the build's outcomes and the browser test's report.

    ``edith_missing``: EDITH was meant to be built and its page is not in the site.
    """
    found: list[dict] = []
    if browser == "failure" and report is None:  # it did not get as far as reporting: the site is unchecked
        found.append({"area": "history", "message": "The browser check could not run, so the site could not be checked."})
    if flightsim == "failure":
        found.append({"area": "build", "message": FLIGHTSIM_FAILED})
    elif predictions in BUILD_FAILED:
        found.append({"area": "build", "message": BUILD_FAILED[predictions]})
    for failure in (report or {}).get("failed", []):
        area = failure.get("area", "history")
        if area in (*PAGES, EDITH) and any(p["area"] == "build" for p in found):
            continue  # the build failing already says it
        found.append({"area": area, "message": failure.get("message", "").strip() or f"the check \"{failure['name']}\" failed"})
    if edith_missing and not any(p["area"] in ("build", EDITH) for p in found):
        found.append({"area": EDITH, "message": EDITH_MISSING})
    return found


def sentence(text: str) -> str:
    """The text as a sentence with one full stop at the end."""
    return text.strip().rstrip(".").strip() + "."


def fetch(url: str) -> tuple[bytes, str | None] | None:
    """The file and its Last-Modified date from the live site, or None if it cannot be had."""
    try:
        with urllib.request.urlopen(url, timeout=60) as reply:
            body = reply.read()
            stamp = reply.headers.get("Last-Modified")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not body or b"<html" not in body[:4000].lower():
        return None
    return body, stamp


def when(text: str | None) -> str:
    """A Last-Modified header as '2026-10-02 22:41 UTC' (the current time if it cannot be read)."""
    try:
        moment = parsedate_to_datetime(text) if text else datetime.now(timezone.utc)
    except (TypeError, ValueError):
        moment = datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def banner(text: str, since: str) -> str:
    """The large Error bar for a page that is shown on its own; it removes itself inside the History page."""
    style = (
        "background:#b62324;color:#fff;padding:14px 20px 16px;border-bottom:4px solid #f85149;"
        "font:700 20px/1.4 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
    )
    return (
        "<!--srt-error-start-->"
        f'<div id="srt-error" role="alert" style="{style}">'
        '<span style="font-size:30px;letter-spacing:.03em;margin-right:14px;text-transform:uppercase">Error</span>'
        f"{html.escape(text)}</div>"
        "<script>(function(){var e=document.getElementById('srt-error');"
        "try{if(window.top!==window.self)e.remove();}catch(x){e.remove();}})();</script>"
        f"<!--srt-good-since:{html.escape(since)}-->"
        "<!--srt-error-end-->"
    )


def with_banner(page: str, text: str, since: str) -> str:
    """The page with any earlier Error bar taken out and this one put right after <body>."""
    page = MARK.sub("", page)
    tag = re.search(r"<body[^>]*>", page, re.IGNORECASE)
    block = banner(text, since)
    if not tag:
        return block + page
    return page[: tag.end()] + block + page[tag.end() :]


def restore(site: Path, config: dict, live_url: str, area: str, message: str) -> tuple[str, str]:
    """Put the live page(s) of an area in the site. Returns (what is shown, since): last_good / as_built / none."""
    files = page_files(config, area)
    got: dict[str, tuple[str, str]] = {}
    for rel in files:
        reply = fetch(f"{live_url.rstrip('/')}/{rel}") if live_url else None
        if reply is None:
            continue
        text = reply[0].decode("utf-8", "replace")
        old = SINCE.search(text)  # a page kept before already knows when it was last good
        got[rel] = (text, html.unescape(old.group(1)) if old else when(reply[1]))
    for rel, (text, since) in got.items():
        target = site / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        name = {"vision": "Vision", EDITH: "EDITH"}.get(area, "Predictions")
        note = f"{name}: {sentence(message)} This is the last good version, from {since}."
        target.write_text(with_banner(text, note, since), encoding="utf-8")
    if got:
        return "last_good", min(since for _, since in got.values())
    return ("as_built" if any((site / rel).is_file() for rel in files) else "none"), ""


def write_report(site: Path, status: dict, summary: Path | None) -> None:
    (site / "build_status.json").write_text(json.dumps(status, indent=1), encoding="utf-8")
    labels = {"predictions": "Predictions", "vision": "Vision", "edith": "EDITH", "build": "Predictions and Vision",
              "history": "History", "jarvis": "Jarvis lines"}
    shown = {
        "last_good": "showing the last good version",
        "as_built": "no earlier version, showing the new one as built",
        "none": "nothing to show",
    }
    for p in status["problems"]:
        extra = f" ({shown[p['shown']]})" if p.get("shown") else ""
        print(f"::error title={labels.get(p['area'], p['area'])}::{p['message']}{extra}")
    if summary is not None and status["problems"]:
        lines = ["## ERROR: this push broke something", ""]
        lines += [f"- **{labels.get(p['area'], p['area'])}**: {p['message']}" + (f" _{shown[p['shown']]}_" if p.get("shown") else "") for p in status["problems"]]
        with summary.open("a", encoding="utf-8") as out:
            out.write("\n".join(lines) + "\n\n")


def run(args: argparse.Namespace) -> int:
    site = Path(args.site)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = None
    if args.report and Path(args.report).is_file():
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    pages = (*PAGES, EDITH) if args.edith else PAGES  # what this build was meant to make
    missing = args.edith and not all((site / rel).is_file() for rel in page_files(config, EDITH))
    found = problems_found(args.flightsim, args.predictions, report, args.browser, missing)
    found = [p for p in found if p["area"] != EDITH or args.edith]  # no EDITH problems when it was not asked for
    areas = {a for p in found for a in (pages if p["area"] == "build" else (p["area"],)) if a in pages}
    shown: dict[str, tuple[str, str]] = {}
    for area in sorted(areas):
        message = next((p["message"] for p in found if p["area"] in (area, "build")), "")
        shown[area] = restore(site, config, args.live_url, area, message)
    problems = []
    for p in found:
        entry = dict(p)
        if p["area"] == "build" or p["area"] in pages:
            kinds = [shown[a] for a in (pages if p["area"] == "build" else (p["area"],)) if a in shown]
            entry["shown"] = "last_good" if kinds and all(k[0] == "last_good" for k in kinds) else (kinds[0][0] if kinds else "none")
            entry["since"] = min((k[1] for k in kinds if k[1]), default="")
        problems.append(entry)
    status = {
        "ok": not problems,
        "commit": args.commit or "",
        "run_url": args.run_url or "",
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "problems": problems,
    }
    write_report(site, status, Path(args.summary) if args.summary else None)
    print(f"site status: {'ok' if status['ok'] else str(len(problems)) + ' problem(s)'}")
    return 1 if any(p["area"] in BLOCKING for p in problems) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", required=True)
    parser.add_argument("--config", required=True, help="aero_modeling/whatif_config.json (for the rockets' places)")
    parser.add_argument("--live-url", default="", help="where the site is live now, to take the last good pages from")
    parser.add_argument("--flightsim", default="success", help="outcome of the flight_sim checkout")
    parser.add_argument("--predictions", default="success", help="outcome of the Predictions build")
    parser.add_argument("--browser", default="success", help="outcome of the browser test step")
    parser.add_argument("--edith", action="store_true", help="this build was meant to make the EDITH page too")
    parser.add_argument("--report", default="", help="the browser test's --report file")
    parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--run-url", default="")
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""))
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
