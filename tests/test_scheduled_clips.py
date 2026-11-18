import json
from pathlib import Path
import pytest
import scheduled_clips as worker
from stream_sources import choose


def test_highest_measured_viewers_and_target_tie():
    target={'viewers':100,'targeted':True}
    other={'viewers':101,'targeted':False}
    assert choose([target,other]) is other
    other['viewers']=100
    assert choose([other,target]) is target
    assert choose([]) is None


def test_uncertain_upload_survives_and_blocks_retry(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'WORK',tmp_path/'work')
    monkeypatch.setattr(worker,'ROOT',tmp_path)
    worker.save(tmp_path/'scheduler.json',{'enabled':True})
    worker.save(worker.WORK/'state.json',{'uploads':[{'status':'uncertain'}]})
    monkeypatch.setattr(worker,'scan',lambda: pytest.fail('must not discover or retry an uncertain upload'))
    with pytest.raises(RuntimeError,match='Unresolved upload'):
        worker.tick()
    assert worker.read(worker.WORK/'state.json',{})['uploads'][0]['status']=='uncertain'


def test_cadence_skips_scan_and_capture(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'WORK',tmp_path/'work')
    monkeypatch.setattr(worker,'ROOT',tmp_path)
    worker.save(tmp_path/'scheduler.json',{'enabled':True,'scan_minutes':30})
    worker.save(worker.WORK/'state.json',{'uploads':[],'last_scan_at':worker.time.time()})
    monkeypatch.setattr(worker,'scan',lambda: pytest.fail('scan not due'))
    assert worker.tick()['status']=='scan_not_due'


def test_old_game_cannot_publish(monkeypatch):
    monkeypatch.setattr(worker,'active_game',lambda:'gta6')
    monkeypatch.setattr(worker,'youtube',lambda:pytest.fail('must not contact upload API'))
    with pytest.raises(RuntimeError,match='old-game'):
        worker.publish(Path('unused.mp4'),{}, {'game':'gta5'}, {'uploads':[]})


def test_timeout_is_durable_and_second_attempt_never_calls_youtube(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'ROOT',tmp_path)
    monkeypatch.setattr(worker,'WORK',tmp_path/'work')
    monkeypatch.setattr(worker,'active_game',lambda:'gta5')
    worker.save(tmp_path/'scheduler.json',{'enabled':True})
    video=tmp_path/'clip.mp4'; video.write_bytes(b'finished video fixture')
    calls=[]
    class Upload:
        def videos(self): return self
        def insert(self,**kwargs):
            calls.append(kwargs)
            return self
        def execute(self,**kwargs): raise TimeoutError('response lost')
    monkeypatch.setattr(worker,'youtube',lambda:Upload())
    state={'uploads':[]}
    with pytest.raises(RuntimeError,match='uncertain'):
        worker.publish(video,{'title':'clip','description':'source'}, {'game':'gta5'}, state)
    persisted=worker.read(worker.WORK/'state.json',{})
    assert persisted['uploads'][0]['status']=='uncertain'
    assert persisted['uploads'][0]['marker'] in calls[0]['body']['snippet']['description']
    with pytest.raises(RuntimeError,match='Unresolved'):
        worker.publish(video,{}, {'game':'gta5'}, persisted)
    assert len(calls)==1


def test_non_english_generated_hook_uses_neutral_source_label():
    assert worker.usable_hook({'title':'\u092e\u0947\u0930\u0947 \u092d\u093e\u0908'},
        {'channel':'Creator','game':'gta5'})=="Creator's GTA V live moment"


def test_ready_clip_waits_then_publishes(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'WORK',tmp_path)
    monkeypatch.setattr(worker,'active_game',lambda:'gta5')
    clip=tmp_path/'ready.mp4'; clip.write_bytes(b'video')
    row={'file':str(clip),'ready_at':worker.time.time(),'source':{'game':'gta5'}}
    state={'uploads':[],'last_upload_at':worker.time.time(),'ready_clips':[row]}
    sent=[]
    monkeypatch.setattr(worker,'publish',lambda *args: sent.append(args) or {'status':'uploaded'})
    assert worker.publish_ready(state,{'post_minutes':120}) is None
    assert not sent and len(state['ready_clips'])==1
    state['last_upload_at']=0
    assert worker.publish_ready(state,{'post_minutes':120})['status']=='uploaded'
    assert len(sent)==1 and not state['ready_clips']


def test_old_game_queue_is_discarded(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'WORK',tmp_path)
    monkeypatch.setattr(worker,'active_game',lambda:'gta6')
    clip=tmp_path/'ready.mp4'; clip.write_bytes(b'video')
    state={'uploads':[],'ready_clips':[{'file':str(clip),'ready_at':worker.time.time(),'source':{'game':'gta5'}}]}
    monkeypatch.setattr(worker,'publish',lambda *a:pytest.fail('wrong-game publish'))
    assert worker.publish_ready(state,{}) is None
    assert state['ready_clips']==[]


def test_ready_clip_releases_at_90_minutes(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'WORK',tmp_path)
    monkeypatch.setattr(worker,'active_game',lambda:'gta5')
    monkeypatch.setattr(worker.time,'time',lambda:100000)
    clip=tmp_path/'ready.mp4'; clip.write_bytes(b'video')
    state={'uploads':[],'last_upload_at':100000-5399,'ready_clips':[{'file':str(clip),
        'ready_at':99000,'source':{'game':'gta5'}}]}
    sent=[]
    monkeypatch.setattr(worker,'publish',lambda *args:sent.append(args) or {'status':'uploaded'})
    config={'post_minutes':120,'early_publish_minutes':30}
    assert worker.publish_ready(state,config) is None
    state['last_upload_at']-=1
    assert worker.publish_ready(state,config)['status']=='uploaded'
    assert len(sent)==1


def test_failed_capture_immediately_retries_without_resetting_upload_timer(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'WORK',tmp_path/'work'); monkeypatch.setattr(worker,'ROOT',tmp_path)
    worker.save(tmp_path/'scheduler.json',{'enabled':True})
    calls=[]
    def attempt(scan_only):
        calls.append(1)
        if len(calls)==1:
            worker.save(worker.WORK/'state.json',{'failed_phase':'capturing','uploads':[],'last_upload_at':123})
            raise RuntimeError('capture failed')
        state=worker.read(worker.WORK/'state.json',{})
        assert state['retry_pending'] and state['last_upload_at']==123
        return {'status':'uploaded'}
    monkeypatch.setattr(worker,'attempt_tick',attempt)
    assert worker.tick()['status']=='uploaded' and len(calls)==2


def test_uncertain_upload_never_retries(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'WORK',tmp_path/'work'); monkeypatch.setattr(worker,'ROOT',tmp_path)
    worker.save(tmp_path/'scheduler.json',{'enabled':True})
    calls=[]
    def attempt(scan_only):
        calls.append(1)
        worker.save(worker.WORK/'state.json',{'failed_phase':'uploading','uploads':[{'status':'uncertain'}]})
        raise RuntimeError('upload uncertain')
    monkeypatch.setattr(worker,'attempt_tick',attempt)
    with pytest.raises(RuntimeError): worker.tick()
    assert len(calls)==1


def test_known_youtube_rejection_does_not_create_uncertain_hold(tmp_path,monkeypatch):
    from googleapiclient.errors import HttpError
    from httplib2 import Response
    monkeypatch.setattr(worker,'WORK',tmp_path/'work'); monkeypatch.setattr(worker,'ROOT',tmp_path)
    monkeypatch.setattr(worker,'active_game',lambda:'gta5')
    worker.save(tmp_path/'scheduler.json',{'enabled':True})
    video=tmp_path/'clip.mp4'; video.write_bytes(b'video')
    class API:
        def videos(self): return self
        def insert(self,**kw): return self
        def execute(self,**kw):
            raise HttpError(Response({'status':'403'}),b'{"error":{"errors":[{"reason":"uploadLimitExceeded"}]}}')
    monkeypatch.setattr(worker,'youtube',lambda:API())
    state={'uploads':[]}
    with pytest.raises(RuntimeError,match='uploadLimitExceeded'):
        worker.publish(video,{'title':'test','description':'source'},{'game':'gta5'},state)
    assert state['uploads'][0]['status']=='rejected'
