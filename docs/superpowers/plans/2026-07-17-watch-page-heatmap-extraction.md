# Watch-Page Heatmap Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract YouTube most-replayed markers from the cookie-authenticated watch page before downloading a candidate, so the pipeline can again choose a heatmap-based 35-second clip.

**Architecture:** Add a pure HTML parser that discovers embedded `heatMarkerRenderer` JSON objects and normalizes them to the existing heatmap contract. Add a small watch-page fetcher backed by the existing Netscape cookie file, then call it before `download()` in `main()`. Candidates without markers are skipped before media is downloaded.

**Tech Stack:** Python 3.12+, standard-library `http.cookiejar` and `urllib.request`, pytest, GitHub Actions.

Spec: `docs/superpowers/specs/2026-07-17-watch-page-heatmap-extraction-design.md`

---

## File structure

- `pipeline.py` — pure heatmap parsing, cookie-backed watch-page request, and reordered candidate flow.
- `tests/test_pipeline.py` — parser, request, skip-before-download, and existing orchestration regression tests.
- `README.md` — explains that the private-session cookie export supports both watch-page heatmap lookup and yt-dlp downloads.

### Task 1: Parse embedded heatmap markers

**Files:**
- Modify: `pipeline.py:1-55`
- Test: `tests/test_pipeline.py`

- [x] **Step 1: Write the failing parser tests**

Append these tests to `tests/test_pipeline.py`:

```python
def test_parse_watch_heatmap_normalizes_and_sorts_markers():
    html = '''
    <script>
      {"heatMarkerRenderer":{"timeRangeStartMillis":20000,
       "markerDurationMillis":5000,"heatMarkerIntensityScoreNormalized":0.4}}
      {"heatMarkerRenderer":{"timeRangeStartMillis":0,
       "markerDurationMillis":5000,"heatMarkerIntensityScoreNormalized":1.0}}
    </script>
    '''

    assert pipeline.parse_watch_heatmap(html) == [
        {"start_time": 0.0, "end_time": 5.0, "value": 1.0},
        {"start_time": 20.0, "end_time": 25.0, "value": 0.4},
    ]


def test_parse_watch_heatmap_ignores_invalid_and_duplicate_markers():
    html = '''
    {"heatMarkerRenderer":{"timeRangeStartMillis":0,
     "markerDurationMillis":5000,"heatMarkerIntensityScoreNormalized":0.7}}
    {"heatMarkerRenderer":{"timeRangeStartMillis":0,
     "markerDurationMillis":5000,"heatMarkerIntensityScoreNormalized":0.2}}
    {"heatMarkerRenderer":{"timeRangeStartMillis":1000,
     "markerDurationMillis":0,"heatMarkerIntensityScoreNormalized":0.9}}
    {"heatMarkerRenderer":{"timeRangeStartMillis":"bad",
     "markerDurationMillis":1000,"heatMarkerIntensityScoreNormalized":0.9}}
    '''

    assert pipeline.parse_watch_heatmap(html) == [
        {"start_time": 0.0, "end_time": 5.0, "value": 0.7},
    ]


def test_parse_watch_heatmap_returns_empty_for_pages_without_markers():
    assert pipeline.parse_watch_heatmap("<html>no replay data</html>") == []
```

- [x] **Step 2: Run the parser tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_pipeline.py -v -k parse_watch_heatmap
```

Expected: all three tests fail with `AttributeError` because
`pipeline.parse_watch_heatmap` does not yet exist.

- [x] **Step 3: Add the minimal pure parser**

Add `import math` near the other imports in `pipeline.py`, then add this below
`pick_window`:

```python
HEAT_MARKER_RE = re.compile(r'"heatMarkerRenderer"\s*:\s*')


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_watch_heatmap(html):
    """Extract and normalize embedded YouTube most-replayed marker objects."""
    decoder = json.JSONDecoder()
    markers = {}
    for match in HEAT_MARKER_RE.finditer(html):
        try:
            marker, _ = decoder.raw_decode(html[match.end():])
        except json.JSONDecodeError:
            continue
        start_ms = marker.get("timeRangeStartMillis")
        duration_ms = marker.get("markerDurationMillis")
        value = marker.get("heatMarkerIntensityScoreNormalized")
        if not all(_number(x) for x in (start_ms, duration_ms, value)):
            continue
        if duration_ms <= 0 or not math.isfinite(value):
            continue
        start = float(start_ms) / 1000
        end = float(start_ms + duration_ms) / 1000
        if start < 0 or end <= start:
            continue
        markers.setdefault((start, end), {
            "start_time": start, "end_time": end, "value": float(value),
        })
    return [markers[key] for key in sorted(markers)]
```

The `setdefault` deliberately keeps the first occurrence for duplicate time
ranges. Do not add a dependency or parse a fixed `ytInitialPlayerResponse`
path; `heatMarkerRenderer` may move within YouTube's page payload.

- [x] **Step 4: Run the parser tests to verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_pipeline.py -v -k parse_watch_heatmap
```

Expected: 3 passed.

- [x] **Step 5: Commit the parser**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat: parse watch-page heatmap markers"
```

### Task 2: Fetch watch-page data and skip before download

**Files:**
- Modify: `pipeline.py:1-230`
- Modify: `tests/test_pipeline.py`

- [x] **Step 1: Write failing watch-page and orchestration tests**

Append these tests to `tests/test_pipeline.py`:

```python
def test_fetch_watch_heatmap_uses_watch_url_and_parses_response(monkeypatch):
    requested = []

    class Response:
        def read(self):
            return (b'{"heatMarkerRenderer":{"timeRangeStartMillis":0,'
                    b'"markerDurationMillis":5000,'
                    b'"heatMarkerIntensityScoreNormalized":1.0}}')

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Opener:
        def open(self, request, timeout):
            requested.append((request.full_url, timeout))
            return Response()

    monkeypatch.setattr(pipeline, "build_opener", lambda *_handlers: Opener())

    heatmap = pipeline.fetch_watch_heatmap("abc123")

    assert requested == [("https://www.youtube.com/watch?v=abc123", 20)]
    assert heatmap == [{"start_time": 0.0, "end_time": 5.0, "value": 1.0}]


def test_main_skips_no_heatmap_before_downloading(tmp_path, monkeypatch):
    used_file = tmp_path / "used_videos.json"
    used_file.write_text("[]")
    first, second = _vid("first", "PT10M"), _vid("second", "PT10M")
    downloaded = []

    monkeypatch.setattr(pipeline, "USED_FILE", used_file)
    monkeypatch.setattr(pipeline, "LOG_FILE", tmp_path / "log.jsonl")
    monkeypatch.setattr(pipeline, "fetch_trending", lambda _key: [first, second])
    monkeypatch.setattr(pipeline, "fetch_watch_heatmap", lambda vid: [] if vid == "first" else [
        {"start_time": 0.0, "end_time": 10.0, "value": 1.0},
    ])
    monkeypatch.setattr(pipeline, "download", lambda vid: (downloaded.append(vid) or tmp_path / f"{vid}.mp4", {}))
    monkeypatch.setattr(pipeline, "gen_metadata", lambda *_args: {
        "title": "title", "description": "description", "hashtags": [], "hook": "hook",
    })
    monkeypatch.setattr(pipeline, "edit", lambda *_args: None)
    monkeypatch.setattr(pipeline, "self_check", lambda _path: None)
    monkeypatch.setattr(pipeline, "upload", lambda *_args: "upload-id")
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "--dry-run"])
    monkeypatch.setenv("YT_API_KEY", "test-key")

    pipeline.main()

    assert downloaded == ["second"]
```

In the existing `test_bookkeeping_failure_after_upload_does_not_try_next_candidate`,
add this monkeypatch next to the existing `fetch_trending` monkeypatch:

```python
monkeypatch.setattr(pipeline, "fetch_watch_heatmap", lambda _vid: [
    {"start_time": 0, "end_time": 10, "value": 1},
])
```

- [x] **Step 2: Run the new tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_pipeline.py -v -k "fetch_watch_heatmap or skips_no_heatmap"
```

Expected: failure with `AttributeError` because
`pipeline.fetch_watch_heatmap` does not yet exist.

- [x] **Step 3: Add the cookie-backed watch-page fetcher**

Add these imports in `pipeline.py`:

```python
from http.cookiejar import MozillaCookieJar
from urllib.request import HTTPCookieProcessor, Request, build_opener
```

Add this constant and function below `parse_watch_heatmap`:

```python
WATCH_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


def fetch_watch_heatmap(video_id):
    """Return most-replayed markers embedded in a cookie-authenticated watch page."""
    jar = MozillaCookieJar()
    cookie_file = Path("cookies.txt")
    if cookie_file.exists():
        jar = MozillaCookieJar(str(cookie_file))
        jar.load(ignore_discard=True, ignore_expires=True)
    opener = build_opener(HTTPCookieProcessor(jar))
    request = Request(
        f"https://www.youtube.com/watch?v={video_id}",
        headers={"User-Agent": WATCH_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
    )
    with opener.open(request, timeout=20) as response:
        html = response.read().decode("utf-8", errors="replace")
    return parse_watch_heatmap(html)
```

- [x] **Step 4: Reorder candidate processing to fetch markers before download**

Inside the candidate `try:` block in `main()`, replace the first three lines:

```python
src, info = download(vid)
heatmap = info.get("heatmap")
if not heatmap:
    raise ValueError("no heatmap")
start = pick_window(heatmap, info["duration"])
```

with:

```python
heatmap = fetch_watch_heatmap(vid)
if not heatmap:
    raise ValueError("no heatmap")
duration = parse_duration(video["contentDetails"]["duration"])
start = pick_window(heatmap, duration)
src, _info = download(vid)
```

Keep `download()` returning `(path, info)` for now; do not refactor its public
shape as part of this change.

- [x] **Step 5: Run focused and full tests to verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_pipeline.py -v -k "watch_heatmap or skips_no_heatmap or bookkeeping"
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m py_compile pipeline.py
```

Expected: focused tests pass; the full suite passes; compilation emits no
output.

- [x] **Step 6: Commit the fetcher and flow change**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat: extract heatmaps from YouTube watch pages"
```

### Task 3: Document the new heatmap source

**Files:**
- Modify: `README.md`

- [x] **Step 1: Update the cookie setup description**

Replace README setup item 3 with:

```markdown
3. Export YouTube cookies from a private/incognito YouTube session using a
   browser extension that writes Netscape `cookies.txt` format. The pipeline
   uses them for both the watch-page most-replayed lookup and yt-dlp downloads.
   Re-export when either stage starts failing.
```

- [x] **Step 2: Verify documentation and repository state**

Run:

```bash
git diff --check
.venv/bin/python -m pytest tests/ -q
```

Expected: no whitespace errors; all tests pass.

- [x] **Step 3: Commit the documentation**

```bash
git add README.md
git commit -m "docs: explain watch-page heatmap cookies"
```

---

## Plan self-review

- Spec coverage: Task 1 implements resilient marker parsing and normalization;
  Task 2 implements cookie-backed watch-page retrieval, pre-download skipping,
  and failure propagation; Task 3 documents the cookie requirement. PO tokens,
  player API calls, browser automation, and non-heatmap fallback remain absent.
- Placeholder scan: no unresolved placeholders or undefined helper names remain.
- Type consistency: `parse_watch_heatmap()` returns the exact marker dictionary
  shape already consumed by `pick_window()`; `fetch_watch_heatmap()` returns
  that list; `main()` gets duration from the existing `contentDetails` value.
