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
