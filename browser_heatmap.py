"""Read YouTube heat markers from the rendered Brave player."""
import math
import multiprocessing
import os
import time
from http.cookiejar import Cookie, MozillaCookieJar
from tempfile import TemporaryDirectory

from playwright.sync_api import sync_playwright

from heatmaps import normalize_heat_markers


BRAVE_EXECUTABLE = os.getenv(
    "BRAVE_EXECUTABLE", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
)
RENDERED_HEATMAP_TIMEOUT_SECONDS = 20
RENDERED_HEATMAP_POLL_SECONDS = 1
RENDERED_HEATMAP_WORKER_TIMEOUT_SECONDS = 25
CHROMIUM_EXPIRY_THRESHOLD = 10_000_000_000
CHROMIUM_UNIX_EPOCH_OFFSET_SECONDS = 11_644_473_600

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


class RenderedHeatmapWorkerError(RuntimeError):
    """The isolated Brave worker failed before reporting heatmap data."""


def _is_youtube_domain(domain):
    domain = domain.lower()
    return domain == "youtube.com" or domain.endswith(".youtube.com")


def _playwright_expiry(expires):
    """Return a valid Playwright expiry from a Netscape cookie expiry."""
    if isinstance(expires, bool) or not isinstance(expires, (int, float)):
        return -1
    try:
        if not math.isfinite(expires) or expires <= 0:
            return -1
        if expires > CHROMIUM_EXPIRY_THRESHOLD:
            expires = (
                expires / 1_000_000 - CHROMIUM_UNIX_EPOCH_OFFSET_SECONDS
            )
        return expires if math.isfinite(expires) and expires > 0 else -1
    except OverflowError:
        return -1


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
            "expires": _playwright_expiry(cookie.expires),
        })
    return records


def load_youtube_cookies(cookie_file):
    """Load a Netscape-format cookie file and return YouTube Playwright cookies."""
    cookie_jar = MozillaCookieJar(cookie_file)
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    return playwright_cookies(cookie_jar)


def _is_transient_evaluate_navigation_error(error):
    """Whether an evaluation raced with YouTube replacing its player document."""
    message = str(error).lower()
    return (
        "execution context was destroyed" in message
        and "navigation" in message
        and "evaluate" in message
    )


def _validated_normalized_heatmap(result):
    """Return a canonical rendered heatmap only when the worker result is valid."""
    if not isinstance(result, list):
        return None

    raw_markers = []
    expected_keys = {"start_time", "end_time", "value"}
    for marker in result:
        if not isinstance(marker, dict) or set(marker) != expected_keys:
            return None
        start = marker["start_time"]
        end = marker["end_time"]
        value = marker["value"]
        if not all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            for item in (start, end, value)
        ):
            return None
        raw_markers.append({
            "timeRangeStartMillis": start * 1_000,
            "markerDurationMillis": (end - start) * 1_000,
            "heatMarkerIntensityScoreNormalized": value,
        })

    normalized = normalize_heat_markers(raw_markers)
    return normalized if normalized == result else None


def _fetch_rendered_heatmap_in_worker(video_id, cookie_file):
    """Run the normal Playwright lifecycle in the isolated worker process."""
    cookies = load_youtube_cookies(cookie_file)
    if not cookies:
        return []

    with TemporaryDirectory(prefix="yt-brave-") as profile:
        with sync_playwright() as playwright:
            context = None
            try:
                context = playwright.chromium.launch_persistent_context(
                    profile,
                    executable_path=BRAVE_EXECUTABLE,
                    headless=False,
                    args=["--no-first-run", "--no-default-browser-check"],
                )
                context.add_cookies(cookies)
                page = context.new_page()
                page.goto(
                    f"https://www.youtube.com/watch?v={video_id}",
                    wait_until="domcontentloaded",
                    timeout=20_000,
                )
                deadline = time.monotonic() + RENDERED_HEATMAP_TIMEOUT_SECONDS
                while True:
                    try:
                        markers = page.evaluate(PLAYER_MARKERS_SCRIPT)
                    except Exception as error:
                        if not _is_transient_evaluate_navigation_error(error):
                            raise
                        if time.monotonic() >= deadline:
                            return []
                        page.wait_for_timeout(RENDERED_HEATMAP_POLL_SECONDS * 1_000)
                        continue
                    normalized = normalize_heat_markers(markers or [])
                    if normalized:
                        return normalized
                    if time.monotonic() >= deadline:
                        return []
                    page.wait_for_timeout(RENDERED_HEATMAP_POLL_SECONDS * 1_000)
            finally:
                if context is not None:
                    context.close()


def _rendered_heatmap_worker(video_id, cookie_file, result_sender):
    """Send a small, cookie-free result envelope back to the parent process."""
    try:
        result_sender.send({
            "ok": True,
            "result": _fetch_rendered_heatmap_in_worker(video_id, cookie_file),
        })
    except Exception as error:
        # Exception text can include browser request details; keep candidate logs
        # useful without ever risking a cookie value appearing in them.
        result_sender.send({
            "ok": False,
            "error": f"{type(error).__name__} while reading rendered player",
        })
    finally:
        result_sender.close()


def fetch_rendered_heatmap(video_id, cookie_file):
    """Fetch rendered markers with a parent-enforced deadline for Brave teardown."""
    spawn_context = multiprocessing.get_context("spawn")
    result_receiver, result_sender = spawn_context.Pipe(duplex=False)
    worker = spawn_context.Process(
        target=_rendered_heatmap_worker,
        args=(video_id, os.fspath(cookie_file), result_sender),
    )
    try:
        worker.start()
    except Exception:
        result_sender.close()
        result_receiver.close()
        raise
    result_sender.close()

    try:
        worker.join(RENDERED_HEATMAP_WORKER_TIMEOUT_SECONDS)
        if worker.is_alive():
            worker.terminate()
            worker.join()
            return []

        if not result_receiver.poll(0):
            raise RenderedHeatmapWorkerError(
                "rendered heatmap worker exited without a result"
            )
        envelope = result_receiver.recv()
    finally:
        result_receiver.close()

    if not isinstance(envelope, dict) or not isinstance(envelope.get("ok"), bool):
        raise RenderedHeatmapWorkerError("rendered heatmap worker returned an invalid result")
    if not envelope["ok"]:
        error = envelope.get("error")
        if not isinstance(error, str) or not error:
            error = "unknown error while reading rendered player"
        raise RenderedHeatmapWorkerError(f"rendered heatmap worker failed: {error}")

    normalized = _validated_normalized_heatmap(envelope.get("result"))
    if normalized is None:
        raise RenderedHeatmapWorkerError(
            "rendered heatmap worker returned invalid normalized markers"
        )
    return normalized
