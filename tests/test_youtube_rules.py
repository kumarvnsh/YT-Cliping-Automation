from datetime import datetime, timezone
from pathlib import Path

import pytest

import scheduled_clips as worker
import youtube_rules as rules
from clip_titles import upload_title

NOW = 1_800_000_000.0


def uploaded(hours_ago, channel, title):
    at = datetime.fromtimestamp(NOW - hours_ago * 3600, timezone.utc).isoformat()
    return {'status': 'uploaded', 'video_id': 'x', 'finished_at': at, 'source': {'channel': channel}, 'title': title}


def test_burst_is_spaced_and_capped(tmp_path):
    state = {'uploads': [uploaded(0.02, 'IShowSpeed', 'IShowSpeed crashes into a sidewalk')]}
    with pytest.raises(rules.RuleHold) as hold:  # the 2026-11-21 burst: four uploads inside one minute
        rules.check({'channel': 'KaiCenat'}, 'Kai checks out the ocean view | KaiCenat | GTA 6', state, {}, tmp_path, at=NOW)
    assert hold.value.kind == 'spacing'
    state = {'uploads': [uploaded(h, c, t) for h, c, t in (
        (20, 'A', 'Boat chase ends in a fistfight'), (12, 'B', 'Plane lands in a field'), (4, 'C', 'Bank heist goes wrong'))]}
    assert rules.spacing_wait(state, {}, at=NOW) == pytest.approx(NOW - 20 * 3600 + 86400)
    assert rules.spacing_wait(state, {'youtube_rules': {'max_per_day': 4}}, at=NOW) is None


def test_same_streamer_never_twice_in_a_row(tmp_path):
    state = {'uploads': [uploaded(30, 'Kai Cenat', 'Kai buys a house'), uploaded(3, 'Kai Cenat Live', 'Kai checks his phone in the gym')]}
    with pytest.raises(rules.RuleHold) as hold:
        rules.check({'channel': 'KaiCenat'}, 'Plane crash lands in the water | KaiCenat | GTA 6', state, {}, tmp_path, at=NOW)
    assert hold.value.kind == 'streamer'
    rules.check({'channel': 'IShowSpeed'}, 'Speed gets lost in a tall building | IShowSpeed | GTA 6', state, {}, tmp_path, at=NOW)


def test_vague_and_repeated_titles_are_refused(tmp_path):
    state = {'uploads': [uploaded(30, 'IShowSpeed', 'Plane crash landing in a field | IShowSpeed | GTA 6')]}
    source = {'channel': 'Kai Cenat Live', 'game': 'gta6'}
    for title, kind in ((upload_title(source, 'Kai Cenat Live live moment'), 'title'),
                        (upload_title(source, 'Kai Cenat Live: Plane Crash Landing in Field'), 'title')):
        with pytest.raises(rules.RuleHold) as hold:
            rules.check(source, title, state, {}, tmp_path, at=NOW)
        assert hold.value.kind == kind
    rules.check(source, upload_title(source, 'Kai talks bills while holding a baby'), state, {}, tmp_path, at=NOW)


def test_titles_lead_with_the_moment_not_the_streamer():
    source = {'channel': 'Kai Cenat Live', 'game': 'gta6'}
    assert upload_title(source, 'Kai Cenat Live: Plane Crash Into Water') == 'Plane Crash Into Water | Kai Cenat Live | GTA 6'
    assert upload_title(source, 'Kai Cenat Live | GTA 6 | Boat chase') == 'Boat chase | Kai Cenat Live | GTA 6'
    title = upload_title(source, 'Boat chase')
    assert upload_title(source, title) == title


def test_ready_queue_skips_a_repeat_streamer_and_drops_a_vague_title(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, 'WORK', tmp_path)
    monkeypatch.setattr(worker, 'ROOT', tmp_path)
    monkeypatch.setattr(worker, 'source_allowed', lambda _: True)
    worker.save(tmp_path / 'scheduler.json', {'enabled': True})
    monkeypatch.setattr(worker.time, 'time', lambda: NOW)
    clips = []
    for name, channel, hook in (('a', 'Kai Cenat', 'Kai jumps off a roof'), ('b', 'Rubius', 'Rubius live moment'),
                                ('c', 'IShowSpeed', 'Speed steals a fire truck')):
        folder = tmp_path / name; folder.mkdir()
        (folder / 'clip.mp4').write_bytes(name.encode())
        worker.save(folder / 'metadata.json', {'title': hook, 'description': 'd'})
        clips.append({'file': str(folder / 'clip.mp4'), 'ready_at': NOW, 'source': {'channel': channel, 'game': 'gta6'}})
    state = {'uploads': [uploaded(5, 'Kai Cenat', 'Kai buys a house')], 'ready_clips': clips}
    sent = []

    class API:
        def videos(self): return self
        def insert(self, **kw): sent.append(kw['body']['snippet']['title']); return self
        def execute(self, **kw): return {'id': 'new', 'status': {'privacyStatus': 'public'}}
    monkeypatch.setattr(worker, 'youtube', lambda: API())
    monkeypatch.setitem(__import__('sys').modules, 'facebook_shotsnip', type('fb', (), {'crosspost': staticmethod(lambda *a: None)}))
    worker.publish_ready(state, {})
    assert sent == ['Speed steals a fire truck | IShowSpeed | GTA 6']
    assert [Path(c['file']).parent.name for c in state['ready_clips']] == ['a']  # Kai waits; the vague Rubius clip is gone


def test_commentary_titles_name_the_evidenced_game():
    from clip_titles import game_hashtag, with_game
    gta6 = {'game': 'gta6', 'channel': 'Ludwig', 'title': 'DAY 4 NON-LETHAL GTA VI RUN'}
    assert with_game('Ludwig in a tense mission setup', gta6) == 'GTA 6: Ludwig in a tense mission setup'
    assert with_game('GTA 6 bank job goes wrong', gta6) == 'GTA 6 bank job goes wrong'   # never twice
    assert with_game('Grand Theft Auto VI heist', gta6) == 'Grand Theft Auto VI heist'
    assert with_game('Plane lands in a field', {'game': 'gta', 'title': ''}) == 'Plane lands in a field'  # unproven: untouched
    assert len(with_game('x' * 120, gta6)) <= 100
    assert game_hashtag(gta6) == '#GTA6' and game_hashtag({'game': 'gta', 'title': ''}) == ''
