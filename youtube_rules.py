"""Upload rules every Shotsnip YouTube upload must pass, on every path (automatic, manual, run-now, batch).

YouTube stopped recommending the channel on 2026-11-21 after four uploads in one minute with the same title,
followed by many near-identical "Kai Cenat Live | GTA 6 | ..." titles from one stream. These rules stop that:
  - spacing:   at least `min_gap_minutes` between YouTube uploads, and at most `max_per_day` in any 24 hours
  - variety:   never the same streamer twice in a row, nor within `same_streamer_hours`
  - titles:    each title must describe the moment, and must not repeat or nearly repeat a recent title.
               Broad animal words (funny, animals, pets) do not count, so "Funny animal clips" every time is refused
               while "Pets were NOT having it today" next to "This little guy chose violence" is fine.
Settings live in scheduler.json under "youtube_rules" and are set from Studio HQ (owner_settings.py).
"""
import json
import re
import time
from datetime import datetime
from pathlib import Path

from owner_settings import DEFAULTS as _OWNER
DEFAULTS = {k.split('.', 1)[1]: v for k, v in _OWNER.items() if k.startswith('youtube_rules.')}
FILLER = set('''a an the of in on at to and or with his her their its is are was it this that s
live moment moments reaction reacts reacting highlight highlights stream streams streamer streaming gameplay clip clips
game gaming gta grand theft auto v vi 5 6 playing plays played progress update day early access
funny animal animals pet pets video videos compilation shorts'''.split())  # Shotsnip's broad words: the hook must differ


class RuleHold(RuntimeError):
    """The upload must wait (spacing) or be dropped (streamer, title); nothing was sent to YouTube."""

    def __init__(self, message, kind, wait_until=None):
        super().__init__(message)
        self.kind = kind  # 'spacing' (wait), 'streamer' (try another clip first) or 'title' (drop this clip)
        self.wait_until = wait_until


def settings(config):
    rules = config.get('youtube_rules') if isinstance(config.get('youtube_rules'), dict) else {}
    return {**DEFAULTS, **rules}


def streamer_key(name):
    key = re.sub(r'[^a-z0-9]', '', str(name or '').casefold())
    return re.sub(r'(live|tv|official|gaming)$', '', key) or key


def words(text):
    return re.findall(r"[a-z0-9]+", str(text or '').casefold().replace("'s ", ' '))


def meaningful(title, streamer=''):
    """The words that say what happens, without the streamer's name, the game, or filler."""
    names = set(words(streamer)) | {streamer_key(streamer)}
    return [w for w in words(title) if w not in FILLER and w not in names and streamer_key(w) not in names]


def _time(row):
    for key in ('finished_at', 'started_at'):
        try:
            return datetime.fromisoformat(str(row.get(key))).timestamp()
        except (TypeError, ValueError):
            continue
    return None


def youtube_uploads(state):
    rows = [r for r in state.get('uploads', []) if r.get('status') == 'uploaded' and r.get('video_id')]
    return sorted(((t, r) for r in rows if (t := _time(r)) is not None), key=lambda pair: pair[0])


def recent_titles(state, root, limit):
    titles = [r.get('title') for _, r in youtube_uploads(state) if r.get('title')]
    try:  # uploads made before titles were recorded: the dashboard's public snapshot has them
        listed = json.loads((Path(root) / 'work' / 'dashboard' / 'analytics.json').read_text(encoding='utf-8')).get('videos', [])
        titles += [v.get('title') for v in listed if v.get('title')]
    except (OSError, ValueError, AttributeError):
        pass
    return titles[-limit * 2:]


def spacing_wait(state, config, at=None):
    """When the next YouTube upload may go out; None if it may go now."""
    rules, at = settings(config), at or time.time()
    times = [t for t, _ in youtube_uploads(state)]
    last = max(times + [state.get('last_youtube_upload_at') or 0])
    waits = []
    if last and at - last < rules['min_gap_minutes'] * 60:
        waits.append(last + rules['min_gap_minutes'] * 60)
    day = [t for t in times if at - t < 86400]
    if len(day) >= rules['max_per_day']:
        waits.append(sorted(day)[len(day) - rules['max_per_day']] + 86400)
    return max(waits) if waits else None


def streamer_problem(source, state, config, at=None):
    rules, at = settings(config), at or time.time()
    key = streamer_key(source.get('channel'))
    if not key:
        return None
    uploads = youtube_uploads(state)
    if uploads and streamer_key(uploads[-1][1].get('source', {}).get('channel')) == key:
        return f"{source.get('channel')} was also the last upload; a different streamer must go next"
    for t, row in uploads:
        if at - t < rules['same_streamer_hours'] * 3600 and streamer_key(row.get('source', {}).get('channel')) == key:
            return f"{source.get('channel')} was uploaded less than {rules['same_streamer_hours']} hours ago"
    return None


def title_problem(title, source, state, config, root):
    rules = settings(config)
    mine = meaningful(title, source.get('channel'))
    if len(mine) < 2:
        return f'title "{title}" does not say what happens in the clip'
    for old in recent_titles(state, root, rules['recent_titles']):
        theirs = meaningful(old, source.get('channel'))
        if not theirs:
            continue
        a, b = set(mine), set(theirs)
        if a == b or len(a & b) / len(a | b) >= rules['similar_titles']:
            return f'title "{title}" repeats the recent title "{old}"'
    return None


def check(source, title, state, config, root, at=None):
    """Raise RuleHold when this upload would break a rule. Spacing first, so a waiting clip keeps its place."""
    wait = spacing_wait(state, config, at)
    if wait:
        when = datetime.fromtimestamp(wait).strftime('%d %b %H:%M')
        raise RuleHold(f'YouTube spacing rule: next upload allowed after {when}', 'spacing', wait_until=wait)
    problem = streamer_problem(source, state, config, at)
    if problem:
        raise RuleHold('YouTube variety rule: ' + problem, 'streamer')
    problem = title_problem(title, source, state, config, root)
    if problem:
        raise RuleHold('YouTube title rule: ' + problem, 'title')
