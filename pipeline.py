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


def _drawtext_escape(text):
    return text.replace("\\", "\\\\").replace("'", "’").replace(":", "\\:").replace("%", "\\%")


def edit(src, start, hook, credit, out):
    hook_e, credit_e = _drawtext_escape(hook), _drawtext_escape(credit)
    vf = ("crop=ih*9/16:ih,scale=1080:1920,"
          f"drawtext=text='{hook_e}':font=DejaVuSans:fontsize=64:fontcolor=white:"
          "borderw=4:bordercolor=black:x=(w-text_w)/2:y=200,"
          f"drawtext=text='{credit_e}':font=DejaVuSans:fontsize=36:fontcolor=white:"
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
