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


def test_caption_language_policy(monkeypatch):
    from types import SimpleNamespace as N
    import finish_video as f
    for language in ('hi','en','es','pt','ja'):
        calls=[]
        class Model:
            def transcribe(self, source, **kwargs):
                calls.append(kwargs)
                word=N(start=0,end=1,word='hello',probability=.9)
                return iter([N(start=0,end=1,text='hello',words=[word])]),N(language=language,language_probability=.99,duration=1)
        monkeypatch.setattr(f,'load_model',lambda name:Model())
        result=f.transcribe('clip.mp4')
        assert result['language']==('hi' if language=='hi' else 'en')
        assert result['source_language']==language
        assert result['translated']==(language not in ('hi','en'))
        assert len(calls)==(1 if language in ('hi','en') else 2)
        if len(calls)==2: assert calls[-1]['task']=='translate'


def test_non_english_cache_cannot_bypass_translation(tmp_path,monkeypatch):
    import json
    import finish_video as f
    cache=tmp_path/'transcript.json'
    monkeypatch.setattr(f,'transcribe',lambda *args:{'language':'en','translated':True})
    for language in ('es','hi','en'):
        cache.write_text(json.dumps({'language':language,'segments':[]}))
        result,used=f.caption_transcript('clip.mp4',transcript_file=cache)
        assert used==(language in ('hi','en'))
        assert result['language']==('hi' if language=='hi' else 'en')


def test_missing_word_times_do_not_crash_cached_slicing():
    from clip_efficiency import slice_transcript
    assert slice_transcript({'language':'hi','segments':[{'words':None}]},0,30)['segments']==[]
    assert slice_transcript({'segments':None},0,30)['segments']==[]

def test_missing_speech_needs_explicit_reviewed_action_override(monkeypatch):
    import pytest
    import finish_video as f
    with pytest.raises(ValueError,match='No speech'):f.check_caption_evidence({'segments':[]},'clip')


def test_reviewed_action_with_sound_does_not_get_invented_captions(monkeypatch):
    from types import SimpleNamespace as N
    import finish_video as f
    monkeypatch.setattr(f.subprocess,'run',lambda *a,**kw:N(returncode=0,stderr='max_volume: -5.0 dB'))
    transcript={'segments':[]}
    f.check_caption_evidence(transcript,'clip',True)
    assert transcript['segments']==[]
    assert transcript['review_status']=='speech_not_recognized_no_generated_captions'


def test_reviewed_action_still_rejects_muted_source(monkeypatch):
    import pytest
    from types import SimpleNamespace as N
    import finish_video as f
    monkeypatch.setattr(f.subprocess,'run',lambda *a,**kw:N(returncode=0,stderr='max_volume: -91.0 dB'))
    with pytest.raises(ValueError,match='No audible'):f.check_caption_evidence({'segments':[]},'clip',True)
