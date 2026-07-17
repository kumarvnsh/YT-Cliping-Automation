# Manual Clip Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual mode that builds a Short from a user-supplied YouTube URL and start timestamp, alongside the untouched trending pipeline.

**Architecture:** Extend `pipeline.py` with two pure parsers (`parse_video_id`, `parse_timestamp`), a `run_manual` function that reuses the existing `download`/`gen_metadata`/`edit`/`self_check`/`upload` helpers, and a `main()` that dispatches to manual mode when `--url` is passed (otherwise the existing trending loop, now factored into `run_trending`). A new `clip.yml` `workflow_dispatch` mirrors `daily.yml`'s environment.

**Tech Stack:** Python 3.14, pytest, yt-dlp, ffmpeg, GitHub Actions (self-hosted macOS runner).

---

### Task 1: `parse_video_id`

**Files:**
- Modify: `pipeline.py` (add helper near `parse_duration`, ~line 32)
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py`:

```python
def test_parse_video_id_handles_all_url_forms():
    assert pipeline.parse_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert pipeline.parse_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert pipeline.parse_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert pipeline.parse_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s") == "dQw4w9WgXcQ"


def test_parse_video_id_rejects_junk():
    with pytest.raises(ValueError):
        pipeline.parse_video_id("not a url")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py::test_parse_video_id_handles_all_url_forms -v`
Expected: FAIL with `AttributeError: module 'pipeline' has no attribute 'parse_video_id'`

- [ ] **Step 3: Write minimal implementation**

Add to `pipeline.py` after `parse_duration` (after line 35):

```python
_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})")


def parse_video_id(url):
    """Extract the 11-char video id from watch?v=, youtu.be/, or /shorts/ URLs."""
    m = _VIDEO_ID_RE.search(url)
    if not m:
        raise ValueError(f"cannot parse video id from url: {url!r}")
    return m.group(1)
```

`re` is already imported at the top of `pipeline.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -k parse_video_id -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat: add parse_video_id for manual clip mode"
```

---

### Task 2: `parse_timestamp`

**Files:**
- Modify: `pipeline.py` (add helper after `parse_video_id`)
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py`:

```python
def test_parse_timestamp_handles_all_formats():
    assert pipeline.parse_timestamp("90") == 90
    assert pipeline.parse_timestamp("1:30") == 90
    assert pipeline.parse_timestamp("1:02:03") == 3723


@pytest.mark.parametrize("bad", ["", "-5", "1:2:3:4", "abc", "1:xx"])
def test_parse_timestamp_rejects_junk(bad):
    with pytest.raises(ValueError):
        pipeline.parse_timestamp(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py::test_parse_timestamp_handles_all_formats -v`
Expected: FAIL with `AttributeError: module 'pipeline' has no attribute 'parse_timestamp'`

- [ ] **Step 3: Write minimal implementation**

Add to `pipeline.py` after `parse_video_id`:

```python
def parse_timestamp(ts):
    """Parse SS, MM:SS, or HH:MM:SS into seconds. Rejects negatives and junk."""
    parts = ts.strip().split(":")
    if len(parts) > 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"bad timestamp: {ts!r}")
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + int(p)
    return seconds
```

Note: `str.isdigit()` is False for `""`, `"-5"`, and non-numeric text, so those raise.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -k parse_timestamp -v`
Expected: PASS (6 tests: 1 format test + 5 parametrized junk cases)

- [ ] **Step 5: Commit**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat: add parse_timestamp for manual clip mode"
```

---

### Task 3: `run_manual` and `main()` dispatch

**Files:**
- Modify: `pipeline.py` — add `run_manual`, factor the trending loop into `run_trending`, rewrite `main()` (current `main()` at lines 282-327)
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pipeline.py`:

```python
def test_run_manual_clips_at_parsed_start_and_uploads_once(tmp_path, monkeypatch):
    edits = []
    uploads = []
    logged = []

    monkeypatch.setattr(pipeline, "WORK", tmp_path)
    monkeypatch.setattr(pipeline, "download", lambda vid: (
        tmp_path / f"{vid}.mp4", {"title": "T", "uploader": "Chan", "duration": 600}))
    monkeypatch.setattr(pipeline, "gen_metadata", lambda *_a: {
        "title": "T", "description": "D", "hashtags": [], "hook": "HOOK"})
    monkeypatch.setattr(pipeline, "edit",
                        lambda src, start, hook, credit, out: edits.append((start, hook, credit)))
    monkeypatch.setattr(pipeline, "self_check", lambda _p: None)
    monkeypatch.setattr(pipeline, "upload", lambda path, meta, privacy: uploads.append(privacy) or "id")
    monkeypatch.setattr(pipeline, "log_run", lambda entry: logged.append(entry))

    pipeline.run_manual("https://youtu.be/dQw4w9WgXcQ", 120, "public")

    assert edits == [(120, "HOOK", "Credit: Chan")]
    assert uploads == ["public"]
    assert logged[-1]["method"] == "manual"


def test_run_manual_clamps_start_past_end(tmp_path, monkeypatch):
    edits = []

    monkeypatch.setattr(pipeline, "WORK", tmp_path)
    monkeypatch.setattr(pipeline, "download", lambda vid: (
        tmp_path / f"{vid}.mp4", {"title": "T", "uploader": "Chan", "duration": 100}))
    monkeypatch.setattr(pipeline, "gen_metadata", lambda *_a: {
        "title": "T", "description": "D", "hashtags": [], "hook": "H"})
    monkeypatch.setattr(pipeline, "edit",
                        lambda src, start, hook, credit, out: edits.append(start))
    monkeypatch.setattr(pipeline, "self_check", lambda _p: None)
    monkeypatch.setattr(pipeline, "upload", lambda *_a: "id")
    monkeypatch.setattr(pipeline, "log_run", lambda _e: None)

    # start 90 in a 100s source: clamp to duration - CLIP_SECONDS = 65
    pipeline.run_manual("https://youtu.be/dQw4w9WgXcQ", 90, "public")

    assert edits == [65]


def test_run_manual_does_not_touch_used_videos(tmp_path, monkeypatch):
    used_file = tmp_path / "used_videos.json"
    used_file.write_text("[]")

    monkeypatch.setattr(pipeline, "WORK", tmp_path)
    monkeypatch.setattr(pipeline, "USED_FILE", used_file)
    monkeypatch.setattr(pipeline, "download", lambda vid: (
        tmp_path / f"{vid}.mp4", {"title": "T", "uploader": "Chan", "duration": 600}))
    monkeypatch.setattr(pipeline, "gen_metadata", lambda *_a: {
        "title": "T", "description": "D", "hashtags": [], "hook": "H"})
    monkeypatch.setattr(pipeline, "edit", lambda *_a: None)
    monkeypatch.setattr(pipeline, "self_check", lambda _p: None)
    monkeypatch.setattr(pipeline, "upload", lambda *_a: "id")
    monkeypatch.setattr(pipeline, "log_run", lambda _e: None)

    pipeline.run_manual("https://youtu.be/dQw4w9WgXcQ", 10, "public")

    assert used_file.read_text() == "[]"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -k run_manual -v`
Expected: FAIL with `AttributeError: module 'pipeline' has no attribute 'run_manual'`

- [ ] **Step 3: Add `run_manual` implementation**

Add to `pipeline.py` immediately before `def main():`:

```python
def run_manual(url, start, privacy):
    """Build and upload one Short from a user-supplied URL and start second."""
    video_id = parse_video_id(url)
    src, info = download(video_id)
    duration = info.get("duration") or 0
    start = min(max(0, start), max(0, duration - CLIP_SECONDS))
    channel = info.get("uploader", "")
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    meta = gen_metadata(info["title"], channel, canonical)
    out = WORK / f"short_{video_id}.mp4"
    edit(src, start, meta["hook"], f"Credit: {channel}", out)
    self_check(out)
    uploaded_id = upload(out, meta, privacy)
    log_run({"source": video_id, "uploaded": uploaded_id,
             "title": meta["title"], "privacy": privacy, "method": "manual"})
    print(f"Uploaded https://www.youtube.com/shorts/{uploaded_id}")
```

- [ ] **Step 4: Factor the trending loop into `run_trending` and rewrite `main()`**

Replace the entire current `main()` (lines 282-327, from `def main():` through the final `sys.exit(...)`) with:

```python
def run_trending(privacy):
    used = set(json.loads(USED_FILE.read_text()))
    candidates = eligible(fetch_trending(os.environ["YT_API_KEY"]), used)
    errors = []
    for video in candidates[:MAX_ATTEMPTS]:
        vid = video["id"]
        try:
            heatmap = fetch_heatmap(vid)
            duration = parse_duration(video["contentDetails"]["duration"])
            src, _info = download(vid)
            if heatmap:
                start, method = pick_window(heatmap, duration), "heatmap"
            else:
                start, method = motion.pick_motion_window(
                    motion_scores(src), duration, CLIP_SECONDS)
            title = video["snippet"]["title"]
            channel = video["snippet"]["channelTitle"]
            url = f"https://www.youtube.com/watch?v={vid}"
            meta = gen_metadata(title, channel, url)
            out = WORK / f"short_{vid}.mp4"
            edit(src, start, meta["hook"], f"Credit: {channel}", out)
            self_check(out)
            uploaded_id = upload(out, meta, privacy)
        except Exception as e:
            # broad catch is deliberate: any per-candidate failure -> try next one
            print(f"candidate {vid} failed: {e}", file=sys.stderr)
            errors.append(f"{vid}: {e}")
            continue
        # Do not retry another candidate after upload: this only prevents duplicates
        # within this invocation, not exactly-once delivery across future runs.
        used.add(vid)
        atomic_write_json(USED_FILE, sorted(used))
        log_run({"source": vid, "uploaded": uploaded_id,
                 "title": meta["title"], "privacy": privacy, "method": method})
        print(f"Uploaded https://www.youtube.com/shorts/{uploaded_id}")
        return
    log_run({"failed": errors})
    sys.exit(f"all {MAX_ATTEMPTS} candidates failed: {errors}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="upload as private instead of public")
    ap.add_argument("--url", help="manual mode: YouTube URL to clip from")
    ap.add_argument("--start",
                    help="manual mode: start timestamp (SS, MM:SS, or HH:MM:SS)")
    args = ap.parse_args()
    privacy = "private" if args.dry_run else "public"

    if args.url:
        if not args.start:
            sys.exit("--start is required with --url")
        run_manual(args.url, parse_timestamp(args.start), privacy)
        return
    run_trending(privacy)
```

- [ ] **Step 5: Run the full suite to verify manual tests pass and trending tests still pass**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all prior tests plus 3 new `run_manual` tests). The existing
`test_main_*` and `test_bookkeeping_*` tests still pass because `main()` with no
`--url` calls `run_trending`, which is the unchanged loop.

- [ ] **Step 6: Commit**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat: add manual clip mode (--url/--start) to pipeline"
```

---

### Task 4: `clip.yml` dispatch workflow

**Files:**
- Create: `.github/workflows/clip.yml`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py`:

```python
def test_clip_workflow_is_manual_dispatch_on_mac_runner():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/clip.yml").read_text()

    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "url:" in workflow and "start:" in workflow
    assert "runs-on: [self-hosted, macOS, yt-shorts]" in workflow
    assert 'pipeline.py --url' in workflow
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py::test_clip_workflow_is_manual_dispatch_on_mac_runner -v`
Expected: FAIL with `FileNotFoundError: .../.github/workflows/clip.yml`

- [ ] **Step 3: Create the workflow**

Create `.github/workflows/clip.yml`:

```yaml
name: clip
concurrency:
  group: daily-short
  cancel-in-progress: false

on:
  workflow_dispatch:
    inputs:
      url:
        description: YouTube URL to clip from
        type: string
        required: true
      start:
        description: Start timestamp (SS, MM:SS, or HH:MM:SS)
        type: string
        required: true
      dry_run:
        type: boolean
        default: false

permissions:
  contents: read

jobs:
  run:
    runs-on: [self-hosted, macOS, yt-shorts]
    steps:
      - uses: actions/checkout@v4
      - name: Create local Python environment
        run: |
          python3 --version
          python3 -m venv .venv
      - uses: denoland/setup-deno@v2
        with:
          deno-version: v2.3.0
      - name: Verify Mac runner dependencies
        run: |
          export PATH="/opt/homebrew/opt/ffmpeg-full/bin:$PATH"
          echo "/opt/homebrew/opt/ffmpeg-full/bin" >> "$GITHUB_PATH"
          command -v ffmpeg
          ffmpeg -hide_banner -filters 2>&1 | grep -q drawtext
      - run: .venv/bin/python -m pip install -r requirements.txt
      - name: Export local Brave cookies
        run: .venv/bin/python export_cookies.py
      - name: Run manual clip
        run: |
          if [ "${{ inputs.dry_run }}" = "true" ]; then
            .venv/bin/python pipeline.py --url "${{ inputs.url }}" --start "${{ inputs.start }}" --dry-run
          else
            .venv/bin/python pipeline.py --url "${{ inputs.url }}" --start "${{ inputs.start }}"
          fi
        env:
          YT_API_KEY: ${{ secrets.YT_API_KEY }}
          YT_CLIENT_ID: ${{ secrets.YT_CLIENT_ID }}
          YT_CLIENT_SECRET: ${{ secrets.YT_CLIENT_SECRET }}
          YT_REFRESH_TOKEN: ${{ secrets.YT_REFRESH_TOKEN }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          FFMPEG_FONT: Arial
          USE_RENDERED_HEATMAP: "1"
          BRAVE_EXECUTABLE: /Applications/Brave Browser.app/Contents/MacOS/Brave Browser
      - name: Remove exported cookies
        if: always()
        run: rm -f cookies.txt
```

Notes: `concurrency.group` matches `daily.yml` so a manual clip and the daily
run never fight over the single self-hosted runner. There is no "commit state"
step because manual mode writes no `used_videos.json`. `USE_RENDERED_HEATMAP`
and `BRAVE_EXECUTABLE` are unused by manual mode but kept so the env block is
identical to `daily.yml` and the cookie export step works.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py::test_clip_workflow_is_manual_dispatch_on_mac_runner -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/clip.yml tests/test_pipeline.py
git commit -m "feat: add clip.yml manual-dispatch workflow"
```

---

## Final verification

- [ ] Run the whole suite: `.venv/bin/python -m pytest -q` — all green.
- [ ] Sanity-check the parsers without network:

```bash
.venv/bin/python -c "import pipeline; print(pipeline.parse_video_id('https://youtu.be/dQw4w9WgXcQ'), pipeline.parse_timestamp('1:02:03'))"
```

Expected: `dQw4w9WgXcQ 3723`
