import pipeline


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
