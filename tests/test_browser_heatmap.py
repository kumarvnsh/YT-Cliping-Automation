from http.cookiejar import Cookie


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
