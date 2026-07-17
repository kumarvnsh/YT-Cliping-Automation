# YT Gaming Shorts Auto-Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Daily GitHub Actions job that finds a trending gaming video, cuts its most-replayed 35s into a 9:16 short with hook/credit text, generates metadata via OpenAI (Claude fallback), and uploads it to the user's YouTube channel.

**Architecture:** One flat `pipeline.py` of small pure-ish functions orchestrated by `main()`. Repo files are the database (`used_videos.json`, `log.jsonl`). GH Actions cron runs it and commits state back. `get_token.py` is a one-time local OAuth helper.

**Tech Stack:** Python 3.12, yt-dlp, ffmpeg, google-api-python-client, openai, anthropic, pytest.

Spec: `docs/superpowers/specs/2026-07-17-yt-gaming-shorts-pipeline-design.md`

---

### Task 1: Scaffold

**Files:**
- Create: `requirements.txt`, `.gitignore`, `used_videos.json`, `tests/__init__.py`

- [ ] **Step 1: Write files**

`requirements.txt`:
```
yt-dlp
google-api-python-client
google-auth
google-auth-oauthlib
openai
anthropic
pytest
```

`.gitignore`:
```
__pycache__/
work/
cookies.txt
*.mp4
```

`used_videos.json`:
```json
[]
```

`tests/__init__.py`: empty file.

- [ ] **Step 2: Verify deps install**

Run: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -q && .venv/bin/pytest --version`
Expected: pytest version prints. Add `.venv/` to `.gitignore`.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt .gitignore used_videos.json tests/__init__.py
git commit -m "chore: scaffold pipeline project"
```

---

### Task 2: Trending fetch, duration parse, dedupe filter

**Files:**
- Create: `pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing tests**

`tests/test_pipeline.py`:
```python
import pipeline


def _vid(vid_id, duration):
    return {"id": vid_id,
            "snippet": {"title": "t", "channelTitle": "c"},
            "contentDetails": {"duration": duration}}


def test_parse_duration():
    assert pipeline.parse_duration("PT1H2M3S") == 3723
    assert pipeline.parse_duration("PT45S") == 45
    assert pipeline.parse_duration("PT10M") == 600


def test_eligible_filters_used_and_short():
    items = [_vid("a", "PT10M"), _vid("b", "PT1M30S"), _vid("c", "PT5M")]
    out = pipeline.eligible(items, used={"c"})
    assert [v["id"] for v in out] == ["a"]
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/pytest tests/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline'`.

- [ ] **Step 3: Implement**

`pipeline.py`:
```python
"""Daily YT gaming shorts pipeline. See docs/superpowers/specs/ for design."""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CLIP_SECONDS = 35
MIN_SOURCE_SECONDS = 120
MAX_ATTEMPTS = 3
USED_FILE = Path("used_videos.json")
LOG_FILE = Path("log.jsonl")
WORK = Path("work")


def parse_duration(iso: str) -> int:
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    h, mn, s = (int(g or 0) for g in m.groups())
    return h * 3600 + mn * 60 + s


def eligible(items, used):
    return [v for v in items
            if v["id"] not in used
            and parse_duration(v["contentDetails"]["duration"]) >= MIN_SOURCE_SECONDS]


def fetch_trending(api_key):
    from googleapiclient.discovery import build
    yt = build("youtube", "v3", developerKey=api_key)
    resp = yt.videos().list(part="snippet,contentDetails", chart="mostPopular",
                            videoCategoryId="20", regionCode="US",
                            maxResults=25).execute()
    return resp["items"]
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/ -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat: trending fetch, duration parse, dedupe filter"
```

---

### Task 3: Clip window pick from heatmap

**Files:**
- Modify: `pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_pipeline.py`:
```python
def test_pick_window_finds_hot_region():
    # 200s video, heat spike at 100-140s
    heatmap = [{"start_time": float(t), "end_time": float(t + 10),
                "value": 1.0 if 100 <= t < 140 else 0.1}
               for t in range(0, 200, 10)]
    start = pipeline.pick_window(heatmap, duration=200)
    assert 90 <= start <= 110


def test_pick_window_clamps_to_video_end():
    heatmap = [{"start_time": 190.0, "end_time": 200.0, "value": 1.0},
               {"start_time": 0.0, "end_time": 10.0, "value": 0.1}]
    start = pipeline.pick_window(heatmap, duration=200)
    assert start <= 200 - pipeline.CLIP_SECONDS
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/pytest tests/ -v -k pick_window`
Expected: FAIL — `AttributeError: module 'pipeline' has no attribute 'pick_window'`.

- [ ] **Step 3: Implement**

Add to `pipeline.py` after `fetch_trending`:
```python
def pick_window(heatmap, duration, clip=CLIP_SECONDS):
    """Return clip start second: window with highest summed most-replayed value.

    heatmap: yt-dlp info-JSON entries {start_time, end_time, value}.
    ponytail: O(n^2) scan; heatmaps are ~100 entries, fine forever.
    """
    best_start, best_score = 0.0, -1.0
    for h in heatmap:
        start = min(h["start_time"], max(0.0, duration - clip))
        end = start + clip
        score = sum(x["value"] for x in heatmap
                    if x["start_time"] < end and x["end_time"] > start)
        if score > best_score:
            best_score, best_start = score, start
    return best_start
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/ -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat: pick clip window from most-replayed heatmap"
```

---

### Task 4: Metadata — OpenAI primary, Claude fallback

**Files:**
- Modify: `pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_pipeline.py`:
```python
def test_gen_metadata_uses_openai(monkeypatch):
    meta = {"title": "T", "description": "D", "hashtags": ["#shorts"], "hook": "H"}
    monkeypatch.setattr(pipeline, "_openai_meta", lambda p: meta)
    monkeypatch.setattr(pipeline, "_claude_meta",
                        lambda p: (_ for _ in ()).throw(AssertionError("fallback used")))
    assert pipeline.gen_metadata("src", "chan", "http://u")["title"] == "T"


def test_gen_metadata_falls_back_to_claude(monkeypatch):
    meta = {"title": "T2", "description": "D", "hashtags": [], "hook": "H"}
    def boom(p):
        raise RuntimeError("openai down")
    monkeypatch.setattr(pipeline, "_openai_meta", boom)
    monkeypatch.setattr(pipeline, "_claude_meta", lambda p: meta)
    assert pipeline.gen_metadata("src", "chan", "http://u")["title"] == "T2"
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/pytest tests/ -v -k metadata`
Expected: FAIL — no attribute `_openai_meta`.

- [ ] **Step 3: Implement**

Add to `pipeline.py`:
```python
META_SYSTEM = (
    "You write YouTube Shorts metadata for gaming clip videos. "
    "Respond with ONLY a JSON object with keys: "
    "title (string, <100 chars, hooky), "
    "description (string, MUST include the provided source credit line), "
    "hashtags (list of strings incl. #shorts #gaming), "
    "hook (string, <40 chars, on-screen text)."
)


def _prompt(src_title, src_channel, src_url):
    return (f'Source video: "{src_title}" by {src_channel}.\n'
            f'Source credit line to include in description: '
            f'"Credit: {src_channel} — {src_url}"')


def _openai_meta(prompt):
    from openai import OpenAI
    r = OpenAI().chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": META_SYSTEM},
                  {"role": "user", "content": prompt}])
    return json.loads(r.choices[0].message.content)


def _claude_meta(prompt):
    import anthropic
    r = anthropic.Anthropic().messages.create(
        model="claude-haiku-4-5", max_tokens=1000,
        system=META_SYSTEM,
        messages=[{"role": "user", "content": prompt}])
    return json.loads(r.content[0].text)


def gen_metadata(src_title, src_channel, src_url):
    prompt = _prompt(src_title, src_channel, src_url)
    try:
        meta = _openai_meta(prompt)
    except Exception as e:
        print(f"OpenAI failed ({e}); falling back to Claude", file=sys.stderr)
        meta = _claude_meta(prompt)
    for key in ("title", "description", "hashtags", "hook"):
        if key not in meta:
            raise ValueError(f"metadata missing {key}: {meta}")
    return meta
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/ -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat: metadata via OpenAI with Claude fallback"
```

---

### Task 5: Download + ffmpeg edit + output self-check

**Files:**
- Modify: `pipeline.py`

No unit tests: subprocess/network glue. Verified end-to-end by `--dry-run`
self-check (Task 6) which asserts output duration and resolution.

- [ ] **Step 1: Implement download**

Add to `pipeline.py`:
```python
def download(video_id):
    """Download source at <=720p; return (video path, info dict)."""
    import yt_dlp
    WORK.mkdir(exist_ok=True)
    out = WORK / f"{video_id}.mp4"
    opts = {"format": "bv*[height<=720]+ba/b[height<=720]",
            "outtmpl": str(out),
            "merge_output_format": "mp4",
            "quiet": True}
    if Path("cookies.txt").exists():
        opts["cookiefile"] = "cookies.txt"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}")
    return out, info
```

- [ ] **Step 2: Implement edit + self-check**

Add to `pipeline.py`:
```python
def _drawtext_escape(text):
    return text.replace("\\", "\\\\").replace("'", "’").replace(":", "\\:").replace("%", "\\%")


def edit(src, start, hook, credit, out):
    hook_e, credit_e = _drawtext_escape(hook), _drawtext_escape(credit)
    vf = ("crop=ih*9/16:ih,scale=1080:1920,"
          f"drawtext=text='{hook_e}':font=DejaVuSans:fontsize=64:fontcolor=white:"
          "borderw=4:bordercolor=black:x=(w-text_w)/2:y=200,"
          f"drawtext=text='{credit_e}':font=DejaVuSans:fontsize=36:fontcolor=white:"
          "borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-250")
    subprocess.run(["ffmpeg", "-y", "-ss", str(start), "-i", str(src),
                    "-t", str(CLIP_SECONDS), "-vf", vf,
                    "-c:v", "libx264", "-preset", "veryfast",
                    "-c:a", "aac", str(out)],
                   check=True, capture_output=True)


def self_check(path):
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True)
    data = json.loads(probe.stdout)
    w, h = data["streams"][0]["width"], data["streams"][0]["height"]
    dur = float(data["format"]["duration"])
    assert (w, h) == (1080, 1920), f"bad resolution {w}x{h}"
    assert abs(dur - CLIP_SECONDS) < 2, f"bad duration {dur}"
```

- [ ] **Step 3: Sanity-run existing tests (no regressions)**

Run: `.venv/bin/pytest tests/ -v`
Expected: 6 PASS.

- [ ] **Step 4: Commit**

```bash
git add pipeline.py
git commit -m "feat: download, ffmpeg vertical edit, output self-check"
```

---

### Task 6: Upload + main orchestration + --dry-run

**Files:**
- Modify: `pipeline.py`

- [ ] **Step 1: Implement upload**

Add to `pipeline.py`:
```python
def upload(path, meta, privacy):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"])
    yt = build("youtube", "v3", credentials=creds)
    body = {"snippet": {"title": meta["title"][:100],
                        "description": meta["description"],
                        "tags": [t.lstrip("#") for t in meta["hashtags"]],
                        "categoryId": "20"},
            "status": {"privacyStatus": privacy,
                       "selfDeclaredMadeForKids": False}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(path), resumable=True))
    return req.execute()["id"]
```

- [ ] **Step 2: Implement main**

Add to `pipeline.py`:
```python
def log_run(entry):
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="upload as private instead of public")
    args = ap.parse_args()
    privacy = "private" if args.dry_run else "public"

    used = set(json.loads(USED_FILE.read_text()))
    candidates = eligible(fetch_trending(os.environ["YT_API_KEY"]), used)
    errors = []
    for video in candidates[:MAX_ATTEMPTS]:
        vid = video["id"]
        try:
            src, info = download(vid)
            heatmap = info.get("heatmap")
            if not heatmap:
                raise ValueError("no heatmap")
            start = pick_window(heatmap, info["duration"])
            title = video["snippet"]["title"]
            channel = video["snippet"]["channelTitle"]
            url = f"https://www.youtube.com/watch?v={vid}"
            meta = gen_metadata(title, channel, url)
            out = WORK / f"short_{vid}.mp4"
            edit(src, start, meta["hook"], f"Credit: {channel}", out)
            self_check(out)
            uploaded_id = upload(out, meta, privacy)
            used.add(vid)
            USED_FILE.write_text(json.dumps(sorted(used), indent=0))
            log_run({"source": vid, "uploaded": uploaded_id,
                     "title": meta["title"], "privacy": privacy})
            print(f"Uploaded https://www.youtube.com/shorts/{uploaded_id}")
            return
        except Exception as e:
            print(f"candidate {vid} failed: {e}", file=sys.stderr)
            errors.append(f"{vid}: {e}")
    log_run({"failed": errors})
    sys.exit(f"all {MAX_ATTEMPTS} candidates failed: {errors}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run all tests + syntax check**

Run: `.venv/bin/pytest tests/ -v && .venv/bin/python -c "import pipeline"`
Expected: 6 PASS, clean import.

- [ ] **Step 4: Commit**

```bash
git add pipeline.py
git commit -m "feat: upload and main orchestration with dry-run"
```

---

### Task 7: get_token.py (one-time OAuth)

**Files:**
- Create: `get_token.py`

- [ ] **Step 1: Implement**

`get_token.py`:
```python
"""One-time local run: prints the YouTube refresh token for GH secrets.

Usage: YT_CLIENT_ID=... YT_CLIENT_SECRET=... python get_token.py
Prereq: OAuth client of type 'Desktop app' in Google Cloud Console,
YouTube Data API v3 enabled.
"""
import os
from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_config(
    {"installed": {
        "client_id": os.environ["YT_CLIENT_ID"],
        "client_secret": os.environ["YT_CLIENT_SECRET"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"}},
    scopes=["https://www.googleapis.com/auth/youtube.upload"])
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
print("\nYT_REFRESH_TOKEN =", creds.refresh_token)
```

- [ ] **Step 2: Syntax check**

Run: `.venv/bin/python -c "import ast; ast.parse(open('get_token.py').read())"`
Expected: no output. (Don't run the flow itself — user does that once, interactively.)

- [ ] **Step 3: Commit**

```bash
git add get_token.py
git commit -m "feat: one-time OAuth refresh token helper"
```

---

### Task 8: GitHub Actions workflow + README

**Files:**
- Create: `.github/workflows/daily.yml`, `README.md`

- [ ] **Step 1: Write workflow**

`.github/workflows/daily.yml`:
```yaml
name: daily-short
on:
  schedule:
    - cron: "30 17 * * *"   # 17:30 UTC = 11:00 PM IST daily
  workflow_dispatch:
    inputs:
      dry_run:
        type: boolean
        default: true

permissions:
  contents: write

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: sudo apt-get update && sudo apt-get install -y ffmpeg fonts-dejavu
      - run: pip install -r requirements.txt
      - name: Write cookies
        run: printf '%s' "$YT_COOKIES" > cookies.txt
        env:
          YT_COOKIES: ${{ secrets.YT_COOKIES }}
      - name: Run pipeline
        run: |
          if [ "${{ inputs.dry_run }}" = "true" ]; then
            python pipeline.py --dry-run
          else
            python pipeline.py
          fi
        env:
          YT_API_KEY: ${{ secrets.YT_API_KEY }}
          YT_CLIENT_ID: ${{ secrets.YT_CLIENT_ID }}
          YT_CLIENT_SECRET: ${{ secrets.YT_CLIENT_SECRET }}
          YT_REFRESH_TOKEN: ${{ secrets.YT_REFRESH_TOKEN }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - name: Commit state
        if: always()
        run: |
          git config user.name "pipeline-bot"
          git config user.email "bot@users.noreply.github.com"
          git add used_videos.json log.jsonl
          git diff --cached --quiet || git commit -m "state: daily run"
          git push
```

Note: scheduled runs get `inputs.dry_run` empty → else-branch → public. Manual
runs default to dry-run. Intended.

- [ ] **Step 2: Write README**

`README.md`:
```markdown
# yt-auto-animation — gaming shorts pipeline

Daily job: trending gaming video → most-replayed 35s → 9:16 short with
hook/credit text → upload. Spec + plan in `docs/superpowers/`.

## One-time setup

1. Google Cloud Console: create project, enable **YouTube Data API v3**,
   create an **API key** and an OAuth client (**Desktop app**).
2. `YT_CLIENT_ID=... YT_CLIENT_SECRET=... python get_token.py` → copy
   refresh token.
3. Export YouTube cookies (browser extension "Get cookies.txt", Netscape
   format) — needed so yt-dlp works from GitHub's IPs. Re-export when
   downloads start failing.
4. Add GitHub secrets: `YT_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`,
   `YT_REFRESH_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `YT_COOKIES`.

## Rollout

Week 1: trigger workflow manually (defaults to dry-run → uploads **private**),
check results in YouTube Studio. Then rely on the daily cron (uploads public).

## Local test

    pip install -r requirements.txt && pytest
    YT_API_KEY=... OPENAI_API_KEY=... python pipeline.py --dry-run

## Known risks

Copyright strikes possible (credit is not a license); reused-content
demonetization; cookie expiry. See spec for details.
```

- [ ] **Step 3: Validate workflow YAML**

Run: `.venv/bin/pip install -q pyyaml && .venv/bin/python -c "import yaml; yaml.safe_load(open('.github/workflows/daily.yml'))"`
Expected: clean exit.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/daily.yml README.md
git commit -m "feat: daily workflow and setup docs"
```

---

### Task 9: End-to-end dry run (manual, user-gated)

**Files:** none.

- [ ] **Step 1: Local dry run (needs user's keys)**

Run: `YT_API_KEY=... YT_CLIENT_ID=... YT_CLIENT_SECRET=... YT_REFRESH_TOKEN=... OPENAI_API_KEY=... ANTHROPIC_API_KEY=... .venv/bin/python pipeline.py --dry-run`
Expected: `Uploaded https://www.youtube.com/shorts/<id>`, video visible as
**Private** in YouTube Studio, `used_videos.json` + `log.jsonl` updated.
If the user's keys aren't available, stop and hand this step to the user.

- [ ] **Step 2: User pushes repo to GitHub, adds secrets, triggers workflow manually**

User action. Verify the Actions run is green and a private short appears.

- [ ] **Step 3: Commit any state files produced locally**

```bash
git add used_videos.json log.jsonl
git commit -m "state: first dry run"
```
