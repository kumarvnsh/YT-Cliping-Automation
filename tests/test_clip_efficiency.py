from clip_efficiency import slice_transcript,cleanup,checkpoints
import os,time


def test_cached_words_shift_clip_and_keep_partial_edges():
    t={'language':'en','segments':[{'words':[{'word':'a','start':9,'end':11},{'word':'b','start':12,'end':13},{'word':'outside','start':20,'end':21}]}]}
    r=slice_transcript(t,10,15)
    assert r['duration']==5
    assert r['segments'][0]['words']==[{'word':'a','start':0,'end':1},{'word':'b','start':2,'end':3}]


def test_cleanup_retains_finished_and_recent_raw(tmp_path):
    folder=tmp_path/'work/scheduler/run'; folder.mkdir(parents=True)
    old=folder/'capture.mp4'; old.write_bytes(b'raw'); os.utime(old,(0,0))
    recent=folder/'highlight.mp4'; recent.write_bytes(b'new')
    final=folder/'finished-short.mp4'; final.write_bytes(b'final'); os.utime(final,(0,0))
    assert cleanup(tmp_path)['files_removed']==1
    assert recent.exists() and final.exists() and not old.exists()


def test_checkpoints_do_not_invent_missing_ages():
    rows=[{'checked_at':'2026-11-17T01:05:00+00:00','videos':[{'id':'a','title':'A','views':10,'published_at':'2026-11-17T00:00:00Z'}]},
          {'checked_at':'2026-11-17T03:00:00+00:00','videos':[{'id':'a','views':999,'published_at':'2026-11-17T00:00:00Z'}]}]
    p=checkpoints(rows)[0]['checkpoints']
    assert p['1']['views']==10 and '6' not in p and '24' not in p


def test_one_hour_checkpoint_survives_late_half_hour_refresh():
    # Published 08:49; refreshes landed at 09:30 and 10:05 (both outside +/-15 min).
    rows=[{'checked_at':'2026-11-18T09:30:00+00:00','videos':[{'id':'v','title':'V','views':5,'published_at':'2026-11-18T08:49:00Z'}]},
          {'checked_at':'2026-11-18T10:05:00+00:00','videos':[{'id':'v','views':40,'published_at':'2026-11-18T08:49:00Z'}]}]
    p=checkpoints(rows)[0]['checkpoints']
    assert p['1']['views']==40 and p['1']['observed_at']=='2026-11-18T10:05:00+00:00'


def test_checkpoint_reuses_publish_time_when_detail_lookup_failed():
    rows=[{'checked_at':'2026-11-18T09:50:00+00:00','videos':[{'id':'v','title':'V','views':30,'published_at':None,'detail_status':'unavailable'}]},
          {'checked_at':'2026-11-18T13:42:00+00:00','videos':[{'id':'v','views':300,'published_at':'2026-11-18T08:49:00Z'}]}]
    p=checkpoints(rows)[0]['checkpoints']
    assert p['1']['views']==30 and '6' not in p


def test_finished_manual_jobs_release_their_kitty_downloads(tmp_path):
    import json, os, time
    import manual_jobs
    from clip_efficiency import cleanup
    kitty = tmp_path/'kitty'; downloads = kitty/'data/downloads'; downloads.mkdir(parents=True)
    old = time.time() - 5*86400
    with manual_jobs.connection(tmp_path) as db:
        for job, status in (('donejob', 'published'), ('waitjob', 'queued_publish')):
            db.execute('INSERT INTO jobs (id,status) VALUES (?,?)', (job, status))
            folder = tmp_path/'work/manual'/job; folder.mkdir(parents=True)
            record = folder/'kitty-job.json'; record.write_text(json.dumps({'video_id': f'local_{job[:4]}aa'}))
            media = downloads/f'local_{job[:4]}aa.mp4'; media.write_bytes(b'v')
            for p in (record, media):
                os.utime(p, (old, old))
    cleanup(tmp_path, 3, kitty)
    assert not (downloads/'local_doneaa.mp4').exists()   # published: download freed
    assert (downloads/'local_waitaa.mp4').exists()        # still waiting to publish: kept


def test_never_posted_clips_go_but_posted_queued_and_fresh_ones_stay(tmp_path):
    import json, os, time
    from clip_efficiency import stale_runs
    sched = tmp_path/'work/scheduler'
    old = time.time() - 8*3600
    for name in ('dead', 'posted', 'queued', 'fresh', 'kept'):
        run = sched/name; (run/'highlight-finished').mkdir(parents=True)
        f = run/'highlight-finished'/'finished-short.mp4'; f.write_bytes(b'v')
        if name != 'fresh':
            os.utime(f, (old, old))
    (sched/'kept'/'.studio-hq-keep').write_text('')
    (sched/'state.json').write_text(json.dumps({'uploads': [{'file': str(sched/'posted'/'highlight-finished'/'finished-short.mp4')}],
                                                'ready_clips': [{'file': str(sched/'queued'/'highlight-finished'/'finished-short.mp4')}]}))
    assert [r.name for r in stale_runs(tmp_path, 0.25)] == ['dead']
