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
    return text.replace("\\", "\\\\").replace("'", "'").replace(":", "\\:").replace("%", "\\%")


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
