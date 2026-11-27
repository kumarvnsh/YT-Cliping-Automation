"""Every Shotsnip setting the owner sets from Studio HQ (Shotsnip > Live > Posting rules).

The owner decides these on the dashboard; the code keeps no values of its own (23 Nov). Each one lives in
scheduler.json, and DEFAULTS below are only what a fresh scheduler.json starts with: `fill()` writes them into the
file, so every value the scheduler uses is one the dashboard shows and can change.
Studio HQ and the old dashboard both validate changes with `validate()` before `apply()` writes them.
"""
import copy

# (path in scheduler.json, label, group, kind, limits or choices, default, help)
SETTINGS = (
    ('niche', 'Channel content', 'Posting', 'choice', ('gta', 'animals'), 'gta',
     'gta: live GTA stream clips. animals: most-viewed funny animal and brainrot Shorts, re-cut with a '
     'Shotsnip twist, credited on screen and published only after YouTube\'s copyright check.'),
    ('youtube_rules.max_per_day', 'Uploads a day', 'Posting', 'int', (1, 12), 3,
     'At most this many YouTube uploads in any 24 hours.'),
    ('youtube_rules.min_gap_minutes', 'Minimum gap between uploads (min)', 'Posting', 'int', (30, 1440), 120,
     'No upload goes out sooner than this after the last one.'),
    ('youtube_rules.same_streamer_hours', 'Same streamer again after (h)', 'Posting', 'int', (0, 72), 8,
     'A streamer is never posted twice in a row, nor again within this many hours.'),
    ('youtube_rules.similar_titles', 'Block titles this similar (0.5-1)', 'Posting', 'float', (0.5, 1.0), 0.7,
     'A title that shares this share of its words with a recent title is dropped.'),
    ('youtube_rules.recent_titles', 'Recent titles compared', 'Posting', 'int', (10, 300), 60,
     'How many recent titles each new title is checked against.'),
    ('early_publish_minutes', 'Post early by (min)', 'Posting', 'int', (0, 60), 0,
     'How much earlier than the cadence a finished clip may go out.'),
    ('privacy', 'YouTube privacy', 'Posting', 'choice', ('public', 'unlisted', 'private'), 'public',
     'Privacy of automatic uploads.'),
    ('ready_clips_expire_at_midnight', 'Finished clips expire at midnight IST', 'Posting', 'bool', None, True,
     'On: a finished clip that could not post today is dropped. Off: it waits for the next allowed slot.'),
    ('record_lead_minutes', 'Start recording before the slot (min)', 'Recording', 'int', (10, 120), 20,
     'When the next upload is not allowed yet, recording starts this long before the slot opens.'),
    ('capture_seconds', 'Recording length (s)', 'Recording', 'int', (120, 900), 180,
     'How much of the stream is recorded each cycle.'),
    ('capture_max_height', 'Recording quality', 'Recording', 'choice', (720, 1080), 1080,
     'Highest resolution recorded.'),
    ('streamer_cooldown_hours', 'Streamer cooldown when choosing (h)', 'Recording', 'int', (0, 72), 6,
     'A streamer recorded recently is skipped for this long when picking the next stream.'),
    ('replay_lookback_hours', 'Use past streams up to (h old)', 'Recording', 'int', (1, 48), 8,
     'When nobody is live, past broadcasts this recent can be clipped.'),
    ('max_ready_clips', 'Finished clips kept waiting', 'Recording', 'int', (1, 10), 2,
     'Most finished clips held in the posting queue.'),
    ('require_gta_moment_review', 'Only post checked GTA moments', 'Recording', 'bool', None, False,
     'A clip posts only after its moment was checked as real GTA gameplay.'),
    ('growth_experiment.enabled', 'Run the 20 s vs 30 s cut test', 'Recording', 'bool', None, False,
     'Alternates clip lengths until the test has 24 clips.'),
    ('raw_retention_days', 'Keep raw recordings (days)', 'Storage', 'int', (1, 30), 3,
     'Captures and highlight cuts are deleted after this.'),
    ('raw_segment_retention_days', 'Keep live stream segments (days)', 'Storage', 'int', (1, 30), 1,
     'Raw live-stream pieces are deleted after this.'),
    ('never_posted_cleanup_hours', 'Delete never-posted clips after (h)', 'Storage', 'int', (1, 168), 6,
     'A finished clip that was never posted and is not queued is deleted for good this long after its last change.'),
    ('posted_cleanup_hours', 'Remove posted clips from the laptop after (h)', 'Storage', 'int', (1, 168), 6,
     'Once a posted clip is backed up to Drive and checked, its folder is removed after this.'),
)
DEFAULTS = {row[0]: row[5] for row in SETTINGS}
SPEC = {row[0]: row for row in SETTINGS}


def get(config, path):
    value = config
    for part in path.split('.'):
        if not isinstance(value, dict) or part not in value:
            return DEFAULTS[path]
        value = value[part]
    return value


def value(config, path):
    """The value the scheduler uses: what scheduler.json says, else the starting default."""
    return get(config if isinstance(config, dict) else {}, path)


def describe(config):
    rows = []
    for path, label, group, kind, limits, _default, help_text in SETTINGS:
        row = {'key': path, 'label': label, 'group': group, 'kind': kind, 'value': value(config, path), 'help': help_text}
        if kind in ('int', 'float'):
            row['min'], row['max'] = limits
        elif kind == 'choice':
            row['choices'] = list(limits)
        rows.append(row)
    return rows


def validate(changes):
    if not isinstance(changes, dict) or not changes:
        raise ValueError('Change at least one setting.')
    clean = {}
    for path, new in changes.items():
        if path not in SPEC:
            raise ValueError(f'{path} is not a Shotsnip setting.')
        _, label, _, kind, limits, _, _ = SPEC[path]
        if kind == 'bool':
            ok = type(new) is bool
        elif kind == 'int':
            ok = type(new) is int and limits[0] <= new <= limits[1]
        elif kind == 'float':
            ok = type(new) in (int, float) and limits[0] <= new <= limits[1]
            new = round(float(new), 2) if ok else new
        else:
            ok = new in limits and type(new) is type(limits[0])
        if not ok:
            rule = (f'from {limits[0]} to {limits[1]}' if kind in ('int', 'float')
                    else 'on or off' if kind == 'bool' else 'one of ' + ', '.join(map(str, limits)))
            raise ValueError(f'{label} must be {rule}.')
        clean[path] = new
    return clean


def apply(config, changes):
    config = copy.deepcopy(config if isinstance(config, dict) else {})
    for path, new in changes.items():
        parts = path.split('.')
        node = config
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = new
    return config


def fill(config):
    """scheduler.json with every owner setting written out (existing values kept)."""
    return apply(config, {path: value(config, path) for path in DEFAULTS})
