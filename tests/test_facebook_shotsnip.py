import json
from pathlib import Path

import pytest

import facebook_shotsnip as fb


class _Resp:
    def __init__(self, payload=None, status=200):
        self._p = payload or {}
        self.status_code = status
        self.text = json.dumps(self._p)

    def json(self):
        return self._p


def test_facebook_enabled_requires_both_config_and_token(tmp_path, monkeypatch):
    # Arrange
    (tmp_path / "facebook.json").write_text(json.dumps({"enabled": True, "page_id": "1"}))
    monkeypatch.delenv("SHOTSNIP_META_TOKEN", raising=False)
    # Act / Assert — config on, token off
    assert fb.enabled(tmp_path) is False
    # Act / Assert — both present
    monkeypatch.setenv("SHOTSNIP_META_TOKEN", "tok")
    assert fb.enabled(tmp_path) is True


def test_facebook_crosspost_skips_and_is_silent_when_disabled(tmp_path, monkeypatch):
    # Arrange
    monkeypatch.setattr(fb, "enabled", lambda root=None: False)
    monkeypatch.setattr(fb, "post_reel", lambda *a, **k: pytest.fail("must not post when disabled"))
    record = {}
    # Act
    fb.crosspost(record, tmp_path / "clip.mp4", "Title", "Body")
    # Assert
    assert "facebook" not in record


def test_facebook_crosspost_records_error_and_never_raises(tmp_path, monkeypatch):
    # Arrange
    monkeypatch.setattr(fb, "enabled", lambda root=None: True)

    def boom(*a, **k):
        raise RuntimeError("graph 400 bad token")

    monkeypatch.setattr(fb, "post_reel", boom)
    record = {}
    # Act — a Facebook failure must not propagate (YouTube already published)
    fb.crosspost(record, tmp_path / "clip.mp4", "Title", "Body")
    # Assert
    assert record["facebook"]["status"] == "error"
    assert "graph 400 bad token" in record["facebook"]["error"]


def test_facebook_post_reel_runs_full_publish_flow_and_returns_id(tmp_path, monkeypatch):
    # Arrange
    (tmp_path / "facebook.json").write_text(
        json.dumps({"enabled": True, "page_id": "123", "api_version": "v21.0"})
    )
    monkeypatch.setenv("SHOTSNIP_META_TOKEN", "sys-tok")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x01\x02\x03")
    posts = []

    def fake_get(url, **kwargs):
        if kwargs.get("params", {}).get("fields") == "access_token":
            return _Resp({"access_token": "page-tok"})
        return _Resp({"status": {"uploading_phase": {"status": "complete"}}})

    def fake_post(url, **kwargs):
        posts.append((url, kwargs))
        data = kwargs.get("data", {})
        if url.endswith("/video_reels") and data.get("upload_phase") == "start":
            return _Resp({"video_id": "v99", "upload_url": "https://rupload.facebook.com/video-upload/v21.0/v99"})
        return _Resp({"success": True})

    monkeypatch.setattr(fb.requests, "get", fake_get)
    monkeypatch.setattr(fb.requests, "post", fake_post)
    # Act
    reel_id = fb.post_reel(video, "a description", root=tmp_path, timeout_s=5)
    # Assert
    assert reel_id == "v99"
    binary = [p for p in posts if "rupload" in p[0]]
    assert binary and binary[0][1]["data"] == b"\x00\x01\x02\x03"
    assert binary[0][1]["headers"]["file_size"] == "4"
    finish = [p for p in posts if p[0].endswith("/video_reels") and p[1]["data"].get("upload_phase") == "finish"]
    assert finish and finish[0][1]["data"]["video_state"] == "PUBLISHED"
    assert finish[0][1]["data"]["description"] == "a description"


def test_facebook_post_reel_raises_on_graph_error(tmp_path, monkeypatch):
    # Arrange
    (tmp_path / "facebook.json").write_text(json.dumps({"enabled": True, "page_id": "123"}))
    monkeypatch.setenv("SHOTSNIP_META_TOKEN", "sys-tok")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(fb.requests, "get", lambda url, **k: _Resp({"error": "bad"}, status=400))
    # Act / Assert
    with pytest.raises(RuntimeError, match="FB page-token fetch failed"):
        fb.post_reel(video, "d", root=tmp_path, timeout_s=1)
