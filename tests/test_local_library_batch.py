from local_library_batch import resume_if_complete,BATCH
import scheduled_clips as w
import manual_jobs as jobs

def test_live_operations_resume_only_after_all_platforms_complete(tmp_path,monkeypatch):
    monkeypatch.setattr(w,'ROOT',tmp_path)
    ids=[str(i) for i in range(15)]
    config={'enabled':False,'scan_minutes':180,'post_minutes':180,
            'live_operations_batch_hold':{'batch':BATCH,'job_ids':ids}}
    w.save(tmp_path/'scheduler.json',config)
    complete={'status':'published','result':{'publication':{'youtube_id':'yt','facebook':{'status':'published'}}}}
    monkeypatch.setattr(jobs,'get',lambda identity: complete if identity!='14' else {'status':'partial'})
    assert not resume_if_complete()
    assert w.read(tmp_path/'scheduler.json',{})['enabled'] is False
    monkeypatch.setattr(jobs,'get',lambda identity:complete)
    assert resume_if_complete()
    result=w.read(tmp_path/'scheduler.json',{})
    assert result['enabled'] is True and result['scan_minutes']==180
    assert 'live_operations_batch_hold' not in result


def test_hold_is_reapplied_until_the_owner_ends_it(tmp_path,monkeypatch):
    from local_library_batch import enforce_hold
    monkeypatch.setattr(w,'ROOT',tmp_path)
    (tmp_path/'work/dashboard').mkdir(parents=True)
    hold={'batch':BATCH,'job_ids':[str(i) for i in range(15)]}
    w.save(tmp_path/'scheduler.json',{'enabled':True,'live_operations_batch_hold':hold})
    enforce_hold()
    assert w.read(tmp_path/'scheduler.json',{})['enabled'] is False
    flag=tmp_path/'work/dashboard/live-operations-batch-hold.json'
    assert flag.exists()
    # The owner resumes and ends the hold; the leftover flag must not pause live operations again.
    w.save(tmp_path/'scheduler.json',{'enabled':True,'ended_batch_hold':{'batch':BATCH,'by':'owner'}})
    enforce_hold()
    result=w.read(tmp_path/'scheduler.json',{})
    assert result['enabled'] is True and 'live_operations_batch_hold' not in result
    assert not flag.exists()
