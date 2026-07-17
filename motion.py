"""Pure parsing and window selection for local ffmpeg scene/motion fallback.

Used only when both YouTube heatmap sources are empty. ffmpeg itself is invoked
by pipeline.py; this module just parses its output and picks a clip window.
"""
import math
import re

PTS_RE = re.compile(r"pts_time:(\S+)")
SCORE_RE = re.compile(r"lavfi\.scene_score=(\S+)")


def _finite_nonneg(value):
    """Return value as a finite non-negative float, or None if it isn't one."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num) or num < 0:
        return None
    return num


def parse_scene_scores(text):
    """Parse ffmpeg metadata=print output into (pts_time, scene_score) pairs.

    Each frame line carries pts_time:, followed by its lavfi.scene_score= line.
    A pts with no following valid score is dropped; a score with no valid pending
    pts is dropped.
    """
    scores = []
    pending = None
    for line in text.splitlines():
        match = SCORE_RE.search(line)
        if match:
            value = _finite_nonneg(match.group(1))
            if pending is not None and value is not None:
                scores.append((pending, value))
            pending = None
            continue
        match = PTS_RE.search(line)
        if match:
            pending = _finite_nonneg(match.group(1))
    return scores


def pick_motion_window(scores, duration, clip):
    """Return (start_second, method) for the best clip window.

    Sums scene scores over each candidate window and picks the highest. Falls
    back to the centered window when every score is zero, absent, or malformed.
    method is "motion" or "center".

    ponytail: O(n^2) scan; scores are ~one per second, fine forever.
    """
    if not (_positive_finite(duration) and _positive_finite(clip)):
        raise ValueError(f"invalid duration/clip: {duration!r}/{clip!r}")

    valid = []
    for entry in scores:
        try:
            pts, value = entry
        except (TypeError, ValueError):
            continue
        pts, value = _finite_nonneg(pts), _finite_nonneg(value)
        if pts is not None and value is not None:
            valid.append((pts, value))

    max_start = max(0.0, duration - clip)
    best_start, best_score = None, 0.0
    for pts, _value in valid:
        start = min(pts, max_start)
        end = start + clip
        score = sum(v for p, v in valid if start <= p < end)
        if score > best_score:
            best_score, best_start = score, start
    if best_start is None:  # all scores zero/absent/malformed
        return max_start / 2, "center"
    return best_start, "motion"


def _positive_finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0
