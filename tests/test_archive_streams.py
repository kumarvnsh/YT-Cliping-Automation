from datetime import date,datetime,timedelta
import json
from archive_streams import replay_candidate,chat_peak


def test_only_yesterday_completed_streams_qualify(monkeypatch):
    monkeypatch.setattr('archive_streams.active_game',lambda:'gta5')
    info={'id':'abc','title':'GTA V stream','live_status':'was_live','duration':4000,
          'view_count':10000,'upload_date':'20261117','channel':'Creator'}
    assert replay_candidate(info,today=date(2026,11,18))['archive']
    assert replay_candidate({**info,'live_status':'not_live'},today=date(2026,11,18)) is None
    assert replay_candidate({**info,'upload_date':'20261116'},today=date(2026,11,18)) is None


def test_chat_peak_uses_replay_offset_and_unique_chatters():
    def message(at,author):
        return json.dumps({'replayChatItemAction':{'videoOffsetTimeMsec':str(at*1000),
            'actions':[{'addChatItemAction':{'item':{'liveChatTextMessageRenderer':{'authorExternalChannelId':author}}}}]}})
    p=chat_peak([message(10,'a'),message(10,'a'),message(40,'b'),message(50,'c')],100)
    assert p=={'end':60,'unique_chatters':2}


def test_recent_streams_use_end_time_and_an_eight_hour_window():
    from game_schedule import IST
    now=datetime(2026,11,18,22,tzinfo=IST)
    start=(now-timedelta(hours=12)).timestamp()
    info={'id':'abc','title':'GTA 4 stream','live_status':'was_live','duration':6*3600,
          'view_count':5000,'release_timestamp':start,'channel':'Target'}
    candidate=replay_candidate(info,True,lookback_hours=8,now=now)
    assert candidate['recent_replay'] and candidate['archive_age_hours']==6
    assert replay_candidate({**info,'duration':3*3600},True,lookback_hours=8,now=now) is None
    assert replay_candidate({**info,'live_status':'not_live'},True,lookback_hours=8,now=now) is None
    assert replay_candidate({**info,'release_timestamp':(now-timedelta(hours=9)).timestamp(),'duration':3600},True,lookback_hours=8,now=now)
    assert replay_candidate({**info,'release_timestamp':(now-timedelta(hours=9,seconds=1)).timestamp(),'duration':3600},True,lookback_hours=8,now=now) is None


def test_missed_target_replay_wins_before_larger_live_stream(monkeypatch):
    from stream_sources import choose_varied
    missed={'id':'missed','channel':'Missed target','viewers':100,'targeted':True,'archive':True,'recent_replay':True}
    live={'id':'live','channel':'Live target','viewers':100000,'targeted':True}
    assert choose_varied([live,missed],{})['id']=='missed'
    state={'capture_history':[{'at':0,'source':missed}]}
    assert choose_varied([live,missed],state)['id']=='live'


def test_failed_live_attempt_does_not_permanently_block_missed_replay():
    from stream_sources import choose_varied
    import time
    replay={'id':'missed','channel':'Target','viewers':100,'targeted':True,'archive':True,'recent_replay':True}
    live={**replay,'archive':False}
    state={'capture_history':[{'at':time.time()-7*3600,'source':live}]}
    assert choose_varied([replay],state)==replay
    state['uploads']=[{'source':live,'status':'uploaded','finished_at':datetime.now().isoformat()}]
    assert choose_varied([replay],state) is None


def test_heatmap_and_chat_agreement_outranks_a_lone_peak():
    from archive_streams import ranked_moments
    info={'duration':1800,'heatmap':[{'start_time':280,'end_time':320,'value':.8},
                                   {'start_time':1100,'end_time':1140,'value':1}]}
    moments=ranked_moments(info,{9:set(str(i) for i in range(30))})
    assert moments[0]['method']=='combined_replay_signals'
    assert moments[0]['source_time']==300
    assert moments[0]['unique_chatters']==30


def test_replay_window_uses_global_heatmap_instead_of_tail(tmp_path,monkeypatch):
    import archive_streams as archive
    monkeypatch.setattr(archive.subprocess,'run',lambda *a,**kw:None)
    info={'duration':7200,'webpage_url':'https://www.youtube.com/watch?v=abc',
          'heatmap':[{'start_time':1800,'end_time':1860,'value':1}]}
    offset,signal=archive.replay_window(info,tmp_path,300)
    assert offset<=1830<=offset+300
    assert offset!=7200-300 and signal['method']=='most_replayed'


def test_replay_without_signals_is_skipped_not_an_arbitrary_tail(tmp_path,monkeypatch):
    import archive_streams as archive
    import pytest
    monkeypatch.setattr(archive.subprocess,'run',lambda *a,**kw:None)
    monkeypatch.setattr(archive,'audio_moment',lambda info:None)
    with pytest.raises(RuntimeError,match='no usable engagement'):
        archive.replay_window({'duration':7200,'webpage_url':'https://www.youtube.com/watch?v=abc'},tmp_path,300)
