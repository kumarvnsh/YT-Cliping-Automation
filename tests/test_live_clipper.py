from pathlib import Path
import pytest
from live_clipper import SpikeDetector, select_segments
from youtube_live_chat import continuation, embedded_json, messages


def test_spike_uses_preceding_baseline_unique_users_and_cooldown():
    detector = SpikeDetector(window=5, baseline=3, minimum=4, ratio=3, cooldown=20)
    for t in (0, 5, 10):
        detector.advance(t, 'same')
        detector.advance(t + 1, 'same')
    for i in range(5):
        detector.advance(15 + i / 10, str(i))
    event = detector.advance(20)[0]
    assert event['spike'] and event['baseline'] == 1
    for i in range(10):
        detector.advance(20 + i / 10, str(i))
    assert not detector.advance(25)[0]['spike']


def test_quiet_stream_has_minimum_and_requires_warmup():
    detector = SpikeDetector(baseline=2, minimum=4)
    for i in range(20):
        detector.advance(i / 100, str(i))
    assert not detector.advance(5)[0]['spike']
    detector.advance(10)
    detector.advance(11, 'one')
    assert not detector.advance(15)[0]['spike']


def test_buffer_selection_offsets_and_rejects_missing_history():
    segments = [(Path(str(i)), i, i + 2) for i in range(10, 50, 2)]
    chosen, offset = select_segments(segments, 47, 30)
    assert chosen[0][1] == 16 and offset == 1
    with pytest.raises(ValueError):
        select_segments(segments, 35, 30)
    with pytest.raises(ValueError):
        select_segments(segments[:4] + segments[5:], 47, 30)


def test_chat_parser_uses_live_configuration_and_handles_current_message_shape():
    data = embedded_json('window["ytInitialData"] = {"a":1}; extra',
                         r'window\["ytInitialData"\]\s*=\s*')
    assert data == {'a': 1}
    token, interval = continuation({'continuations': [
        {'timedContinuationData': {'continuation': 'next', 'timeoutMs': 2000}}]})
    assert (token, interval) == ('next', 2)
    parsed = list(messages({'actions': [{'addChatItemAction': {'item': {
        'liveChatTextMessageRenderer': {'id': 'message1', 'authorExternalChannelId': 'user1',
                                        'timestampUsec': '1700000000000000'}}}}]}))
    assert parsed == [{'id': 'message1', 'author': 'user1', 'timestamp': 1700000000}]
    with pytest.raises(ValueError):
        continuation({})
    with pytest.raises(ValueError):
        embedded_json('outdated browser', r'window\["ytInitialData"\]\s*=\s*')


def test_segment_listing_retries_while_ffmpeg_locks_it(tmp_path, monkeypatch):
    import live_clipper
    (tmp_path / 'segment-000000.ts').write_bytes(b'x')
    listing = tmp_path / 'segments.csv'
    listing.write_text('segment-000000.ts,0.0,2.0\n', encoding='utf-8')
    original, calls = Path.read_text, []
    def locked_once(self, *args, **kwargs):
        if self.name == 'segments.csv' and not calls:
            calls.append(1)
            raise PermissionError(13, 'The process cannot access the file', str(self))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', locked_once)
    monkeypatch.setattr(live_clipper.time, 'sleep', lambda _: None)
    assert live_clipper.completed_segments(tmp_path) == [(tmp_path / 'segment-000000.ts', 0.0, 2.0)]

def test_render_uses_given_segments_without_rereading_locked_listing(tmp_path, monkeypatch):
    import live_clipper
    segments = [(tmp_path / 'segment-000000.ts', 0.0, 40.0)]
    def locked(_folder):
        raise PermissionError(13, 'Permission denied', str(tmp_path / 'segments.csv'))
    monkeypatch.setattr(live_clipper, 'completed_segments', locked)
    sentinel = RuntimeError('reached ffmpeg')
    def fake_run(*args, **kwargs):
        raise sentinel
    monkeypatch.setattr(live_clipper.subprocess, 'run', fake_run)
    monkeypatch.setattr(live_clipper, 'ffmpeg', lambda: 'ffmpeg')
    with pytest.raises(RuntimeError) as caught:
        live_clipper.render(tmp_path, 35.0, 30, 'chat-spike', segments)
    assert caught.value is sentinel


def test_stall_is_not_judged_from_a_reused_locked_listing():
    from live_clipper import capture_stalled
    segments = [(Path('segment-000000.ts'), 0.0, 2.0)]
    # Loop was blocked 90 s finishing a clip, then the listing was locked this tick.
    assert not capture_stalled(False, segments, last_progress=0, now=90)
    assert capture_stalled(True, segments, last_progress=0, now=90)
    assert not capture_stalled(True, segments, last_progress=60, now=90)
    assert not capture_stalled(True, [], last_progress=0, now=90)


def test_source_resolution_preference_is_bounded():
    from live_clipper import capture_format
    assert 'height<=1080' in capture_format('https://www.youtube.com/watch?v=abcdefghijk',2160)
    assert capture_format('https://www.twitch.tv/creator',1080)=='best[height<=1080]/best'
