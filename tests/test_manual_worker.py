from pathlib import Path
import manual_jobs as jobs
import manual_worker as worker
import finish_video


def test_exact_preview_finishes_without_publishing(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    monkeypatch.setattr(worker.w, 'ROOT', tmp_path)
    monkeypatch.setattr(worker.w, 'WORK', tmp_path/'work/scheduler')
    identity=jobs.enqueue({'mode':'exact','url':'https://youtu.be/abcdefghijk',
        'ranges':'0 - 25','hook':'Preview test'})[0]
    monkeypatch.setattr(worker,'inspect',lambda url:dict(id='abcdefghijk',channel='Test creator',duration=120))
    raw=tmp_path/'raw.mp4'; raw.write_bytes(b'test')
    monkeypatch.setattr(worker,'exact_download',lambda *args:raw)
    finished=tmp_path/'finished.mp4'; finished.write_bytes(b'test')
    worker.w.save(tmp_path/'metadata.json',{'title':'Preview test'})
    monkeypatch.setattr(finish_video,'finish',lambda *args,**kwargs:finished)
    def forbidden(*args,**kwargs): raise AssertionError('A preview must not publish')
    monkeypatch.setattr(worker,'publish_result',forbidden)
    worker.process(jobs.claim())
    job=jobs.get(identity)
    assert job['status']=='ready'
    assert job['result']['file']==str(finished)
    assert worker.w.read(worker.w.WORK/'state.json',{})['selected']['channel']=='Test creator'


def test_selected_youtube_privacy_is_forwarded(tmp_path,monkeypatch):
    video=tmp_path/'clip.mp4'; video.write_bytes(b'test')
    worker.w.save(tmp_path/'metadata.json',{})
    observed={}
    def publish(*args,**kwargs):
        observed.update(kwargs)
        return {'video_id':'id','returned_privacy':'private'}
    monkeypatch.setattr(worker.w,'publish',publish)
    worker.publish_result({'options':{'platforms':['youtube'],'privacy':'private'}},
        {'file':str(video),'source':{}},{})
    assert observed=={'destinations':['youtube'],'privacy':'private','manual':True}


def test_state_save_retries_transient_windows_lock(tmp_path,monkeypatch):
    original=Path.replace
    attempts=[]
    def locked_once(self,target):
        attempts.append(str(self))
        if len(attempts)==1: raise PermissionError('busy')
        return original(self,target)
    monkeypatch.setattr(Path,'replace',locked_once)
    monkeypatch.setattr(worker.w.time,'sleep',lambda seconds:None)
    worker.w.save(tmp_path/'state.json',{'ok':True})
    assert worker.w.read(tmp_path/'state.json',{})=={'ok':True}
    assert len(attempts)==2
    assert not list(tmp_path.glob('*.tmp'))


def test_manual_clip_waits_for_shared_six_hour_window(tmp_path,monkeypatch):
    monkeypatch.setattr(jobs,'ROOT',tmp_path)
    monkeypatch.setattr(worker.w,'ROOT',tmp_path)
    monkeypatch.setattr(worker.w,'WORK',tmp_path/'work/scheduler')
    worker.w.save(tmp_path/'scheduler.json',{'post_minutes':360,'early_publish_minutes':0})
    identity=jobs.enqueue({'mode':'auto','publish':True})[0]
    job=jobs.claim();job['result']={'file':'finished.mp4'}
    state={'uploads':[],'last_upload_at':worker.time.time()-3600}
    assert worker.defer_publication(job,state)
    assert jobs.get(identity)['status']=='queued_publish'
    state['last_upload_at']-=5*3600+1
    assert not worker.defer_publication(job,state)


def test_facebook_delivery_repair_does_not_wait_for_new_clip_window():
    job={'options':{'platforms':['facebook']},'result':{'publication':{'youtube_id':'already-published'}}}
    assert not worker.defer_publication(job,{'last_upload_at':worker.time.time()})
