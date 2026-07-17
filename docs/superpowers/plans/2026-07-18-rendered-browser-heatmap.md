# Rendered Browser Heatmap Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Obtain YouTube most-replayed markers from a visible, isolated Brave browser session when static watch-page HTML has no heatmap data.

**Architecture:** Move renderer-record validation into a small pure module shared by the existing static parser and a new Playwright adapter. The adapter imports only the run's temporary cookie jar into a new Brave profile, polls the rendered player response for heat-marker renderer objects, then closes and deletes the profile. `pipeline.py` tries static data first and invokes the browser fallback only when `USE_RENDERED_HEATMAP=1`.

**Tech Stack:** Python 3.14+, Playwright Python package, locally installed Brave, Netscape `cookies.txt`, pytest, GitHub Actions self-hosted macOS runner.

Spec: `docs/superpowers/specs/2026-07-18-rendered-browser-heatmap-design.md`

---

## File structure

- Create: `heatmaps.py` — pure validation, normalization, de-duplication, and parsing of heat-marker renderer records.
- Create: `browser_heatmap.py` — Brave/Playwright temporary-context lifecycle and rendered-player extraction only.
- Modify: `pipeline.py` — imports the static parser, calls browser fallback after an empty static result, and retains candidate-level errors.
- Modify: `tests/test_pipeline.py` — pure normalizer, static-to-browser fallback, and workflow regression tests.
- Modify: `requirements.txt` — add the Playwright Python package.
- Modify: `.github/workflows/daily.yml` — enable rendered fallback and declare the default Brave executable.
- Modify: `README.md` — explain the visible temporary Brave window and local runner requirement.

### Task 1: Extract shared heatmap normalization

**Files:**
- Create: `heatmaps.py`
- Modify: `pipeline.py:1-115`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing pure-normalizer tests**

Add these tests to `tests/test_pipeline.py`:

```python
import heatmaps


def test_normalize_heat_markers_keeps_first_duplicate_and_sorts():
    markers = [
        {"timeRangeStartMillis": 20_000, "markerDurationMillis": 5_000,
         "heatMarkerIntensityScoreNormalized": 0.4},
        {"timeRangeStartMillis": 0, "markerDurationMillis": 5_000,
         "heatMarkerIntensityScoreNormalized": 1.0},
        {"timeRangeStartMillis": 0, "markerDurationMillis": 5_000,
         "heatMarkerIntensityScoreNormalized": 0.2},
    ]

    assert heatmaps.normalize_heat_markers(markers) == [
        {"start_time": 0.0, "end_time": 5.0, "value": 1.0},
        {"start_time": 20.0, "end_time": 25.0, "value": 0.4},
    ]


def test_normalize_heat_markers_ignores_invalid_numeric_values():
    markers = [
        {"timeRangeStartMillis": "bad", "markerDurationMillis": 5_000,
         "heatMarkerIntensityScoreNormalized": 1.0},
        {"timeRangeStartMillis": 0, "markerDurationMillis": 0,
         "heatMarkerIntensityScoreNormalized": 1.0},
        {"timeRangeStartMillis": 0, "markerDurationMillis": 5_000,
         "heatMarkerIntensityScoreNormalized": float("inf")},
    ]

    assert heatmaps.normalize_heat_markers(markers) == []
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_pipeline.py -q -k normalize_heat_markers
```

Expected: collection fails because `heatmaps` does not exist.

- [ ] **Step 3: Create the pure normalizer and migrate static parsing**

Create `heatmaps.py`:

```python
import json
import math
import re

HEAT_MARKER_RE = re.compile(r'"heatMarkerRenderer"\s*:\s*')


def _finite_number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def normalize_heat_markers(markers):
    normalized = []
    seen_ranges = set()
    for marker in markers:
        if not isinstance(marker, dict):
            continue
        start_ms = marker.get("timeRangeStartMillis")
        duration_ms = marker.get("markerDurationMillis")
        value = marker.get("heatMarkerIntensityScoreNormalized")
        if not all(_finite_number(item) for item in (start_ms, duration_ms, value)):
            continue
        start, end = start_ms / 1000, (start_ms + duration_ms) / 1000
        if (not math.isfinite(start) or not math.isfinite(end)
                or duration_ms <= 0 or start < 0 or end <= start):
            continue
        marker_range = (start, end)
        if marker_range in seen_ranges:
            continue
        seen_ranges.add(marker_range)
        normalized.append({"start_time": float(start), "end_time": float(end),
                           "value": float(value)})
    return sorted(normalized, key=lambda marker: (marker["start_time"], marker["end_time"]))


def parse_watch_heatmap(html):
    decoder = json.JSONDecoder()
    renderer_markers = []
    for match in HEAT_MARKER_RE.finditer(html):
        try:
            marker, _ = decoder.raw_decode(html, match.end())
        except (json.JSONDecodeError, ValueError):
            continue
        renderer_markers.append(marker)
    return normalize_heat_markers(renderer_markers)
```

In `pipeline.py`, remove `HEAT_MARKER_RE`, `_number`, `_finite_number`, and the
local `parse_watch_heatmap`, then import:

```python
from heatmaps import parse_watch_heatmap
```

- [ ] **Step 4: Run focused and full tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_pipeline.py -q -k "normalize_heat_markers or parse_watch_heatmap"
.venv/bin/python -m pytest -q
```

Expected: new normalizer tests and existing static-parser tests pass.

- [ ] **Step 5: Commit the pure extraction**

```bash
git add heatmaps.py pipeline.py tests/test_pipeline.py
git commit -m "refactor: share heatmap marker normalization"
```

### Task 2: Add isolated Brave rendered-player extraction

**Files:**
- Create: `browser_heatmap.py`
- Modify: `requirements.txt`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing browser-boundary tests**

Add these tests to `tests/test_pipeline.py`:

```python
import browser_heatmap
from http.cookiejar import Cookie


def test_playwright_cookie_filters_to_youtube_and_preserves_scope():
    cookie = Cookie(0, "SID", "value", None, False, ".youtube.com", True,
                    True, "/", True, False, 1_900_000_000, False, None, None,
                    {}, False)
    other = Cookie(0, "other", "value", None, False, ".example.com", True,
                   True, "/", True, False, None, False, None, None, {}, False)

    assert browser_heatmap.playwright_cookies([cookie, other]) == [{
        "name": "SID", "value": "value", "domain": ".youtube.com",
        "path": "/", "secure": False, "httpOnly": False,
        "expires": 1_900_000_000,
    }]


def test_rendered_heatmap_closes_context_and_normalizes_page_markers(monkeypatch, tmp_path):
    calls = []

    class Page:
        def goto(self, *_args, **_kwargs):
            calls.append("goto")
        def evaluate(self, _script):
            return [{"timeRangeStartMillis": 0, "markerDurationMillis": 5000,
                     "heatMarkerIntensityScoreNormalized": 1.0}]

    class Context:
        def add_cookies(self, cookies):
            calls.append(("cookies", cookies))
        def new_page(self):
            return Page()
        def close(self):
            calls.append("close")

    class Chromium:
        def launch_persistent_context(self, *_args, **_kwargs):
            return Context()

    class Playwright:
        chromium = Chromium()

    class PlaywrightContext:
        def __enter__(self):
            return Playwright()
        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(browser_heatmap, "sync_playwright", lambda: PlaywrightContext())
    monkeypatch.setattr(browser_heatmap, "load_youtube_cookies", lambda _path: [])

    result = browser_heatmap.fetch_rendered_heatmap("abc123", tmp_path / "cookies.txt")

    assert result == [{"start_time": 0.0, "end_time": 5.0, "value": 1.0}]
    assert calls[-1] == "close"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_pipeline.py -q -k "playwright_cookie or rendered_heatmap_closes"
```

Expected: collection fails because `browser_heatmap` does not exist.

- [ ] **Step 3: Implement the Playwright adapter**

Create `browser_heatmap.py` with these constants and functions:

```python
import os
import tempfile
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path

from heatmaps import normalize_heat_markers
from playwright.sync_api import sync_playwright

BRAVE_EXECUTABLE = os.getenv(
    "BRAVE_EXECUTABLE",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)
RENDERED_HEATMAP_TIMEOUT_SECONDS = 20
RENDERED_HEATMAP_POLL_SECONDS = 1
PLAYER_MARKERS_SCRIPT = """
() => {
  const player = document.querySelector('ytd-player')?.player_;
  const roots = [window.ytInitialPlayerResponse, player?.getPlayerResponse?.()];
  const found = [];
  const walk = (value, seen = new WeakSet()) => {
    if (!value || typeof value !== 'object' || seen.has(value)) return;
    seen.add(value);
    if (value.heatMarkerRenderer) found.push(value.heatMarkerRenderer);
    for (const child of Object.values(value)) walk(child, seen);
  };
  roots.forEach(root => walk(root));
  return found;
}
"""


def playwright_cookies(cookies):
    result = []
    for cookie in cookies:
        if not cookie.domain.lstrip(".").endswith("youtube.com"):
            continue
        result.append({
            "name": cookie.name, "value": cookie.value, "domain": cookie.domain,
            "path": cookie.path or "/", "secure": bool(cookie.secure),
            "httpOnly": bool(cookie._rest.get("HttpOnly")),
            "expires": cookie.expires if cookie.expires is not None else -1,
        })
    return result


def load_youtube_cookies(cookie_file):
    jar = MozillaCookieJar(str(cookie_file))
    jar.load(ignore_discard=True, ignore_expires=True)
    return playwright_cookies(jar)


def fetch_rendered_heatmap(video_id, cookie_file):
    cookies = load_youtube_cookies(cookie_file)
    if not cookies:
        return []
    with tempfile.TemporaryDirectory(prefix="yt-brave-") as profile:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                profile, executable_path=BRAVE_EXECUTABLE, headless=False,
                args=["--no-first-run", "--no-default-browser-check"],
            )
            try:
                context.add_cookies(cookies)
                page = context.new_page()
                page.goto(f"https://www.youtube.com/watch?v={video_id}",
                          wait_until="domcontentloaded", timeout=20_000)
                deadline = time.monotonic() + RENDERED_HEATMAP_TIMEOUT_SECONDS
                while time.monotonic() < deadline:
                    heatmap = normalize_heat_markers(page.evaluate(PLAYER_MARKERS_SCRIPT))
                    if heatmap:
                        return heatmap
                    page.wait_for_timeout(RENDERED_HEATMAP_POLL_SECONDS * 1000)
                return []
            finally:
                context.close()
```

Append this exact dependency to `requirements.txt`:

```text
playwright
```

Install the new dependency into the active local test environment before
running the green tests:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

- [ ] **Step 4: Run focused and full tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_pipeline.py -q -k "playwright_cookie or rendered_heatmap_closes"
.venv/bin/python -m pytest -q
```

Expected: the mocked browser lifecycle test confirms context closure and the
full suite remains green without launching Brave.

- [ ] **Step 5: Commit the browser adapter**

```bash
git add browser_heatmap.py requirements.txt tests/test_pipeline.py
git commit -m "feat: read heatmaps from rendered Brave player"
```

### Task 3: Integrate fallback, runner configuration, and documentation

**Files:**
- Modify: `pipeline.py:115-145,270-310`
- Modify: `.github/workflows/daily.yml:20-55`
- Modify: `README.md:7-30`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing static-to-browser fallback and workflow tests**

Add these tests to `tests/test_pipeline.py`:

```python
def test_fetch_heatmap_uses_rendered_browser_only_after_empty_static_result(monkeypatch):
    monkeypatch.setattr(pipeline, "fetch_watch_heatmap", lambda _vid: [])
    monkeypatch.setattr(
        pipeline.browser_heatmap,
        "fetch_rendered_heatmap",
        lambda vid, cookie_file: [{"start_time": 0.0, "end_time": 5.0, "value": 1.0}],
    )
    monkeypatch.setenv("USE_RENDERED_HEATMAP", "1")

    assert pipeline.fetch_heatmap("abc123") == [
        {"start_time": 0.0, "end_time": 5.0, "value": 1.0},
    ]


def test_fetch_heatmap_does_not_launch_browser_when_static_markers_exist(monkeypatch):
    static = [{"start_time": 0.0, "end_time": 5.0, "value": 1.0}]
    monkeypatch.setattr(pipeline, "fetch_watch_heatmap", lambda _vid: static)
    monkeypatch.setattr(
        pipeline.browser_heatmap,
        "fetch_rendered_heatmap",
        lambda *_args: (_ for _ in ()).throw(AssertionError("browser launched")),
    )
    monkeypatch.setenv("USE_RENDERED_HEATMAP", "1")

    assert pipeline.fetch_heatmap("abc123") == static


def test_workflow_enables_rendered_brave_heatmap_fallback():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/daily.yml").read_text()

    assert "USE_RENDERED_HEATMAP: \"1\"" in workflow
    assert "BRAVE_EXECUTABLE:" in workflow
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_pipeline.py -q -k "fetch_heatmap_uses_rendered or does_not_launch_browser or workflow_enables_rendered"
```

Expected: failures because `pipeline.fetch_heatmap` does not exist and the
workflow has no rendered-browser environment variables.

- [ ] **Step 3: Add the controlled fallback and runner variables**

Add imports to `pipeline.py`:

```python
import browser_heatmap
```

Add this function after `fetch_watch_heatmap`:

```python
def fetch_heatmap(video_id):
    heatmap = fetch_watch_heatmap(video_id)
    if heatmap or os.getenv("USE_RENDERED_HEATMAP") != "1":
        return heatmap
    return browser_heatmap.fetch_rendered_heatmap(video_id, Path("cookies.txt"))
```

In `main()`, replace:

```python
heatmap = fetch_watch_heatmap(vid)
```

with:

```python
heatmap = fetch_heatmap(vid)
```

Add these environment values alongside the existing `Run pipeline` environment
values in `.github/workflows/daily.yml`:

```yaml
          USE_RENDERED_HEATMAP: "1"
          BRAVE_EXECUTABLE: /Applications/Brave Browser.app/Contents/MacOS/Brave Browser
```

Update README setup item 3 to state that runs create a visible temporary Brave
window seeded from the signed-in local Brave session, and that this temporary
profile and cookie export are removed after each job.

- [ ] **Step 4: Run focused and full tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_pipeline.py -q -k "fetch_heatmap or workflow_enables_rendered"
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile pipeline.py heatmaps.py browser_heatmap.py export_cookies.py
git diff --check
```

Expected: fallback behavior is covered without launching a browser; the full
suite and compilation pass with no whitespace errors.

- [ ] **Step 5: Commit integration and documentation**

```bash
git add pipeline.py .github/workflows/daily.yml README.md tests/test_pipeline.py
git commit -m "feat: fall back to rendered Brave heatmaps"
```

- [ ] **Step 6: Validate on the self-hosted runner**

Push the branch, leave the runner's `./run.sh` process running, and manually
dispatch `daily-short` with `dry_run=true`. Confirm a temporary Brave window
appears, the log records a rendered heatmap result for a candidate, and the
video upload remains private. Confirm the workflow's cleanup step removes
`cookies.txt` after the job.
