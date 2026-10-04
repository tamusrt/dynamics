"""Checks of the Update CSV helper with a fake runner (no RASAero, no flight_sim).

Run: python tools/whatif/tests/test_local_helper.py
"""

import http.client
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import local_helper  # noqa: E402

ORIGIN = "https://tamusrt.github.io"


class Rig:
    """A temporary repo layout, a helper on a free port, and a runner that fakes the three commands."""

    def __init__(self, build_fails=False, convert_fails=False, hold=None, sweep_writes=31, publish=False, push_fails=False, with_ork=False, corrects=True):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        aero = self.repo / "aero_modeling"
        (aero / "R" / "RASA").mkdir(parents=True)
        (aero / "R" / "RASA" / "a.csv").write_text("old csv")
        (aero / "R" / "RASA" / "r.CDX1").write_text("cdx")
        if with_ork:  # a design file, so the .CDX1 is checked against it before the sweep
            (aero / "R" / "a.ork").write_text("ork")
        self.corrects = corrects
        config = aero / "whatif_config.json"
        config.write_text(json.dumps({
            "default": "R",
            "rockets": {"R": {"name": "Rocket R", "ork": "R/a.ork", "aero": "R/RASA/a.csv", "motor": "R/a.eng",
                              "rasaero": "R/RASA/r.CDX1", "alpha_dir": "R/RASA/alpha"}},
        }))
        self.calls = []
        self.opened = []
        self.build_fails, self.convert_fails, self.hold, self.sweep_writes = build_fails, convert_fails, hold, sweep_writes
        self.push_fails, self.stopped = push_fails, threading.Event()
        ctx = local_helper.load_context(config, self.repo, "python")
        self.helper = local_helper.Helper(ctx, runner=self.run, probe=lambda module: True, opener=self.opened.append,
                                          auto_publish=publish, on_published=self.stopped.set)
        self.server = local_helper.serve(self.helper, 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def run(self, cmd, on_line, cancel):
        text = [str(c) for c in cmd]
        self.calls.append(text)
        script = " ".join(text)
        if "rasaero_sweep.py" in script:
            out = Path(text[text.index("--out") + 1])
            out.mkdir(parents=True, exist_ok=True)
            for n in range(self.sweep_writes):
                (out / f"alpha{n}.txt").write_text("x")
                on_line(f"Finished {n}")
                if self.hold and n == 3:
                    self.hold.wait(5)
                    if cancel.is_set():
                        return 1
            return 0
        if "ras_csv" in script:
            Path(text[text.index("--out") + 1]).write_text("new csv")
            return 1 if self.convert_fails else 0
        if "fix_cdx.py" in script:
            on_line("r.CDX1 was corrected (2 values)." if self.corrects else "r.CDX1 already matches a.ork.")
            return 0
        if text[0] == "git":
            if text[1] == "diff":
                return 1  # the CSV changed
            if text[1] in ("push", "pull") and self.push_fails:
                return 1
            if text[1] == "rev-parse":
                on_line("abc1234")
            return 0
        if "build_site.py" in script:
            site = Path(text[text.index("--site") + 1]) / "predictions"
            site.mkdir(parents=True, exist_ok=True)
            (site / "index.html").write_text("<h1>page</h1>")
            on_line("built")
            return 1 if self.build_fails else 0
        raise AssertionError(script)

    def request(self, method, path, origin=ORIGIN, host=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if origin:
            headers["Origin"] = origin
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        body = response.read()
        result = (response.status, dict(response.getheaders()), body)
        conn.close()
        return result

    def post(self, job, body=None, **kw):
        status, headers, body = self.request("POST", f"/api/{job}", body=None if body is None else json.dumps(body), **kw)
        return status, json.loads(body or b"{}"), headers

    def status(self):
        return json.loads(self.request("GET", "/api/status")[2])

    def wait_idle(self):
        for _ in range(200):
            st = self.status()
            if not st["busy"]:
                return st
            time.sleep(0.05)
        raise AssertionError("the job never finished")

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()


def with_rig(**kwargs):
    def wrap(test):
        def run():
            rig = Rig(**kwargs)
            try:
                test(rig)
            finally:
                rig.close()
        run.__name__ = test.__name__
        return run
    return wrap


@with_rig()
def test_status_reports_the_files(rig):
    st = rig.status()
    assert st["helper"] == 1 and st["rocket"] == "Rocket R" and st["busy"] is None
    assert st["alpha"]["count"] == 0 and st["alpha"]["dir"] == "aero_modeling/R/RASA/alpha"
    assert st["csv"]["path"] == "aero_modeling/R/RASA/a.csv" and st["cdx"]["path"] == "aero_modeling/R/RASA/r.CDX1"
    assert st["publish"] == ["git add aero_modeling/R/RASA/a.csv", "git add aero_modeling/R/RASA/r.CDX1",
                             'git commit -m "Update R RASAero CSV"', "git push"]


@with_rig()
def test_only_the_pages_site_and_this_helper_are_allowed(rig):
    status, headers, _ = rig.request("GET", "/api/status")
    assert status == 200 and headers["Access-Control-Allow-Origin"] == ORIGIN
    assert headers["Access-Control-Allow-Private-Network"] == "true"
    assert rig.request("GET", "/api/status", origin="https://evil.example")[0] == 403
    assert rig.request("GET", "/api/status", origin=f"http://127.0.0.1:{rig.port}")[0] == 200
    assert rig.request("GET", "/api/status", origin=None)[0] == 200  # the page served by the helper itself
    assert rig.request("GET", "/api/status", host="evil.example")[0] == 403  # DNS rebinding
    assert rig.request("OPTIONS", "/api/update", origin="https://evil.example")[0] == 403
    assert rig.request("OPTIONS", "/api/update")[0] == 204


@with_rig()
def test_a_post_must_come_from_an_allowed_origin(rig):
    assert rig.post("update", origin=None)[0] == 403
    assert rig.post("update", origin="https://evil.example")[0] == 403
    assert rig.calls == []


@with_rig()
def test_update_without_the_files_says_what_is_missing(rig):
    assert rig.post("update")[0] == 200
    st = rig.wait_idle()
    assert "Only 0 of 31" in st["error"] and rig.calls == []


@with_rig()
def test_sweep_counts_its_files_then_update_converts_and_rebuilds(rig):
    assert rig.post("sweep")[0] == 200
    st = rig.wait_idle()
    assert st["error"] is None and st["alpha"]["count"] == 31 and st["progress"] == 31
    assert st["finished"]["job"] == "sweep"
    assert any("--window" in c and "RASAero" in c and "--countdown" in c for c in rig.calls)
    csv = rig.repo / "aero_modeling" / "R" / "RASA" / "a.csv"
    assert csv.read_text() == "old csv"
    assert rig.post("update")[0] == 200
    st = rig.wait_idle()
    assert st["error"] is None and st["finished"]["job"] == "update"
    assert csv.read_text() == "new csv" and not csv.with_name("a.csv.tmp").exists()
    kinds = ["ras_csv" if any("ras_csv" in p for p in c) else "build" for c in rig.calls[1:]]
    assert kinds == ["ras_csv", "build"]
    assert rig.request("GET", "/predictions/", origin=None)[2] == b"<h1>page</h1>"
    assert rig.request("GET", "/predictions/", origin=None)[1]["Cache-Control"] == "no-store"
    assert rig.request("GET", "/", origin=None)[0] == 302


@with_rig(build_fails=True)
def test_a_failed_build_puts_the_old_csv_back(rig):
    rig.post("sweep")
    rig.wait_idle()
    rig.post("update")
    st = rig.wait_idle()
    assert "old CSV was put back" in st["error"] and st["finished"]["job"] == "sweep"
    assert (rig.repo / "aero_modeling" / "R" / "RASA" / "a.csv").read_text() == "old csv"


@with_rig(convert_fails=True)
def test_a_failed_conversion_leaves_the_csv_alone(rig):
    rig.post("sweep")
    rig.wait_idle()
    rig.post("update")
    st = rig.wait_idle()
    assert "CSV is unchanged" in st["error"]
    assert (rig.repo / "aero_modeling" / "R" / "RASA" / "a.csv").read_text() == "old csv"
    assert not (rig.repo / "aero_modeling" / "R" / "RASA" / "a.csv.tmp").exists()


@with_rig(sweep_writes=20)
def test_a_short_sweep_is_an_error(rig):
    rig.post("sweep")
    assert "only 20 of 31" in rig.wait_idle()["error"]


def test_a_second_job_and_cancel():
    hold = threading.Event()
    rig = Rig(hold=hold)
    try:
        assert rig.post("sweep")[0] == 200
        for _ in range(100):
            if rig.status()["progress"] >= 4:
                break
            time.sleep(0.05)
        assert rig.status()["busy"] == "sweep"
        status, body, _ = rig.post("update")
        assert status == 409 and "busy" in body["error"]
        assert rig.post("cancel")[0] == 200
        hold.set()
        assert rig.wait_idle()["error"] == "Cancelled."
    finally:
        hold.set()
        rig.close()


def test_a_missing_problem_blocks_the_job():
    rig = Rig()
    try:
        rig.helper.update_problem = "flight_sim is missing"
        status, body, _ = rig.post("update")
        assert status == 409 and body["error"] == "flight_sim is missing"
        assert rig.status()["updateAvailable"] is False
    finally:
        rig.close()


@with_rig()
def test_the_page_can_open_the_two_folders_and_nothing_else(rig):
    st = rig.status()
    ras = str(rig.repo / "aero_modeling" / "R" / "RASA")
    assert st["folders"] == {"rasaero": ras, "repo": str(rig.repo)}
    status, body, _ = rig.post("open", {"target": "rasaero"})
    assert status == 200 and body == {"opened": "rasaero"} and rig.opened == [Path(ras)]
    assert rig.post("open", {"target": "repo"})[0] == 200 and rig.opened[-1] == rig.repo
    for bad in ({"target": "../../etc"}, {"target": "C:\\Windows"}, {"target": ""}, {}, {"path": str(rig.repo)}):
        status, body, _ = rig.post("open", bad)
        assert status == 400 and "can only open" in body["error"], (bad, status, body)
    assert rig.post("open")[0] == 400  # no body at all
    assert len(rig.opened) == 2


@with_rig()
def test_opening_a_folder_follows_the_same_origin_rules(rig):
    assert rig.post("open", {"target": "repo"}, origin=None)[0] == 403
    assert rig.post("open", {"target": "repo"}, origin="https://evil.example")[0] == 403
    assert rig.post("open", {"target": "repo"}, host="evil.example")[0] == 403
    assert rig.opened == []


@with_rig()
def test_opening_a_folder_that_is_missing_says_so(rig):
    import shutil
    shutil.rmtree(rig.repo / "aero_modeling" / "R" / "RASA")
    status, body, _ = rig.post("open", {"target": "rasaero"})
    assert status == 400 and "does not exist" in body["error"] and rig.opened == []


def test_config_without_alpha_dir_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "c.json"
        config.write_text(json.dumps({"rockets": {"R": {"ork": "a", "aero": "b", "motor": "c"}}}))
        try:
            local_helper.load_context(config, Path(tmp), "python")
        except SystemExit as error:
            assert "alpha_dir" in str(error)
        else:
            raise AssertionError("a rocket without alpha_dir cannot be updated")


@with_rig(publish=True)
def test_a_good_update_commits_only_the_csv_and_cdx_pushes_and_stops(rig):
    rig.post("sweep")
    rig.wait_idle()
    rig.post("update")
    st = rig.wait_idle()
    assert st["error"] is None and st["published"]["commit"] == "abc1234"
    git = [c[1:] for c in rig.calls if c[0] == "git"]
    paths = ["aero_modeling/R/RASA/a.csv", "aero_modeling/R/RASA/r.CDX1"]
    assert git[0] == ["add", "--", *paths]
    assert ["commit", "-m", "Update R RASAero CSV", "--", *paths] in git and ["push"] in git
    assert rig.stopped.wait(2)


@with_rig(publish=True, push_fails=True)
def test_a_failed_push_is_an_error_and_the_helper_keeps_running(rig):
    rig.post("sweep")
    rig.wait_idle()
    rig.post("update")
    st = rig.wait_idle()
    assert "could not be pushed" in st["error"] and st["published"] is None
    assert not rig.stopped.is_set()


@with_rig()
def test_without_publishing_nothing_is_committed(rig):
    rig.post("sweep")
    rig.wait_idle()
    rig.post("update")
    st = rig.wait_idle()
    assert st["error"] is None and st["published"] is None
    assert not any(c[0] == "git" for c in rig.calls)


@with_rig(with_ork=True)
def test_a_corrected_cdx_stops_before_rasaero_so_it_can_be_opened_again(rig):
    rig.post("sweep")
    st = rig.wait_idle()
    assert "Nothing was typed into RASAero" in st["error"] and "File, Open" in st["error"]
    assert not any("rasaero_sweep.py" in " ".join(c) for c in rig.calls)


@with_rig(with_ork=True, corrects=False)
def test_a_matching_cdx_goes_to_the_rasaero_already_open(rig):
    rig.post("sweep")
    assert rig.wait_idle()["error"] is None
    sweep = [c for c in rig.calls if "rasaero_sweep.py" in " ".join(c)][-1]
    assert sweep[-2:] == ["--expect", "r.CDX1"] and "--open" not in sweep


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
