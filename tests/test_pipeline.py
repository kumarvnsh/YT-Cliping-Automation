import pipeline
import json
import signal
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


def test_parse_watch_heatmap_normalizes_and_sorts_markers():
    html = '''
    {"heatMarkerRenderer": {"timeRangeStartMillis": 20000,
                            "markerDurationMillis": 5000,
                            "heatMarkerIntensityScoreNormalized": 0.4}}
    {"heatMarkerRenderer": {"timeRangeStartMillis": 0,
                            "markerDurationMillis": 5000,
                            "heatMarkerIntensityScoreNormalized": 1}}
    '''

    assert pipeline.parse_watch_heatmap(html) == [
        {"start_time": 0.0, "end_time": 5.0, "value": 1.0},
        {"start_time": 20.0, "end_time": 25.0, "value": 0.4},
    ]


def test_parse_watch_heatmap_ignores_invalid_and_duplicate_markers():
    html = '''
    {"heatMarkerRenderer": {"timeRangeStartMillis": 0,
                            "markerDurationMillis": 5000,
                            "heatMarkerIntensityScoreNormalized": 0.7}}
    {"heatMarkerRenderer": {"timeRangeStartMillis": 0,
                            "markerDurationMillis": 5000,
                            "heatMarkerIntensityScoreNormalized": 0.2}}
    {"heatMarkerRenderer": {"timeRangeStartMillis": 1000,
                            "markerDurationMillis": 0,
                            "heatMarkerIntensityScoreNormalized": 0.5}}
    {"heatMarkerRenderer": {"timeRangeStartMillis": "bad",
                            "markerDurationMillis": 5000,
                            "heatMarkerIntensityScoreNormalized": 0.5}}
    '''

    assert pipeline.parse_watch_heatmap(html) == [
        {"start_time": 0.0, "end_time": 5.0, "value": 0.7},
    ]


def test_parse_watch_heatmap_returns_empty_for_pages_without_markers():
    assert pipeline.parse_watch_heatmap("<html><body>no markers</body></html>") == []


def test_parse_watch_heatmap_rejects_overflowed_normalized_times():
    html = '''
    {"heatMarkerRenderer": {"timeRangeStartMillis": 1e308,
                            "markerDurationMillis": 1e308,
                            "heatMarkerIntensityScoreNormalized": 0.5}}
    '''

    assert pipeline.parse_watch_heatmap(html) == []


def test_parse_watch_heatmap_ignores_oversized_integer_literals():
    oversized_integer = "9" * 4_301
    html = (
        '{"heatMarkerRenderer": {"timeRangeStartMillis": ' + oversized_integer
        + ', "markerDurationMillis": 5000, '
        + '"heatMarkerIntensityScoreNormalized": 0.5}}'
    )

    assert pipeline.parse_watch_heatmap(html) == []


def test_parse_watch_heatmap_ignores_float_overflowing_integer_values():
    oversized_integer = "9" * 309
    html = (
        '{"heatMarkerRenderer": {"timeRangeStartMillis": ' + oversized_integer
        + ', "markerDurationMillis": 5000, '
        + '"heatMarkerIntensityScoreNormalized": 0.5}}'
    )

    assert pipeline.parse_watch_heatmap(html) == []


def test_parse_watch_heatmap_ignores_truncated_marker_json():
    html = '{"heatMarkerRenderer":{"timeRangeStartMillis":'

    assert pipeline.parse_watch_heatmap(html) == []


def test_fetch_watch_heatmap_uses_watch_url_and_parses_response(monkeypatch):
    class Response:
        def read(self):
            return b'''{"heatMarkerRenderer": {"timeRangeStartMillis": 0,
                "markerDurationMillis": 5000,
                "heatMarkerIntensityScoreNormalized": 1}}'''

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, timeout))
            return Response()

    monkeypatch.setattr(pipeline, "build_opener", lambda *_handlers: Opener())

    assert pipeline.fetch_watch_heatmap("abc123") == [
        {"start_time": 0.0, "end_time": 5.0, "value": 1.0},
    ]
    assert calls == [("https://www.youtube.com/watch?v=abc123", 20)]


def test_fetch_watch_heatmap_enforces_total_response_deadline(monkeypatch):
    handlers = []
    timers = []

    class Response:
        def read(self):
            handlers[-1](signal.SIGALRM, None)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Opener:
        def open(self, _request, timeout):
            assert timeout == pipeline.WATCH_PAGE_SOCKET_TIMEOUT
            return Response()

    monkeypatch.setattr(pipeline, "build_opener", lambda *_handlers: Opener())
    monkeypatch.setattr(
        pipeline.signal,
        "signal",
        lambda _signal, handler: handlers.append(handler) or signal.SIG_DFL,
    )
    monkeypatch.setattr(
        pipeline.signal,
        "setitimer",
        lambda *args: timers.append(args),
    )

    with pytest.raises(TimeoutError, match="watch-page request exceeded"):
        pipeline.fetch_watch_heatmap("abc123")

    assert timers == [
        (signal.ITIMER_REAL, pipeline.WATCH_PAGE_TOTAL_TIMEOUT),
        (signal.ITIMER_REAL, 0),
    ]


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
    monkeypatch.setattr(pipeline, "fetch_watch_heatmap", lambda _vid: [
        {"start_time": 0, "end_time": 10, "value": 1},
    ])
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


def test_main_skips_no_heatmap_before_downloading(tmp_path, monkeypatch):
    used_file = tmp_path / "used_videos.json"
    used_file.write_text("[]")
    first, second = _vid("first", "PT10M"), _vid("second", "PT10M")
    downloaded = []

    monkeypatch.setattr(pipeline, "USED_FILE", used_file)
    monkeypatch.setattr(pipeline, "LOG_FILE", tmp_path / "log.jsonl")
    monkeypatch.setattr(pipeline, "fetch_trending", lambda _key: [first, second])
    monkeypatch.setattr(
        pipeline,
        "fetch_watch_heatmap",
        lambda vid: [] if vid == "first" else [
            {"start_time": 0, "end_time": 10, "value": 1},
        ],
    )
    monkeypatch.setattr(
        pipeline,
        "download",
        lambda vid: (downloaded.append(vid) or tmp_path / f"{vid}.mp4", {}),
    )
    monkeypatch.setattr(pipeline, "gen_metadata", lambda *_args: {
        "title": "title", "description": "description", "hashtags": [], "hook": "hook",
    })
    monkeypatch.setattr(pipeline, "edit", lambda *_args: None)
    monkeypatch.setattr(pipeline, "self_check", lambda _path: None)
    monkeypatch.setattr(pipeline, "upload", lambda *_args: "id")
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "--dry-run"])
    monkeypatch.setenv("YT_API_KEY", "test-key")

    pipeline.main()

    assert downloaded == ["second"]


def test_main_skips_watch_fetch_error_and_downloads_next_candidate(tmp_path, monkeypatch):
    used_file = tmp_path / "used_videos.json"
    used_file.write_text("[]")
    first, second = _vid("first", "PT10M"), _vid("second", "PT10M")
    downloaded = []

    def fetch_heatmap(vid):
        if vid == "first":
            raise OSError("network")
        return [{"start_time": 0, "end_time": 10, "value": 1}]

    monkeypatch.setattr(pipeline, "USED_FILE", used_file)
    monkeypatch.setattr(pipeline, "LOG_FILE", tmp_path / "log.jsonl")
    monkeypatch.setattr(pipeline, "fetch_trending", lambda _key: [first, second])
    monkeypatch.setattr(pipeline, "fetch_watch_heatmap", fetch_heatmap)
    monkeypatch.setattr(
        pipeline,
        "download",
        lambda vid: (downloaded.append(vid) or tmp_path / f"{vid}.mp4", {}),
    )
    monkeypatch.setattr(pipeline, "gen_metadata", lambda *_args: {
        "title": "title", "description": "description", "hashtags": [], "hook": "hook",
    })
    monkeypatch.setattr(pipeline, "edit", lambda *_args: None)
    monkeypatch.setattr(pipeline, "self_check", lambda _path: None)
    monkeypatch.setattr(pipeline, "upload", lambda *_args: "id")
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "--dry-run"])
    monkeypatch.setenv("YT_API_KEY", "test-key")

    pipeline.main()

    assert downloaded == ["second"]


def test_workflow_provisions_youtube_challenge_dependencies():
    root = Path(__file__).resolve().parents[1]
    requirements = (root / "requirements.txt").read_text()
    workflow = (root / ".github/workflows/daily.yml").read_text()

    assert "yt-dlp[default]" in requirements
    assert "denoland/setup-deno@v2" in workflow


def test_attempt_limit_checks_ten_trending_candidates():
    assert pipeline.MAX_ATTEMPTS == 10
