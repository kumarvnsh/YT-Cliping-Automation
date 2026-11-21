"""ShotSnip pacing trial and observed per-platform checkpoints."""
from datetime import datetime
from pathlib import Path
from statistics import median
import json
import math

TRIAL = 'shotsnip-pacing-20261121'
VARIANTS = (('tight_20s', 20), ('context_30s', 30))
WINDOWS = ((24, 1), (72, 2))
CAP = 24


def trial_record(row):
    """Prefer immutable ledger data; only use a real saved tag for legacy rows."""
    if isinstance(row.get('growth_experiment'), dict):
        return row['growth_experiment']
    try:
        path = Path(row['file']).parent / 'metadata.json'
        tag = json.loads(path.read_text(encoding='utf-8')).get('growth_experiment')
        return tag if isinstance(tag, dict) else {}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def assignment(state, enabled=True):
    count = sum(row.get('status') == 'uploaded' and trial_record(row).get('id') == TRIAL
                for row in state.get('uploads', []))
    active = enabled and count < CAP
    variant, seconds = VARIANTS[count % 2 if active else 1]
    return {'id': TRIAL if active else None, 'slot': count + 1 if active else None,
            'variant': variant, 'source_seconds': seconds,
            'note': 'Experimental assignment. Final duration can include commentary.'}


def stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (AttributeError, ValueError, TypeError, OverflowError):
        return None


def count_value(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def platform_checkpoints(snapshots, ids, platform, as_of):
    """Choose actual, closest observations, using only that platform's post clock."""
    facebook = platform == 'facebook'
    metric, pub = ('plays', 'created_time') if facebook else ('views', 'published_at')
    timestamps, observations = {}, []
    checked = stamp(as_of)
    for snap in snapshots:
        checked_at = snap.get('checked_at')
        checked_ts = stamp(checked_at)
        if checked_ts is None or checked is None or checked_ts > checked:
            continue
        for video in snap.get('videos', []):
            vid = video.get('id')
            if vid not in ids:
                continue
            source_time = video.get(pub)
            if not facebook or video.get('created_time_source') == 'meta':
                if stamp(source_time) is not None:
                    timestamps.setdefault(vid, source_time)
            observed_at = video.get('checked_at') or checked_at
            value = video.get(metric)
            if count_value(value):
                observations.append((vid, observed_at, value))
    checkpoints = {vid: {} for vid in ids}
    for vid, observed_at, value in observations:
        start, observed = stamp(timestamps.get(vid)), stamp(observed_at)
        if start is None or observed is None or not start <= observed <= checked:
            continue
        age = (observed - start) / 3600
        for hour, tolerance in WINDOWS:
            key = str(hour)
            prior = checkpoints[vid].get(key)
            if abs(age - hour) <= tolerance and (prior is None or
                    (abs(age - hour), observed) < (abs(prior['age_hours'] - hour), stamp(prior['observed_at']))):
                checkpoints[vid][key] = {'value': value, 'age_hours': age, 'observed_at': observed_at}
    return {vid: {'published_at': timestamps.get(vid), 'checkpoints': record}
            for vid, record in checkpoints.items()}


def summary(rows, clocks):
    results = {}
    for platform in ('youtube', 'facebook'):
        by_arm = results[platform] = {}
        now = stamp(clocks.get(platform))
        for variant, _ in VARIANTS:
            group = [r[platform] for r in rows if r['variant'] == variant and r[platform]['id']]
            checkpoints = by_arm[variant] = {}
            for hour, tolerance in WINDOWS:
                key = str(hour)
                eligible = [r for r in group if stamp(r['published_at']) is not None and now is not None
                            and now - stamp(r['published_at']) >= (hour - tolerance) * 3600]
                values = [r['checkpoints'][key]['value'] for r in eligible if key in r['checkpoints']]
                checkpoints[key] = {'published': len(group), 'eligible': len(eligible),
                                    'measured': len(values), 'missing': len(eligible) - len(values),
                                    'unverified_time': sum(not r['published_at'] for r in group),
                                    'median': median(values) if values else None,
                                    'reached_1k': sum(v >= 1000 for v in values) if values else None}
    return results


def build_report(state, youtube_history, facebook_history, clocks):
    tagged = {}
    for row in state.get('uploads', []):
        if row.get('status') != 'uploaded' or not row.get('video_id'):
            continue
        tag = trial_record(row)
        if tag.get('id') == TRIAL and tag.get('variant') in dict(VARIANTS):
            tagged.setdefault(row['video_id'], (row, tag))
    fb_ids = {key: row.get('facebook', {}).get('reel_id')
              for key, (row, _) in tagged.items() if row.get('facebook', {}).get('status') == 'published'}
    yt = platform_checkpoints(youtube_history, set(tagged), 'youtube', clocks.get('youtube'))
    fb = platform_checkpoints(facebook_history, set(filter(None, fb_ids.values())), 'facebook', clocks.get('facebook'))
    rows = [{'video_id': key, 'variant': tag['variant'], 'slot': tag.get('slot'),
             'creator': row.get('source', {}).get('channel'), 'source_seconds': tag.get('source_seconds'),
             'rendered_seconds': row.get('rendered_seconds'),
             'youtube': {'id': key, **yt[key]},
             'facebook': {'id': fb_ids.get(key), **fb.get(fb_ids.get(key), {'published_at': None, 'checkpoints': {}})}}
            for key, (row, tag) in tagged.items()]
    return {'id': TRIAL, 'checked_at': clocks.get('youtube'), 'platform_checked_at': clocks,
            'target_clips': CAP, 'published': len(rows), 'status': 'collecting' if len(rows) < CAP else 'awaiting_mature_review',
            'videos': rows, 'checkpoints': summary(rows, clocks)}


def _json(path, fallback):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else fallback
    except (OSError, ValueError):
        return fallback


def report(root, youtube, youtube_history=()):
    root = Path(root)
    dash = root / 'work/dashboard'
    state = _json(root / 'work/scheduler/state.json', {})
    facebook = _json(dash / 'facebook-analytics.json', {})
    # The pause/no-assignment case should not scan the Facebook archive.
    has_trial = any(row.get('status') == 'uploaded' and trial_record(row).get('id') == TRIAL
                    for row in state.get('uploads', []))
    fb_history = (_json(path, {}) for path in (dash / 'facebook-analytics-history').glob('*.json')) if has_trial else ()
    from itertools import chain
    return build_report(state, chain(youtube_history, (youtube,)), chain(fb_history, (facebook,)),
                        {'youtube': youtube.get('checked_at'), 'facebook': facebook.get('checked_at')})
