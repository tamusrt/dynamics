"""Checks of the last-good fallback. Run: python tools/whatif/tests/test_site_status.py"""

import contextlib
import functools
import http.server
import json
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import site_status

CONFIG = {"default": "SRT14", "rockets": {"SRT14": {}, "Other": {}}}
GOOD = "<html><body><h1>last good page {name}</h1></body></html>"


@contextlib.contextmanager
def live_site(files: dict[str, str]):
    """A web server standing in for the site that is live now; yields its address."""
    with tempfile.TemporaryDirectory() as tmp:
        for rel, text in files.items():
            (Path(tmp) / rel).parent.mkdir(parents=True, exist_ok=True)
            (Path(tmp) / rel).write_text(text, encoding="utf-8")

        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *_args):
                pass

        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=tmp))
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()


def _all_live() -> dict[str, str]:
    names = ("predictions/index.html", "predictions/viewer/index.html", "predictions/other/index.html", "predictions/other/viewer/index.html")
    return {n: GOOD.format(name=n) for n in names}


def _new_site(root: Path) -> Path:
    site = root / "site"
    for rel in ("predictions/index.html", "predictions/viewer/index.html", "predictions/other/index.html", "predictions/other/viewer/index.html"):
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_text("<html><body>NEW BROKEN</body></html>", encoding="utf-8")
    return site


def _run(root: Path, site: Path, live: str, *, flightsim="success", predictions="success", failed=None, config=CONFIG, browser="success", report_file=True, edith=False) -> tuple[int, dict]:
    report = root / "report.json"
    report.unlink(missing_ok=True)
    if report_file:
        report.write_text(json.dumps({"failed": failed or [], "skipped": []}))
    cfg = root / "whatif_config.json"
    cfg.write_text(json.dumps(config))
    args = ["--site", str(site), "--config", str(cfg), "--live-url", live, "--flightsim", flightsim, "--predictions", predictions,
            "--browser", browser, "--report", str(report), "--commit", "abcdef1234", "--run-url", "https://example.test/run/9", "--summary", str(root / "summary.md")]
    if edith:
        args.append("--edith")
    code = site_status.main(args)
    return code, json.loads((site / "build_status.json").read_text())


def test_page_places_are_per_rocket_with_the_default_at_the_root():
    assert site_status.page_files(CONFIG, "predictions") == ["predictions/index.html", "predictions/other/index.html"]
    assert site_status.page_files(CONFIG, "vision") == ["predictions/viewer/index.html", "predictions/other/viewer/index.html"]


def test_the_edith_page_is_per_rocket_too():
    assert site_status.page_files(CONFIG, "edith") == ["predictions/edith/index.html", "predictions/other/edith/index.html"]


def _edith_names():
    return ("predictions/edith/index.html", "predictions/other/edith/index.html")


def _with_edith(site: Path) -> Path:
    for rel in _edith_names():
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_text("<html><body>NEW EDITH</body></html>", encoding="utf-8")
    return site


def test_a_clean_build_with_edith_keeps_the_new_edith_page():
    with tempfile.TemporaryDirectory() as tmp, live_site({**_all_live(), **{n: GOOD.format(name=n) for n in _edith_names()}}) as live:
        root = Path(tmp)
        site = _with_edith(_new_site(root))
        code, status = _run(root, site, live, edith=True)
        assert code == 0 and status["ok"] is True
        assert "NEW EDITH" in (site / "predictions/edith/index.html").read_text()


def test_an_edith_page_that_was_not_made_is_replaced_by_the_last_good_one():
    live_files = {**_all_live(), **{n: GOOD.format(name=n) for n in _edith_names()}}
    with tempfile.TemporaryDirectory() as tmp, live_site(live_files) as live:
        root = Path(tmp)
        site = _new_site(root)  # the JARVIS pages are fine, EDITH made nothing
        code, status = _run(root, site, live, edith=True)
        assert code == 0, "an EDITH problem does not stop the site from being published"
        assert [p["area"] for p in status["problems"]] == ["edith"] and status["problems"][0]["shown"] == "last_good"
        page = (site / "predictions/edith/index.html").read_text()
        assert "last good page predictions/edith/index.html" in page and "EDITH: " in page and 'id="srt-error"' in page
        assert "NEW BROKEN" in (site / "predictions/index.html").read_text(), "the JARVIS page is left alone"
        assert "EDITH" in (root / "summary.md").read_text()


def test_without_the_edith_flag_nothing_about_edith_is_checked_or_put_back():
    live_files = {**_all_live(), **{n: GOOD.format(name=n) for n in _edith_names()}}
    with tempfile.TemporaryDirectory() as tmp, live_site(live_files) as live:
        root = Path(tmp)
        site = _new_site(root)
        code, status = _run(root, site, live)
        assert code == 0 and status["ok"] is True
        assert not (site / "predictions/edith").exists(), "an old EDITH page is not brought back"
        failed = [{"name": "EDITH page", "area": "edith", "message": "x"}]
        _, status = _run(root, site, live, failed=failed)
        assert not (site / "predictions/edith").exists()


def test_a_broken_edith_page_found_by_the_browser_test_is_replaced():
    live_files = {**_all_live(), **{n: GOOD.format(name=n) for n in _edith_names()}}
    with tempfile.TemporaryDirectory() as tmp, live_site(live_files) as live:
        root = Path(tmp)
        site = _with_edith(_new_site(root))
        failed = [{"name": "EDITH page shows its chances", "area": "edith", "message": "no apogee chart"}]
        code, status = _run(root, site, live, failed=failed, edith=True)
        assert code == 0 and [p["area"] for p in status["problems"]] == ["edith"]
        assert "no apogee chart" in (site / "predictions/edith/index.html").read_text()


def test_a_failed_build_also_puts_back_edith_when_it_was_expected():
    live_files = {**_all_live(), **{n: GOOD.format(name=n) for n in _edith_names()}}
    with tempfile.TemporaryDirectory() as tmp, live_site(live_files) as live:
        root = Path(tmp)
        site = root / "site"
        site.mkdir()
        _, status = _run(root, site, live, predictions="failure", edith=True)
        assert [p["area"] for p in status["problems"]] == ["build"], "one problem, not one per page"
        for rel in (*_all_live(), *_edith_names()):
            assert f"last good page {rel}" in (site / rel).read_text(), rel


def test_a_clean_build_changes_nothing_and_says_ok():
    with tempfile.TemporaryDirectory() as tmp, live_site(_all_live()) as live:
        root = Path(tmp)
        site = _new_site(root)
        code, status = _run(root, site, live)
        assert code == 0 and status["ok"] is True and status["problems"] == []
        assert "NEW BROKEN" in (site / "predictions/index.html").read_text(), "a good build is left alone"
        assert not (root / "summary.md").exists(), "no error section in the summary when nothing is wrong"


def test_a_broken_vision_is_replaced_by_the_last_good_one_with_an_error_bar():
    with tempfile.TemporaryDirectory() as tmp, live_site(_all_live()) as live:
        root = Path(tmp)
        site = _new_site(root)
        failed = [{"name": "Vision tab draws the 3D scene", "area": "vision", "message": "the 3D scene looks blank"}]
        code, status = _run(root, site, live, failed=failed)
        assert code == 0, "Predictions and Vision problems do not stop the site from being published"
        page = (site / "predictions/viewer/index.html").read_text()
        assert "last good page predictions/viewer/index.html" in page and "NEW BROKEN" not in page
        assert 'id="srt-error"' in page and "the 3D scene looks blank" in page and "last good version, from" in page
        assert "NEW BROKEN" in (site / "predictions/index.html").read_text(), "the Predictions page was fine, so it is kept"
        assert status["ok"] is False and len(status["problems"]) == 1
        problem = status["problems"][0]
        assert problem["area"] == "vision" and problem["shown"] == "last_good" and problem["since"].endswith("UTC")
        assert status["commit"] == "abcdef1234" and status["run_url"] == "https://example.test/run/9"
        assert "ERROR" in (root / "summary.md").read_text() and "3D scene looks blank" in (root / "summary.md").read_text()


def test_a_failed_build_puts_back_both_pages_of_every_rocket():
    with tempfile.TemporaryDirectory() as tmp, live_site(_all_live()) as live:
        root = Path(tmp)
        site = root / "site"
        site.mkdir()  # nothing was built
        code, status = _run(root, site, live, predictions="failure")
        assert code == 0
        for rel in _all_live():
            assert f"last good page {rel}" in (site / rel).read_text(), rel
        assert [p["area"] for p in status["problems"]] == ["build"] and status["problems"][0]["shown"] == "last_good"


def test_a_flight_sim_that_could_not_be_checked_out_is_said_so():
    with tempfile.TemporaryDirectory() as tmp, live_site(_all_live()) as live:
        root = Path(tmp)
        site = root / "site"
        site.mkdir()
        _, status = _run(root, site, live, flightsim="failure", predictions="skipped")
        assert len(status["problems"]) == 1 and "FLIGHT_SIM_REF" in status["problems"][0]["message"]
        failed = [{"name": "Vision", "area": "vision", "message": "x"}]
        _, status = _run(root, site, live, predictions="failure", failed=failed)
        assert [p["area"] for p in status["problems"]] == ["build"], "the failed build already explains the pages"


def test_with_no_earlier_page_the_new_one_is_kept_and_the_bar_says_so():
    with tempfile.TemporaryDirectory() as tmp, live_site({}) as live:
        root = Path(tmp)
        site = _new_site(root)
        failed = [{"name": "Predictions tab opens", "area": "predictions", "message": "no table"}]
        code, status = _run(root, site, live, failed=failed)
        assert code == 0 and status["problems"][0]["shown"] == "as_built"
        assert "NEW BROKEN" in (site / "predictions/index.html").read_text(), "kept as built"
        empty = root / "empty"
        empty.mkdir()
        _, status = _run(root, empty, live, predictions="failure")
        assert status["problems"][0]["shown"] == "none"


def test_a_live_site_that_cannot_be_reached_is_the_same_as_no_earlier_page():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        site = root / "site"
        site.mkdir()
        _, status = _run(root, site, "http://127.0.0.1:9", predictions="failure")
        assert status["problems"][0]["shown"] == "none"
        _, status = _run(root, site, "", predictions="failure")
        assert status["problems"][0]["shown"] == "none"


def test_a_history_problem_blocks_publishing_and_the_dashed_lines_only_go_in_the_bar():
    with tempfile.TemporaryDirectory() as tmp, live_site(_all_live()) as live:
        root = Path(tmp)
        site = _new_site(root)
        code, status = _run(root, site, live, failed=[{"name": "History tab draws its chart", "area": "history", "message": "no chart"}])
        assert code == 1 and status["problems"][0]["area"] == "history"
        assert "NEW BROKEN" in (site / "predictions/index.html").read_text(), "a History problem is not fixed by old pages"
        code, status = _run(root, site, live, failed=[{"name": "dashed lines", "area": "jarvis", "message": "none drawn"}])
        assert code == 0 and status["problems"][0]["area"] == "jarvis" and "shown" not in status["problems"][0]


def test_a_browser_check_that_could_not_run_blocks_publishing():
    with tempfile.TemporaryDirectory() as tmp, live_site(_all_live()) as live:
        root = Path(tmp)
        site = _new_site(root)
        code, status = _run(root, site, live, browser="failure", report_file=False)
        assert code == 1 and status["problems"][0]["area"] == "history" and "could not run" in status["problems"][0]["message"]
        code, status = _run(root, site, live, browser="failure", report_file=True, failed=[{"name": "Vision", "area": "vision", "message": "x"}])
        assert code == 0 and status["problems"][0]["area"] == "vision", "a browser check that reported is believed"


def test_a_page_put_back_twice_keeps_one_bar_and_the_original_date():
    with tempfile.TemporaryDirectory() as tmp, live_site(_all_live()) as live:
        root = Path(tmp)
        site = _new_site(root)
        failed = [{"name": "Vision", "area": "vision", "message": "first problem"}]
        _run(root, site, live, failed=failed)
        first = (site / "predictions/viewer/index.html").read_text()
        since = site_status.SINCE.search(first).group(1)
        # the live site now serves that page with its bar; the next failure fetches it again
        with live_site({**_all_live(), "predictions/viewer/index.html": first.replace(since, "2020-01-02 03:04 UTC")}) as live2:
            site2 = _new_site(root / "again")
            _run(root, site2, live2, failed=[{"name": "Vision", "area": "vision", "message": "second problem"}])
        second = (site2 / "predictions/viewer/index.html").read_text()
        assert second.count('id="srt-error"') == 1 and "second problem" in second and "first problem" not in second
        assert "2020-01-02 03:04 UTC" in second, "the date the page was last good is kept"


def test_something_that_is_not_a_page_is_not_put_back():
    with tempfile.TemporaryDirectory() as tmp, live_site({**_all_live(), "predictions/viewer/index.html": "404 not a page"}) as live:
        root = Path(tmp)
        site = _new_site(root)
        one = {"default": "SRT14", "rockets": {"SRT14": {}}}
        _, status = _run(root, site, live, failed=[{"name": "Vision", "area": "vision", "message": "x"}], config=one)
        assert status["problems"][0]["shown"] == "as_built"
        assert "NEW BROKEN" in (site / "predictions/viewer/index.html").read_text()


def test_the_error_bar_text_is_escaped():
    page = site_status.with_banner("<html><body class=x>hello</body></html>", "<script>alert(1)</script>", "now")
    assert "<script>alert(1)</script>" not in page.split("<script>(function")[0] and "&lt;script&gt;" in page
    assert page.index('id="srt-error"') > page.index("<body class=x>") and page.endswith("hello</body></html>")
    assert site_status.with_banner("no body tag", "t", "s").endswith("no body tag")


def test_the_failed_build_s_own_message_is_in_the_problem():
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "build_log.txt"
        log.write_text("Collecting x\nTraceback (most recent call last):\n  File \"a.py\", line 3\n"
                       "Rocket 'A': the motor_dir /r/Thrust Curves is not a folder.\n", encoding="utf-8")
        said = site_status.build_detail(str(log))
        assert said == "Rocket 'A': the motor_dir /r/Thrust Curves is not a folder.", said
        message = site_status.problems_found("success", "failure", None, "success", False, said)[0]["message"]
        assert "not a folder" in message and "Build the predictions page (main only)" in message
        assert site_status.build_detail(str(Path(tmp) / "nothing.txt")) == ""


def test_the_whole_predictions_tree_is_put_back_from_the_live_site():
    import functools
    import http.server
    import threading

    with tempfile.TemporaryDirectory() as tmp:
        live, site = Path(tmp) / "live", Path(tmp) / "site"
        (live / "predictions" / "viewer").mkdir(parents=True)
        (live / "predictions" / "index.html").write_text("<html><body>good page</body></html>")
        (live / "predictions" / "viewer" / "index.html").write_text("<html><body>good viewer</body></html>")
        (live / "predictions" / "data.bin").write_bytes(b"\x00\x01good")
        site_status.write_manifest(live)
        (site / "predictions").mkdir(parents=True)
        (site / "predictions" / "index.html").write_text("<html><body>broken</body></html>")
        (site / "predictions" / "stray.json").write_text("{}")
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(live))
        handler.log_message = lambda *a, **k: None
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            config = {"default": "R", "rockets": {"R": {}}}
            url = f"http://127.0.0.1:{server.server_address[1]}"
            shown = site_status.restore_tree(site, config, url, {"predictions": "it broke"})
            assert shown and shown["predictions"][0] == "last_good"
            assert "good page" in (site / "predictions" / "index.html").read_text() and "it broke" in (site / "predictions" / "index.html").read_text()
            assert (site / "predictions" / "data.bin").read_bytes() == b"\x00\x01good" and not (site / "predictions" / "stray.json").exists()
            assert site_status.restore_tree(site, config, "http://127.0.0.1:1", {"predictions": "x"}) is None
        finally:
            server.shutdown()


def test_the_real_error_is_quoted_not_the_exit_status_line():
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "build_log.txt"
        log.write_text(
            "building\nrocket: these files are missing: /r/a_aero.csv (git has no record of it moving).\n"
            "Traceback (most recent call last):\n  File \"build_site.py\", line 340, in main\n"
            "    subprocess.run(build[\"cmd\"], check=True)\n  File \"subprocess.py\", line 577, in run\n"
            "    raise CalledProcessError(retcode, process.args)\n"
            "subprocess.CalledProcessError: Command '['python', '-m', 'x']' returned non-zero exit status 1.\n", encoding="utf-8")
        said = site_status.build_detail(str(log))
        assert said.startswith("rocket: these files are missing"), said
        log.write_text("Traceback (most recent call last):\n  File \"a.py\", line 1\n    boom()\nValueError: bad motor\n", encoding="utf-8")
        assert site_status.build_detail(str(log)) == "ValueError: bad motor"


def _serve(live: Path):
    import functools
    import http.server
    import threading

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(live))
    handler.log_message = lambda *a, **k: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_when_only_edith_broke_the_new_predictions_and_vision_stay():
    with tempfile.TemporaryDirectory() as tmp:
        live, site = Path(tmp) / "live", Path(tmp) / "site"
        (live / "predictions" / "edith").mkdir(parents=True)
        (live / "predictions" / "viewer").mkdir(parents=True)
        (live / "predictions" / "index.html").write_text("<html><body>old page</body></html>")
        (live / "predictions" / "viewer" / "index.html").write_text("<html><body>old viewer</body></html>")
        (live / "predictions" / "edith" / "index.html").write_text("<html><body>old edith</body></html>")
        (live / "predictions" / "edith" / "my data.json").write_text('{"old": 1}')  # a name that needs quoting in an address
        site_status.write_manifest(live)
        (site / "predictions" / "viewer").mkdir(parents=True)
        (site / "predictions" / "edith").mkdir(parents=True)
        (site / "predictions" / "index.html").write_text("<html><body>NEW page</body></html>")
        (site / "predictions" / "viewer" / "index.html").write_text("<html><body>NEW viewer</body></html>")
        (site / "predictions" / "edith" / "half.json").write_text("{}")  # what a half-made EDITH left
        server, url = _serve(live)
        try:
            config = {"default": "R", "rockets": {"R": {}}}
            shown = site_status.restore_tree(site, config, url, {"edith": "it broke"}, site_status.in_edith_folder)
            assert shown and shown["edith"][0] == "last_good"
            assert "NEW page" in (site / "predictions" / "index.html").read_text()
            assert "NEW viewer" in (site / "predictions" / "viewer" / "index.html").read_text()
            assert "old edith" in (site / "predictions" / "edith" / "index.html").read_text()
            assert (site / "predictions" / "edith" / "my data.json").read_text() == '{"old": 1}'
            assert not (site / "predictions" / "edith" / "half.json").exists()
        finally:
            server.shutdown()


def test_the_manifest_can_be_written_on_its_own_and_lists_vision_png():
    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "site"
        (site / "predictions").mkdir(parents=True)
        (site / "predictions" / "vision.png").write_bytes(b"png")
        assert site_status.main(["--site", str(site), "--manifest-only"]) == 0
        assert "vision.png" in json.loads((site / "predictions_manifest.json").read_text())["files"]


def test_the_jarvis_note_does_not_report_a_rolled_back_page_as_new():
    import jarvis_note

    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "site"
        (site / "predictions").mkdir(parents=True)
        (site / "build_status.json").write_text(json.dumps({"ok": False, "problems": [
            {"area": "vision", "message": "Vision did not load", "shown": "last_good"}]}))
        out = Path(tmp) / "note.md"
        old = sys.argv
        sys.argv = ["jarvis_note.py", "--site", str(site), "--live-url", "https://x.test/d", "--out", str(out)]
        try:
            jarvis_note.main()
        finally:
            sys.argv = old
        text = out.read_text(encoding="utf-8")
        assert "last good" in text and "Vision did not load" in text and "No new apogees" in text
        assert json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))["warnings"] == 1
        (site / "build_status.json").write_text(json.dumps({"ok": False, "problems": [
            {"area": "edith", "message": "EDITH did not finish", "shown": "last_good"}]}))
        assert jarvis_note.rolled_back(site) is None, "an EDITH problem does not roll the JARVIS page back"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
