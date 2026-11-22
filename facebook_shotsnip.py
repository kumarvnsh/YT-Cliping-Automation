"""Cross-post a finished Shotsnip clip to the Shotsnip Facebook Page as a Reel.

Facebook-only (no Instagram). Runs on the local Windows worker against the
private repo, so it uploads the mp4 bytes directly to Facebook's resumable
endpoint — no public URL / GitHub Release hosting (Histold's meta_uploader
needs that only because it runs in CI on a public repo).

Config: facebook.json {enabled, page_id, api_version}.
Token:  env SHOTSNIP_META_TOKEN — a non-expiring system-user token assigned to
        the Shotsnip Page with pages_read_engagement + pages_manage_posts.

Best-effort by contract: crosspost() never raises, so a Facebook failure can
never block or reverse the YouTube publish that already happened.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

GRAPH = "https://graph.facebook.com"
RUPLOAD = "https://rupload.facebook.com/video-upload"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(root) -> Path:
    return Path(root) if root else Path(__file__).resolve().parent


def _cfg(root=None) -> dict:
    p = _root(root) / "facebook.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _token() -> str:
    token=os.environ.get("SHOTSNIP_META_TOKEN", "").strip()
    if not token and os.name=='nt':
        # Read fresh user settings: setx does not update an existing worker's environment.
        token=_user_token()
    return token


def _user_token():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment') as key:
            return str(winreg.QueryValueEx(key,'SHOTSNIP_META_TOKEN')[0]).strip()
    except (ImportError,OSError):
        return ''


def enabled(root=None) -> bool:
    """Only cross-post when explicitly enabled AND a token is present."""
    return bool(_cfg(root).get("enabled")) and bool(_token())


def _raise(r, step) -> None:
    if r.status_code >= 400:
        raise RuntimeError(f"{step} failed ({r.status_code}): {r.text[:300]}")


def _page_token(user_token: str, page_id: str, ver: str) -> str:
    """Page publishing needs a Page token derived from the system-user token."""
    r = requests.get(f"{GRAPH}/{ver}/{page_id}",
                     params={"fields": "access_token"}, headers={'Authorization':'Bearer '+user_token}, timeout=30)
    _raise(r, "FB page-token fetch")
    tok = r.json().get("access_token")
    if not tok:
        raise RuntimeError(
            "No Page access token — the system user is not assigned to the Shotsnip "
            "Page with content permission in Business settings."
        )
    return tok


def post_reel(video, description: str, root=None, timeout_s: int = 300) -> str:
    """Publish `video` (path) as a Reel on the Shotsnip Page. Returns the reel id.

    Raises on any misconfiguration or API rejection; crosspost() wraps this.
    """
    page_id = str(_cfg(root).get("page_id", "")).strip()
    ver = str(_cfg(root).get("api_version", "v21.0")).strip()
    user_token = _token()
    if not page_id:
        raise RuntimeError("facebook.json has no page_id")
    if not user_token:
        raise RuntimeError("SHOTSNIP_META_TOKEN is not set")

    video = Path(video)
    data = video.read_bytes()
    token = _page_token(user_token, page_id, ver)

    # 1. start — reserve a video id + upload url
    start = requests.post(f"{GRAPH}/{ver}/{page_id}/video_reels",
                          data={"upload_phase": "start", "access_token": token}, timeout=60)
    _raise(start, "FB reel start")
    sj = start.json()
    video_id = sj["video_id"]
    upload_url = sj.get("upload_url") or f"{RUPLOAD}/{ver}/{video_id}"

    # 2. upload the local bytes directly (no public URL needed)
    up = requests.post(upload_url,
                       headers={"Authorization": f"OAuth {token}",
                                "offset": "0", "file_size": str(len(data))},
                       data=data, timeout=max(300, timeout_s))
    _raise(up, "FB reel upload")

    # 3. wait until Meta finishes ingesting before we publish
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        s = requests.get(f"{GRAPH}/{ver}/{video_id}",
                         params={"fields": "status"}, headers={'Authorization':'Bearer '+token}, timeout=30)
        _raise(s, "FB reel status")
        phase = (s.json().get("status", {}).get("uploading_phase") or {}).get("status")
        if phase == "complete":
            break
        if phase == "error":
            raise RuntimeError(f"FB reel ingest errored: {s.json()}")
        time.sleep(5)
    else:
        raise RuntimeError("FB reel did not finish ingesting in time")

    # 4. finish + publish
    finish = requests.post(f"{GRAPH}/{ver}/{page_id}/video_reels", data={
        "upload_phase": "finish",
        "video_id": video_id,
        "video_state": "PUBLISHED",
        "description": description,
        "access_token": token,
    }, timeout=60)
    _raise(finish, "FB reel finish")
    return video_id


def crosspost(record: dict, video, title: str, description: str = "", root=None) -> None:
    """Best-effort Shotsnip FB Page cross-post. Writes record['facebook']; never raises."""
    try:
        if not enabled(root):
            return
        from public_metadata import description as clean_description
        description=clean_description(description)
        desc = title + ("\n\n" + description if description else "")
        reel_id = post_reel(video, desc, root=root)
        record["facebook"] = {"status": "published", "reel_id": reel_id, "at": _now()}
    except Exception as exc:  # noqa: BLE001 — must not break the YouTube publish
        record["facebook"] = {"status": "error", "error": str(exc)[:250], "at": _now()}
