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
