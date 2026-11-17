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
