from http.cookiejar import Cookie
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


def _cookie(domain, *, expires=None):
    return Cookie(
        version=0,
        name="SID",
        value="cookie-value",
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/account",
        path_specified=True,
        secure=True,
        expires=expires,
        discard=False,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": None},
        rfc2109=False,
    )


def test_playwright_cookies_limits_records_to_youtube_domains():
    from browser_heatmap import playwright_cookies

    cookies = [
        _cookie(".youtube.com", expires=123),
        _cookie("example.com", expires=456),
    ]

    assert playwright_cookies(cookies) == [{
        "name": "SID",
        "value": "cookie-value",
        "domain": ".youtube.com",
        "path": "/account",
        "secure": True,
        "httpOnly": True,
        "expires": 123,
    }]


def test_playwright_cookies_maps_zero_expiry_to_session_cookie():
    from browser_heatmap import playwright_cookies

    cookies = [
        _cookie(".youtube.com", expires=0),
        _cookie("accounts.youtube.com", expires=123),
    ]

    assert [cookie["expires"] for cookie in playwright_cookies(cookies)] == [-1, 123]


def test_playwright_cookies_converts_chromium_microsecond_epoch_expiry():
    from browser_heatmap import playwright_cookies

    chromium_expiry = 13_444_333_485_766_430
    expected_expiry = 1_799_859_885.76643

    assert playwright_cookies([_cookie(".youtube.com", expires=chromium_expiry)])[0][
        "expires"
    ] == expected_expiry


def _install_worker_process(monkeypatch, browser_heatmap, messages, *, alive=False):
    """Replace multiprocessing's spawn boundary without starting a browser."""
    class Receiver:
        closed = False
        polls = []

        def poll(self, timeout):
            self.polls.append(timeout)
            return bool(messages)

        def recv(self):
            return messages.pop(0)

        def close(self):
            self.closed = True

    class Sender:
        closed = False

        def close(self):
            self.closed = True

    receiver = Receiver()
    sender = Sender()

    class Process:
        started = False
        joins = []
        terminated = False
        killed = False
        pid = 123

        def start(self):
            self.started = True

        def join(self, timeout=None):
            self.joins.append(timeout)

        def is_alive(self):
            return alive

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    process = Process()

    class Context:
        process_args = None

        def Pipe(self, *, duplex):
            assert duplex is False
            return receiver, sender

        def Process(self, *, target, args):
            assert target is browser_heatmap._rendered_heatmap_worker
            self.process_args = args
            return process

    context = Context()
    monkeypatch.setattr(browser_heatmap.multiprocessing, "get_context", lambda name: (
        name == "spawn" and context
    ))
    monkeypatch.setattr(
        browser_heatmap,
        "load_youtube_cookies",
        lambda _path: (_ for _ in ()).throw(AssertionError("parent loaded cookies")),
    )
    return context, process, receiver, sender


def test_fetch_rendered_heatmap_returns_a_valid_worker_result(monkeypatch):
    import browser_heatmap

    result = [{"start_time": 5.0, "end_time": 7.0, "value": 0.75}]
    context, process, receiver, sender = _install_worker_process(
        monkeypatch,
        browser_heatmap,
        [
            {"kind": "ready", "pid": 123, "pgid": 123},
            {"kind": "result", "result": result},
        ],
    )
    assert browser_heatmap.fetch_rendered_heatmap("abc123", "cookies.txt") == [{
        "start_time": 5.0,
        "end_time": 7.0,
        "value": 0.75,
    }]
    assert context.process_args[0:2] == ("abc123", "cookies.txt")
    profile, result_sender = context.process_args[2:]
    assert result_sender is sender
    assert not os.path.exists(profile)
    assert process.started
    assert process.joins
    assert all(timeout is not None for timeout in process.joins)
    assert receiver.closed
    assert sender.closed


def test_fetch_rendered_heatmap_terminates_and_reaps_an_overrunning_worker(monkeypatch):
    import browser_heatmap

    _context, process, _receiver, _sender = _install_worker_process(
        monkeypatch, browser_heatmap, [], alive=True
    )
    monkeypatch.setattr(browser_heatmap, "RENDERED_HEATMAP_WORKER_TIMEOUT_SECONDS", 0)

    assert browser_heatmap.fetch_rendered_heatmap("abc123", "cookies.txt") == []
    assert process.terminated
    assert process.killed
    assert all(timeout is not None for timeout in process.joins)


def test_fetch_rendered_heatmap_propagates_worker_errors(monkeypatch):
    import browser_heatmap

    _install_worker_process(
        monkeypatch,
        browser_heatmap,
        [
            {"kind": "ready", "pid": 123, "pgid": 123},
            {"kind": "error", "error": "RuntimeError while reading rendered player"},
        ],
    )

    with pytest.raises(browser_heatmap.RenderedHeatmapWorkerError, match="RuntimeError"):
        browser_heatmap.fetch_rendered_heatmap("abc123", "cookies.txt")


def test_worker_retries_transient_evaluate_navigation_error_and_closes_context(monkeypatch, tmp_path):
    import browser_heatmap

    raw_markers = [{
        "timeRangeStartMillis": 5_000,
        "markerDurationMillis": 2_000,
        "heatMarkerIntensityScoreNormalized": 0.75,
    }]

    class Page:
        evaluations = 0
        waits = []

        def goto(self, *_args, **_kwargs):
            pass

        def evaluate(self, script):
            assert script == browser_heatmap.PLAYER_MARKERS_SCRIPT
            self.evaluations += 1
            if self.evaluations == 1:
                raise RuntimeError(
                    "Page.evaluate: Execution context was destroyed, most likely because of a navigation"
                )
            return raw_markers

        def wait_for_timeout(self, timeout):
            self.waits.append(timeout)

    page = Page()

    class Context:
        closed = False

        def add_cookies(self, _cookies):
            pass

        def new_page(self):
            return page

        def close(self):
            self.closed = True

    context = Context()

    class Chromium:
        def launch_persistent_context(self, *_args, **_kwargs):
            return context

    class Playwright:
        chromium = Chromium()

    class PlaywrightManager:
        def __enter__(self):
            return Playwright()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(browser_heatmap, "load_youtube_cookies", lambda _path: [{"name": "SID"}])
    monkeypatch.setattr(browser_heatmap, "sync_playwright", lambda: PlaywrightManager())

    assert browser_heatmap._fetch_rendered_heatmap_in_worker(
        "abc123", "cookies.txt", tmp_path / "profile"
    ) == [{
        "start_time": 5.0,
        "end_time": 7.0,
        "value": 0.75,
    }]
    assert page.evaluations == 2
    assert page.waits == [1_000]
    assert context.closed


def _session_probe_worker(_video_id, cookie_file, _profile, result_sender):
    import browser_heatmap

    browser_heatmap._start_rendered_worker_session(result_sender)
    Path(cookie_file).write_text(f"{os.getpid()} {os.getpgrp()}")
    result_sender.send({"kind": "result", "result": []})
    result_sender.close()


def _large_result_worker(_video_id, _cookie_file, _profile, result_sender):
    import browser_heatmap

    browser_heatmap._start_rendered_worker_session(result_sender)
    result_sender.send({
        "kind": "result",
        "result": [
            {"start_time": float(index), "end_time": float(index + 1), "value": 1.0}
            for index in range(10_000)
        ],
    })
    result_sender.close()


def _sleeping_descendant_worker(_video_id, cookie_file, _profile, result_sender):
    import browser_heatmap

    browser_heatmap._start_rendered_worker_session(result_sender)
    descendant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    Path(cookie_file).write_text(str(descendant.pid))
    while True:
        time.sleep(1)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_spawn_worker_creates_its_own_session(tmp_path):
    import browser_heatmap

    probe_file = tmp_path / "session.txt"

    assert browser_heatmap._supervise_rendered_heatmap_worker(
        _session_probe_worker, "abc123", probe_file
    ) == []

    pid, pgid = (int(value) for value in probe_file.read_text().split())
    assert pid == pgid
    assert pgid != os.getpgrp()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_supervisor_drains_a_large_result_before_worker_exit(tmp_path):
    import browser_heatmap

    result = browser_heatmap._supervise_rendered_heatmap_worker(
        _large_result_worker, "abc123", tmp_path / "unused.txt"
    )

    assert len(result) == 10_000
    assert result[0] == {"start_time": 0.0, "end_time": 1.0, "value": 1.0}


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_supervisor_reaps_timed_out_worker_process_group(tmp_path, monkeypatch):
    import browser_heatmap

    descendant_file = tmp_path / "descendant.pid"
    monkeypatch.setattr(browser_heatmap, "RENDERED_HEATMAP_WORKER_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(browser_heatmap, "RENDERED_HEATMAP_TERMINATE_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(browser_heatmap, "RENDERED_HEATMAP_KILL_GRACE_SECONDS", 0.2)

    assert browser_heatmap._supervise_rendered_heatmap_worker(
        _sleeping_descendant_worker, "abc123", descendant_file
    ) == []

    descendant_pid = int(descendant_file.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(descendant_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("timed-out worker left its descendant running")
