"""Static parsing and normalization for YouTube watch-page heat markers."""
import json
import math
import re


HEAT_MARKER_RE = re.compile(r'"heatMarkerRenderer"\s*:\s*')


def _finite_number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def normalize_heat_markers(markers):
    """Validate and normalize heat-marker renderer mappings."""
    normalized = []
    seen_ranges = set()
    for marker in markers:
        if not isinstance(marker, dict):
            continue

        start_ms = marker.get("timeRangeStartMillis")
        duration_ms = marker.get("markerDurationMillis")
        value = marker.get("heatMarkerIntensityScoreNormalized")
        if not all(_finite_number(item) for item in (start_ms, duration_ms, value)):
            continue

        start = start_ms / 1000
        end = (start_ms + duration_ms) / 1000
        if (not math.isfinite(start) or not math.isfinite(end)
                or duration_ms <= 0 or start < 0 or end <= start):
            continue

        marker_range = (start, end)
        if marker_range in seen_ranges:
            continue
        seen_ranges.add(marker_range)
        normalized.append({"start_time": float(start), "end_time": float(end),
                           "value": float(value)})
    return sorted(normalized,
                  key=lambda marker: (marker["start_time"], marker["end_time"]))


def parse_watch_heatmap(html):
    """Extract and normalize embedded YouTube heat-marker renderers."""
    decoder = json.JSONDecoder()
    markers = []
    for match in HEAT_MARKER_RE.finditer(html):
        try:
            marker, _ = decoder.raw_decode(html, match.end())
        except (json.JSONDecodeError, ValueError):
            continue
        markers.append(marker)
    return normalize_heat_markers(markers)
