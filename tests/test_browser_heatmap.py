from http.cookiejar import Cookie
import signal
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


def test_fetch_rendered_heatmap_normalizes_browser_markers_and_closes_context(monkeypatch):
    import browser_heatmap

    raw_markers = [{
        "timeRangeStartMillis": 5_000,
        "markerDurationMillis": 2_000,
        "heatMarkerIntensityScoreNormalized": 0.75,
    }]

    class Page:
        def goto(self, url, *, wait_until, timeout):
            assert url == "https://www.youtube.com/watch?v=abc123"
            assert wait_until == "domcontentloaded"
            assert timeout == 20_000

        def evaluate(self, script):
            assert script == browser_heatmap.PLAYER_MARKERS_SCRIPT
            return raw_markers

        def wait_for_timeout(self, timeout):
            raise AssertionError(f"unexpected poll wait: {timeout}")

    class Context:
        closed = False

        def add_cookies(self, cookies):
            assert cookies == [{"name": "SID"}]

        def new_page(self):
            return Page()

        def close(self):
            self.closed = True

    context = Context()

    class Chromium:
        def launch_persistent_context(self, profile, **kwargs):
            assert kwargs == {
                "executable_path": browser_heatmap.BRAVE_EXECUTABLE,
                "headless": False,
                "args": ["--no-first-run", "--no-default-browser-check"],
            }
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

    assert browser_heatmap.fetch_rendered_heatmap("abc123", "cookies.txt") == [{
        "start_time": 5.0,
        "end_time": 7.0,
        "value": 0.75,
    }]
    assert context.closed


@pytest.mark.parametrize("failure", ["goto", "evaluate"])
def test_fetch_rendered_heatmap_closes_context_when_browser_errors(monkeypatch, failure):
    import browser_heatmap

    class Page:
        def goto(self, *_args, **_kwargs):
            if failure == "goto":
                raise RuntimeError("goto failed")

        def evaluate(self, _script):
            if failure == "evaluate":
                raise RuntimeError("evaluate failed")
            return []

    class Context:
        closed = False

        def add_cookies(self, _cookies):
            pass

        def new_page(self):
            return Page()

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

    with pytest.raises(RuntimeError, match=f"{failure} failed"):
        browser_heatmap.fetch_rendered_heatmap("abc123", "cookies.txt")

    assert context.closed


def test_fetch_rendered_heatmap_returns_empty_after_no_marker_polling_timeout(monkeypatch):
    import browser_heatmap

    class Page:
        evaluations = 0
        waits = []

        def goto(self, *_args, **_kwargs):
            pass

        def evaluate(self, _script):
            self.evaluations += 1
            return []

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

    monotonic_times = iter([0, 0, browser_heatmap.RENDERED_HEATMAP_TIMEOUT_SECONDS])
    monkeypatch.setattr(browser_heatmap, "load_youtube_cookies", lambda _path: [{"name": "SID"}])
    monkeypatch.setattr(browser_heatmap, "sync_playwright", lambda: PlaywrightManager())
    monkeypatch.setattr(browser_heatmap.time, "monotonic", lambda: next(monotonic_times))

    assert browser_heatmap.fetch_rendered_heatmap("abc123", "cookies.txt") == []
    assert page.evaluations == 2
    assert page.waits == [1_000]
    assert context.closed


def test_fetch_rendered_heatmap_interrupts_a_stuck_evaluate_at_deadline(monkeypatch):
    import browser_heatmap

    class Page:
        def goto(self, *_args, **_kwargs):
            pass

        def evaluate(self, _script):
            time.sleep(0.2)
            return []

        def wait_for_timeout(self, _timeout):
            pass

    class Context:
        closed = False

        def add_cookies(self, _cookies):
            pass

        def new_page(self):
            return Page()

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
    monkeypatch.setattr(browser_heatmap, "RENDERED_HEATMAP_TIMEOUT_SECONDS", 0.05)

    started = time.monotonic()
    assert browser_heatmap.fetch_rendered_heatmap("abc123", "cookies.txt") == []
    assert time.monotonic() - started < 0.15
    assert context.closed
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_rendered_heatmap_deadline_rearms_an_overdue_prior_alarm():
    import browser_heatmap

    original_handler = signal.getsignal(signal.SIGALRM)
    original_delay, original_interval = signal.getitimer(signal.ITIMER_REAL)
    fired = []

    def prior_handler(_signum, _frame):
        fired.append(True)

    signal.signal(signal.SIGALRM, prior_handler)
    signal.setitimer(signal.ITIMER_REAL, 0.01)
    try:
        with browser_heatmap._rendered_heatmap_deadline(0.1):
            time.sleep(0.03)

        restored_delay, restored_interval = signal.getitimer(signal.ITIMER_REAL)
        assert restored_delay > 0
        assert restored_interval == 0

        time.sleep(0.03)
        assert fired == [True]
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, original_handler)
        signal.setitimer(signal.ITIMER_REAL, original_delay, original_interval)


def test_fetch_rendered_heatmap_bounds_blocking_close_after_timeout(monkeypatch):
    import browser_heatmap

    original_handler = signal.getsignal(signal.SIGALRM)
    original_delay, original_interval = signal.getitimer(signal.ITIMER_REAL)
    prior_fired = []

    def prior_handler(_signum, _frame):
        prior_fired.append(True)

    class Page:
        def goto(self, *_args, **_kwargs):
            pass

        def evaluate(self, _script):
            time.sleep(0.2)
            return []

        def wait_for_timeout(self, _timeout):
            pass

    class Context:
        close_started = False

        def add_cookies(self, _cookies):
            pass

        def new_page(self):
            return Page()

        def close(self):
            self.close_started = True
            time.sleep(0.2)

    context = Context()

    class Chromium:
        def launch_persistent_context(self, *_args, **_kwargs):
            return context

    class Playwright:
        chromium = Chromium()

    class PlaywrightManager:
        exited = False

        def __enter__(self):
            return Playwright()

        def __exit__(self, *_args):
            self.exited = True
            return False

    manager = PlaywrightManager()
    signal.signal(signal.SIGALRM, prior_handler)
    signal.setitimer(signal.ITIMER_REAL, 1)
    try:
        monkeypatch.setattr(browser_heatmap, "load_youtube_cookies", lambda _path: [{"name": "SID"}])
        monkeypatch.setattr(browser_heatmap, "sync_playwright", lambda: manager)
        monkeypatch.setattr(browser_heatmap, "RENDERED_HEATMAP_TIMEOUT_SECONDS", 0.05)

        started = time.monotonic()
        assert browser_heatmap.fetch_rendered_heatmap("abc123", "cookies.txt") == []
        assert time.monotonic() - started < 0.15
        assert context.close_started
        assert manager.exited
        assert signal.getsignal(signal.SIGALRM) is prior_handler
        restored_delay, restored_interval = signal.getitimer(signal.ITIMER_REAL)
        assert restored_delay > 0
        assert restored_interval == 0
        assert prior_fired == []
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, original_handler)
        signal.setitimer(signal.ITIMER_REAL, original_delay, original_interval)
