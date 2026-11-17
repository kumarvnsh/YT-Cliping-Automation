"""Daily YT gaming shorts pipeline. See docs/superpowers/specs/ for design."""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener

import browser_heatmap
import motion
from heatmaps import parse_watch_heatmap

CLIP_SECONDS = 35
MIN_SOURCE_SECONDS = 120
# ponytail: cap source length so no-heatmap candidates don't pull a multi-hour
# VOD to grab 35s; tune if too many candidates get filtered.
MAX_SOURCE_SECONDS = 3600
# Backstop so 1 fps scene analysis can never hang the run; tune per runner.
MOTION_ANALYSIS_TIMEOUT = 120
MAX_ATTEMPTS = 10
USED_FILE = Path("used_videos.json")
LOG_FILE = Path("log.jsonl")
WORK = Path("work")


def parse_duration(iso: str) -> int:
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    h, mn, s = (int(g or 0) for g in m.groups())
    return h * 3600 + mn * 60 + s


_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})")


def parse_video_id(url):
    """Extract the 11-char video id from watch?v=, youtu.be/, or /shorts/ URLs."""
    m = _VIDEO_ID_RE.search(url)
    if not m:
        raise ValueError(f"cannot parse video id from url: {url!r}")
    return m.group(1)


def parse_timestamp(ts):
    """Parse SS, MM:SS, or HH:MM:SS into seconds. Rejects negatives and junk."""
    parts = ts.strip().split(":")
    if len(parts) > 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"bad timestamp: {ts!r}")
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + int(p)
    return seconds


def eligible(items, used):
    return [v for v in items
            if v["id"] not in used
            and MIN_SOURCE_SECONDS <= parse_duration(
                v["contentDetails"]["duration"]) <= MAX_SOURCE_SECONDS]


def fetch_trending(api_key):
    from googleapiclient.discovery import build
    yt = build("youtube", "v3", developerKey=api_key)
    resp = yt.videos().list(part="snippet,contentDetails", chart="mostPopular",
                            videoCategoryId="20", regionCode="US",
                            maxResults=25).execute()
    return resp["items"]


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


WATCH_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
WATCH_PAGE_SOCKET_TIMEOUT = 20
WATCH_PAGE_TOTAL_TIMEOUT = 30


@contextmanager
def _watch_page_deadline():
    """Bound the full watch-page request, including a slow response body."""
    def timeout_handler(_signum, _frame):
        raise TimeoutError("watch-page request exceeded total deadline")

    previous_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, WATCH_PAGE_TOTAL_TIMEOUT)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def fetch_watch_heatmap(video_id):
    """Fetch a watch page with local cookies and extract its heat markers."""
    cookie_file = Path("cookies.txt")
    jar = MozillaCookieJar()
    if cookie_file.exists():
        jar = MozillaCookieJar(str(cookie_file))
        jar.load(ignore_discard=True, ignore_expires=True)
    opener = build_opener(HTTPCookieProcessor(jar))
    request = Request(
        f"https://www.youtube.com/watch?v={video_id}",
        headers={"User-Agent": WATCH_USER_AGENT,
                 "Accept-Language": "en-US,en;q=0.9"},
    )
    with _watch_page_deadline():
        with opener.open(request, timeout=WATCH_PAGE_SOCKET_TIMEOUT) as response:
            html = response.read().decode("utf-8", errors="replace")
    return parse_watch_heatmap(html)


def fetch_heatmap(video_id):
    """Fetch static heat markers, optionally falling back to rendered markers."""
    heatmap = fetch_watch_heatmap(video_id)
    if heatmap or os.getenv("USE_RENDERED_HEATMAP") != "1":
        return heatmap
    return browser_heatmap.fetch_rendered_heatmap(video_id, Path("cookies.txt"))


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


def motion_scores(src):
    """Run 1 fps scene analysis on a downloaded clip; return (pts, score) pairs.

    Raises RuntimeError if ffmpeg fails so the candidate is skipped. Empty or
    all-zero output is valid and yields the centered window via pick_motion_window.
    """
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
        meta_path = Path(tmp.name)
    try:
        vf = f"fps=1,select='gt(scene,-1)',metadata=print:file={meta_path}"
        try:
            r = subprocess.run(["ffmpeg", "-y", "-i", str(src), "-vf", vf,
                                "-an", "-f", "null", "-"], capture_output=True,
                               timeout=MOTION_ANALYSIS_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"ffmpeg motion analysis exceeded {MOTION_ANALYSIS_TIMEOUT}s")
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg motion analysis failed: {r.stderr.decode()[-2000:]}")
        return motion.parse_scene_scores(meta_path.read_text())
    finally:
        meta_path.unlink(missing_ok=True)


def _drawtext_escape(text):
    return text.replace("\\", "\\\\").replace("'", "’").replace(":", "\\:").replace("%", "\\%")


def edit(src, start, hook, credit, out):
    hook_e, credit_e = _drawtext_escape(hook), _drawtext_escape(credit)
    font = os.getenv("FFMPEG_FONT", "DejaVuSans")
    vf = ("crop=ih*9/16:ih,scale=1080:1920,"
          f"drawtext=text='{hook_e}':font={font}:fontsize=64:fontcolor=white:"
          "borderw=4:bordercolor=black:x=(w-text_w)/2:y=200,"
          f"drawtext=text='{credit_e}':font={font}:fontsize=36:fontcolor=white:"
          "borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-250")
    r = subprocess.run(["ffmpeg", "-y", "-ss", str(start), "-i", str(src),
                        "-t", str(CLIP_SECONDS), "-vf", vf,
                        "-c:v", "libx264", "-preset", "veryfast",
                        "-c:a", "aac", str(out)],
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.decode()[-2000:]}")


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


def upload(path, meta, privacy):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    token_file = Path(__file__).resolve().parent / "token.json"
    creds = Credentials.from_authorized_user_file(str(token_file)) if token_file.exists() else Credentials(
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


def log_run(entry):
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def atomic_write_json(path, data):
    """Write JSON using a same-directory replacement to avoid partial state files."""
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as tmp:
        json.dump(data, tmp, indent=0)
        tmp.write("\n")
        temp_path = Path(tmp.name)
    temp_path.replace(path)


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


if __name__ == "__main__":
    main()
