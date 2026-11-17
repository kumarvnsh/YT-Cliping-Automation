from media_safety import safe_window


def test_peak_after_audio_ends_falls_back_to_scored_highlight():
    assert safe_window((0,94),[200,46])==(16,46)


def test_partial_or_missing_audio_cannot_reach_captions():
    assert safe_window((0,94),[110,100]) is None
    assert safe_window(None,[50]) is None
    assert safe_window((20,94),[40]) is None


def test_complete_audio_preserves_peak():
    assert safe_window((0,300),[200,46])==(170,200)
