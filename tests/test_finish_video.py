from finish_video import ass_escape, build_subtitles, caption_groups, stamp


def test_subtitles_keep_speech_timing_and_escape_override_commands():
    transcript = {'duration': 30, 'segments': [{'start': 2, 'end': 3,
        'text': 'Look there', 'words': [
            {'start': 2, 'end': 2.4, 'word': 'Look'},
            {'start': 2.5, 'end': 3, 'word': r'{\pos(0,0)}there'}]}]}
    ass, srt, _ = build_subtitles(transcript, 'A parked car', 'Sentry')
    assert '0:00:02.00,0:00:02.50,Caption' in ass
    assert '00:00:02,000 --> 00:00:03,000' in srt
    assert r'{\pos(0,0)}' not in ass
    assert ass_escape('a\nb') == r'a\Nb'
    assert stamp(59.9999, True) == '00:01:00,000'


def test_captions_split_at_silence_and_do_not_invent_word_times():
    transcript = {'segments': [{'text': 'a b c', 'start': 0, 'end': 5,
        'words': [{'word': 'a', 'start': 0, 'end': .4},
                  {'word': 'b', 'start': .5, 'end': 1},
                  {'word': 'c', 'start': 4, 'end': 5}]},
        {'text': 'No word timing', 'start': 6, 'end': 8}]}
    groups = caption_groups(transcript)
    assert len(groups) == 3
    assert groups[-1] == [{'word': 'No word timing', 'start': 6, 'end': 8}]


def test_zero_duration_words_are_preserved_with_the_next_word():
    transcript = {'segments': [{'text': 'I mean', 'start': 1, 'end': 2, 'words': [
        {'word': ' I', 'start': 1, 'end': 1},
        {'word': ' mean', 'start': 1, 'end': 2}]}]}
    groups = caption_groups(transcript)
    assert groups[0][0]['word'].strip() == 'I mean'
