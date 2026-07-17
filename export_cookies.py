"""Export YouTube cookies from the local Brave profile for one pipeline run."""
from pathlib import Path

import yt_dlp

COOKIE_FILE = Path("cookies.txt")


def main():
    options = {
        "cookiesfrombrowser": ("brave",),
        "cookiefile": str(COOKIE_FILE),
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        youtube_cookies = [
            cookie for cookie in ydl.cookiejar
            if cookie.domain.lstrip(".").endswith("youtube.com")
        ]
        if not youtube_cookies:
            raise RuntimeError("no YouTube cookies found in the local Brave profile")

    if not COOKIE_FILE.exists() or not COOKIE_FILE.stat().st_size:
        raise RuntimeError("failed to write cookies.txt")


if __name__ == "__main__":
    main()
