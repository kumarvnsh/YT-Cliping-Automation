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
