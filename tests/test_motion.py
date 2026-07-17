import pytest

import motion


def test_parse_scene_scores_reads_pts_and_scene_values():
    text = "pts_time:1.0\nlavfi.scene_score=0.2\npts_time:4.0\nlavfi.scene_score=0.9\n"

    assert motion.parse_scene_scores(text) == [(1.0, 0.2), (4.0, 0.9)]


def test_parse_scene_scores_rejects_invalid_values_and_stale_pts():
    text = "\n".join(
        [
            "pts_time:1.0",
            "lavfi.scene_score=nan",
            "pts_time:-2",
            "lavfi.scene_score=0.4",
            "pts_time:3.0",
            "unrelated metadata",
            "pts_time:4.0",
            "lavfi.scene_score=0.8",
            "pts_time:5.0",
            "lavfi.scene_score=-0.1",
            "pts_time:inf",
            "lavfi.scene_score=0.6",
            "pts_time:7.0",
            "lavfi.scene_score=0.7",
        ]
    )

    assert motion.parse_scene_scores(text) == [(4.0, 0.8), (7.0, 0.7)]


def test_pick_motion_window_prefers_highest_scoring_35_second_window():
    start, method = motion.pick_motion_window([(10.0, 0.1), (50.0, 0.9)], 120, 35)

    assert method == "motion"
    assert 45 <= start <= 50


def test_pick_motion_window_centers_when_scores_are_empty_or_zero():
    assert motion.pick_motion_window([], 120, 35) == (42.5, "center")
    assert motion.pick_motion_window([(10.0, 0.0)], 120, 35) == (42.5, "center")


def test_pick_motion_window_ignores_malformed_scores_and_clamps_at_end():
    scores = [("bad", 1.0), (10.0, float("inf")), (10.0, -1.0), (119.0, 0.9)]

    assert motion.pick_motion_window(scores, 120, 35) == (85.0, "motion")


def test_pick_motion_window_centers_when_clip_is_longer_than_source():
    assert motion.pick_motion_window([(1.0, 0.9)], 20, 35) == (0, "motion")


@pytest.mark.parametrize("duration, clip", [(0, 35), (-1, 35), (120, 0), (120, -1), (float("inf"), 35)])
def test_pick_motion_window_rejects_invalid_duration_or_clip(duration, clip):
    with pytest.raises(ValueError):
        motion.pick_motion_window([], duration, clip)
