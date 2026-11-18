from pathlib import Path
import pytest
import kitty_recovery as recovery
import scheduled_clips as worker


@pytest.fixture(autouse=True)
def isolated_worker_state(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'WORK',tmp_path/'worker')


def test_only_known_hardware_decoder_errors_are_recoverable():
    assert recovery.decoder_failure('ffmpeg visual decode failed: DXVA2 Failed to create Direct3D device')
    assert not recovery.decoder_failure('ffmpeg visual decode failed: Invalid data found')
    assert not recovery.decoder_failure('YouTube uploadLimitExceeded')


def test_failed_editor_recovers_same_capture_without_submitting_another_job(tmp_path, monkeypatch):
    video=tmp_path/'capture.mp4';video.write_bytes(b'capture')
    monkeypatch.setattr(worker,'WORK',tmp_path)
    calls=[]
    def api(path,payload=None):
        calls.append(path)
        if path=='/health/preflight':return {'checks':[]}
        if path=='/videos/local':return {'job_id':28}
        if path=='/queue/resume':return {}
        if path=='/queue':return {'processing':[],'queued':[]}
        return {'status':'failed','video_id':'local_abc','error':'ffmpeg visual decode failed: Direct3D device creation failed'}
    monkeypatch.setattr(worker,'api',api)
    seen=[]
    def recover(path,identity,heartbeat):
        seen.append((path,identity));heartbeat()
        return {'end_s':60,'score':70}
    monkeypatch.setattr(recovery,'recover',recover)
    state={'uploads':[]}
    assert worker.kitty_select({'title':'Title','channel':'Creator'},video,state)['score']==70
    assert seen==[(video,'local_abc')]
    assert calls.count('/videos/local')==1
    assert state['last_repair']['status']=='recovered'


def test_recovery_refuses_overlap_with_other_editor_work(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'WORK',tmp_path)
    def api(path,payload=None):
        if path=='/health/preflight':return {'checks':[]}
        if path=='/videos/local':return {'job_id':28}
        if path=='/queue/resume':return {}
        if path=='/queue':return {'processing':[{'id':29}]}
        return {'status':'failed','error':'ffmpeg visual decode failed: DXVA2 device creation failed'}
    monkeypatch.setattr(worker,'api',api)
    monkeypatch.setattr(recovery,'recover',lambda *a:pytest.fail('must not overlap'))
    with pytest.raises(RuntimeError,match='another active'):
        worker.kitty_select({'title':'Title','channel':'Creator'},tmp_path/'capture.mp4',{'uploads':[]})


def test_recovery_timeout_stops_its_child(tmp_path,monkeypatch):
    class Child:
        killed=False
        def poll(self):return -1 if self.killed else None
        def kill(self):self.killed=True
        def wait(self):return -1
    child=Child()
    monkeypatch.setattr(recovery.subprocess,'Popen',lambda *a,**kw:child)
    with pytest.raises(RuntimeError,match='exceeded'):
        recovery.recover(tmp_path/'capture.mp4','local_abc',timeout=-1)
    assert child.killed


def test_recovery_does_not_accept_unverified_video_ids(tmp_path):
    with pytest.raises(RuntimeError,match='verified local'):
        recovery.recover(tmp_path/'capture.mp4','../private')


def test_state_write_keeps_backup_and_refuses_incomplete_ledger(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'WORK',tmp_path)
    path=tmp_path/'state.json'
    original={'uploads':[{'status':'quarantined','sha256':'keep'}]}
    worker.save(path,original)
    worker.save(path,{**original,'phase':'analyzing'})
    assert worker.read(tmp_path/'state.previous.json',{})==original
    with pytest.raises(RuntimeError,match='incomplete state'):
        worker.save(path,{'phase':'error'})
    assert worker.read(path,{})['uploads']==original['uploads']
