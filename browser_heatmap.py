"""Read YouTube heat markers from the rendered Brave player."""
import math
import multiprocessing
import os
import signal
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
RENDERED_HEATMAP_SUPERVISOR_POLL_SECONDS = 0.1
RENDERED_HEATMAP_TERMINATE_GRACE_SECONDS = 2
RENDERED_HEATMAP_KILL_GRACE_SECONDS = 2
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


def _fetch_rendered_heatmap_in_worker(video_id, cookie_file, profile):
    """Run the normal Playwright lifecycle in the isolated worker process."""
    cookies = load_youtube_cookies(cookie_file)
    if not cookies:
        return []

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


def _start_rendered_worker_session(result_sender):
    """Isolate Brave descendants before the worker is allowed to launch them."""
    pid = os.getpid()
    pgid = None
    if os.name == "posix":
        os.setsid()
        pgid = os.getpgrp()
    result_sender.send({"kind": "ready", "pid": pid, "pgid": pgid})


def _rendered_heatmap_worker(video_id, cookie_file, profile, result_sender):
    """Run the isolated browser and send cookie-free IPC messages to its parent."""
    try:
        _start_rendered_worker_session(result_sender)
        result_sender.send({
            "kind": "result",
            "result": _fetch_rendered_heatmap_in_worker(video_id, cookie_file, profile),
        })
    except Exception as error:
        # Exception text can include browser request details; keep candidate logs
        # useful without ever risking a cookie value appearing in them.
        result_sender.send({
            "kind": "error",
            "error": f"{type(error).__name__} while reading rendered player",
        })
    finally:
        result_sender.close()


def _ready_worker_group(message, worker):
    """Return a safe process group only when the worker positively identified it."""
    if os.name != "posix" or not isinstance(message, dict):
        return None
    pid = message.get("pid")
    pgid = message.get("pgid")
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or not isinstance(pgid, int)
        or isinstance(pgid, bool)
        or pid <= 0
        or pgid != pid
        or pid != worker.pid
    ):
        return None
    return pgid


def _stop_worker_with_deadline(worker, worker_group):
    """Stop a timed-out worker tree with finite TERM and KILL grace periods."""
    if worker_group is not None:
        try:
            os.killpg(worker_group, signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        worker.terminate()
    worker.join(RENDERED_HEATMAP_TERMINATE_GRACE_SECONDS)
    if not worker.is_alive():
        return

    if worker_group is not None:
        try:
            os.killpg(worker_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        worker.kill()
    worker.join(RENDERED_HEATMAP_KILL_GRACE_SECONDS)


def _supervise_rendered_heatmap_worker(worker_target, video_id, cookie_file):
    """Run a spawn worker while continuously draining its bounded IPC protocol."""
    spawn_context = multiprocessing.get_context("spawn")
    result_receiver, result_sender = spawn_context.Pipe(duplex=False)
    with TemporaryDirectory(prefix="yt-brave-") as profile:
        worker = spawn_context.Process(
            target=worker_target,
            args=(video_id, os.fspath(cookie_file), profile, result_sender),
        )
        try:
            worker.start()
        except Exception:
            result_sender.close()
            result_receiver.close()
            raise
        result_sender.close()

        ready_message = None
        result = None
        worker_error = None
        protocol_error = None
        receiver_open = True
        deadline = time.monotonic() + RENDERED_HEATMAP_WORKER_TIMEOUT_SECONDS
        try:
            while True:
                while receiver_open and result_receiver.poll(0):
                    try:
                        message = result_receiver.recv()
                    except EOFError:
                        receiver_open = False
                        break

                    if not isinstance(message, dict):
                        protocol_error = "rendered heatmap worker returned an invalid message"
                        continue
                    kind = message.get("kind")
                    if kind == "ready":
                        if ready_message is not None:
                            protocol_error = "rendered heatmap worker sent duplicate readiness"
                        elif _ready_worker_group(message, worker) is None and os.name == "posix":
                            protocol_error = "rendered heatmap worker reported an invalid process group"
                        else:
                            ready_message = message
                    elif kind == "result":
                        if ready_message is None or result is not None or worker_error is not None:
                            protocol_error = "rendered heatmap worker sent an invalid result sequence"
                        else:
                            result = message.get("result")
                    elif kind == "error":
                        if ready_message is None or worker_error is not None or result is not None:
                            protocol_error = "rendered heatmap worker sent an invalid error sequence"
                        else:
                            error = message.get("error")
                            worker_error = error if isinstance(error, str) and error else (
                                "unknown error while reading rendered player"
                            )
                    else:
                        protocol_error = "rendered heatmap worker returned an unknown message"

                worker.join(0)
                if not worker.is_alive():
                    break

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _stop_worker_with_deadline(
                        worker, _ready_worker_group(ready_message, worker)
                    )
                    return []
                result_receiver.poll(min(remaining, RENDERED_HEATMAP_SUPERVISOR_POLL_SECONDS))
        finally:
            result_receiver.close()

    if protocol_error is not None:
        raise RenderedHeatmapWorkerError(protocol_error)
    if worker_error is not None:
        raise RenderedHeatmapWorkerError(f"rendered heatmap worker failed: {worker_error}")
    if result is None:
        raise RenderedHeatmapWorkerError("rendered heatmap worker exited without a result")

    normalized = _validated_normalized_heatmap(result)
    if normalized is None:
        raise RenderedHeatmapWorkerError(
            "rendered heatmap worker returned invalid normalized markers"
        )
    return normalized


def fetch_rendered_heatmap(video_id, cookie_file):
    """Fetch rendered markers with a parent-enforced deadline for Brave teardown."""
    return _supervise_rendered_heatmap_worker(
        _rendered_heatmap_worker, video_id, cookie_file
    )
