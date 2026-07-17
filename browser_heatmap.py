"""Read YouTube heat markers from the rendered Brave player."""
import os
import signal
import time
from contextlib import contextmanager
from http.cookiejar import Cookie, MozillaCookieJar
from tempfile import TemporaryDirectory
from time import monotonic

from playwright.sync_api import sync_playwright

from heatmaps import normalize_heat_markers


BRAVE_EXECUTABLE = os.getenv(
    "BRAVE_EXECUTABLE", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
)
RENDERED_HEATMAP_TIMEOUT_SECONDS = 20
RENDERED_HEATMAP_POLL_SECONDS = 1

PLAYER_MARKERS_SCRIPT = """
() => {
  const player = document.querySelector('ytd-player')?.player_;
  const roots = [window.ytInitialPlayerResponse, player?.getPlayerResponse?.()];
  const seen = new WeakSet();
  const markers = [];

  const walk = (value) => {
    if (value === null || typeof value !== 'object' || seen.has(value)) return;
    seen.add(value);
    if (value.heatMarkerRenderer) markers.push(value.heatMarkerRenderer);
    for (const child of Object.values(value)) walk(child);
  };

  for (const root of roots) walk(root);
  return markers;
}
"""


class RenderedHeatmapTimeout(TimeoutError):
    """Raised when rendered-player retrieval exceeds its total deadline."""


@contextmanager
def _rendered_heatmap_deadline(seconds):
    """Apply a Unix alarm deadline while preserving a caller's alarm state."""
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
    started = monotonic()

    def raise_timeout(_signum, _frame):
        raise RenderedHeatmapTimeout("rendered heatmap retrieval timed out")

    signal.signal(signal.SIGALRM, raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        elapsed = monotonic() - started
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        remaining_delay = previous_delay - elapsed
        if previous_delay and remaining_delay <= 0:
            remaining_delay = 0.001
        else:
            remaining_delay = max(0, remaining_delay)
        signal.setitimer(signal.ITIMER_REAL, remaining_delay, previous_interval)


def _is_youtube_domain(domain):
    domain = domain.lower()
    return domain == "youtube.com" or domain.endswith(".youtube.com")


def playwright_cookies(cookies):
    """Convert YouTube cookies from a Mozilla cookie jar for Playwright."""
    records = []
    for cookie in cookies:
        if not isinstance(cookie, Cookie) or not _is_youtube_domain(cookie.domain):
            continue
        records.append({
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path or "/",
            "secure": bool(cookie.secure),
            "httpOnly": any(key.lower() == "httponly" for key in cookie._rest),
            "expires": cookie.expires if cookie.expires is not None else -1,
        })
    return records


def load_youtube_cookies(cookie_file):
    """Load a Netscape-format cookie file and return YouTube Playwright cookies."""
    cookie_jar = MozillaCookieJar(cookie_file)
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    return playwright_cookies(cookie_jar)


def fetch_rendered_heatmap(video_id, cookie_file):
    """Fetch normalized heat markers from YouTube's rendered player state."""
    cookies = load_youtube_cookies(cookie_file)
    if not cookies:
        return []

    with TemporaryDirectory(prefix="yt-brave-") as profile:
        context = None
        try:
            with _rendered_heatmap_deadline(RENDERED_HEATMAP_TIMEOUT_SECONDS):
                with sync_playwright() as playwright:
                    context = playwright.chromium.launch_persistent_context(
                        profile,
                        executable_path=BRAVE_EXECUTABLE,
                        headless=False,
                        args=["--no-first-run", "--no-default-browser-check"],
                    )
                    try:
                        context.add_cookies(cookies)
                        page = context.new_page()
                        page.goto(
                            f"https://www.youtube.com/watch?v={video_id}",
                            wait_until="domcontentloaded",
                            timeout=20_000,
                        )
                        deadline = time.monotonic() + RENDERED_HEATMAP_TIMEOUT_SECONDS
                        while True:
                            markers = page.evaluate(PLAYER_MARKERS_SCRIPT)
                            normalized = normalize_heat_markers(markers or [])
                            if normalized:
                                return normalized
                            if time.monotonic() >= deadline:
                                return []
                            page.wait_for_timeout(RENDERED_HEATMAP_POLL_SECONDS * 1_000)
                    finally:
                        context.close()
        except RenderedHeatmapTimeout:
            return []
