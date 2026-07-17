import pipeline
import json
import sys
from pathlib import Path

import pytest


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


def test_gen_metadata_uses_openai(monkeypatch):
    meta = {"title": "T", "description": "D", "hashtags": ["#shorts"], "hook": "H"}
    monkeypatch.setattr(pipeline, "_openai_meta", lambda p: meta)
    monkeypatch.setattr(pipeline, "_claude_meta",
                        lambda p: (_ for _ in ()).throw(AssertionError("fallback used")))
    assert pipeline.gen_metadata("src", "chan", "http://u")["title"] == "T"


def test_drawtext_escape_kills_quotes():
    out = pipeline._drawtext_escape("it's 100%: fine")
    assert "'" not in out
    assert "\\:" in out and "\\%" in out


def test_gen_metadata_falls_back_to_claude(monkeypatch):
    meta = {"title": "T2", "description": "D", "hashtags": [], "hook": "H"}
    def boom(p):
        raise RuntimeError("openai down")
    monkeypatch.setattr(pipeline, "_openai_meta", boom)
    monkeypatch.setattr(pipeline, "_claude_meta", lambda p: meta)
    assert pipeline.gen_metadata("src", "chan", "http://u")["title"] == "T2"


def test_atomic_write_json_replaces_from_same_directory(tmp_path, monkeypatch):
    target = tmp_path / "used_videos.json"
    target.write_text('["old"]')
    replacements = []
    original_replace = Path.replace

    def observe_replace(source, destination):
        replacements.append((source, destination))
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", observe_replace)

    pipeline.atomic_write_json(target, ["new"])

    assert json.loads(target.read_text()) == ["new"]
    assert len(replacements) == 1
    assert replacements[0][1] == target
    assert replacements[0][0].parent == target.parent


def test_bookkeeping_failure_after_upload_does_not_try_next_candidate(tmp_path, monkeypatch):
    used_file = tmp_path / "used_videos.json"
    used_file.write_text("[]")
    first, second = _vid("first", "PT10M"), _vid("second", "PT10M")
    attempted_uploads = []

    monkeypatch.setattr(pipeline, "USED_FILE", used_file)
    monkeypatch.setattr(pipeline, "LOG_FILE", tmp_path / "log.jsonl")
    monkeypatch.setattr(pipeline, "fetch_trending", lambda _key: [first, second])
    monkeypatch.setattr(pipeline, "download", lambda vid: (tmp_path / f"{vid}.mp4", {
        "duration": 600,
        "heatmap": [{"start_time": 0, "end_time": 10, "value": 1}],
    }))
    monkeypatch.setattr(pipeline, "gen_metadata", lambda *_args: {
        "title": "title", "description": "description", "hashtags": [], "hook": "hook",
    })
    monkeypatch.setattr(pipeline, "edit", lambda *_args: None)
    monkeypatch.setattr(pipeline, "self_check", lambda _path: None)
    monkeypatch.setattr(pipeline, "upload", lambda path, meta, privacy: attempted_uploads.append(path) or "id")
    monkeypatch.setattr(pipeline, "log_run", lambda _entry: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "--dry-run"])
    monkeypatch.setenv("YT_API_KEY", "test-key")

    with pytest.raises(OSError, match="disk full"):
        pipeline.main()

    assert len(attempted_uploads) == 1


def test_workflow_provisions_youtube_challenge_dependencies():
    root = Path(__file__).resolve().parents[1]
    requirements = (root / "requirements.txt").read_text()
    workflow = (root / ".github/workflows/daily.yml").read_text()

    assert "yt-dlp[default]" in requirements
    assert "denoland/setup-deno@v2" in workflow
