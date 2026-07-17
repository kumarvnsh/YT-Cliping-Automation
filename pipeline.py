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
