import json
from growth_experiment import assignment,TRIAL

def test_disabled_retains_thirty_seconds():
    assert assignment({},False)['source_seconds']==30
    assert assignment({},False)['id'] is None

def test_alternates_successful_uploads_only_and_stops(tmp_path):
    metadata=tmp_path/'metadata.json'
    metadata.write_text(json.dumps({'growth_experiment':{'id':TRIAL}}))
    upload={'file':str(tmp_path/'finished.mp4'),'status':'uploaded'}
    assert assignment({})['source_seconds']==20
    assert assignment({'uploads':[upload]})['source_seconds']==30
    assert assignment({'uploads':[{**upload,'status':'uncertain'}]})['source_seconds']==20
    assert assignment({'uploads':[upload]*24})['id'] is None
from growth_experiment import platform_checkpoints,build_report,TRIAL

def test_nearest_window_preserves_zero_and_rejects_wrong_metric():
    snaps=[{'checked_at':'2026-11-21T00:00:00Z','videos':[{'id':'r','created_time':'2026-11-20T00:00:00Z','created_time_source':'meta','plays':0,'initial_plays':500}]}]
    result=platform_checkpoints(snaps,{'r'},'facebook','2026-11-21T00:00:00Z')
    assert result['r']['checkpoints']['24']['value']==0
    snaps[0]['videos'][0].pop('plays')
    assert platform_checkpoints(snaps,{'r'},'facebook','2026-11-21T00:00:00Z')['r']['checkpoints']=={}

def test_unverified_facebook_time_never_used():
    snaps=[{'checked_at':'2026-11-21T00:00:00Z','videos':[{'id':'r','created_time':'2026-11-20T00:00:00Z','plays':200}]}]
    result=platform_checkpoints(snaps,{'r'},'facebook','2026-11-21T00:00:00Z')
    assert result['r']['published_at'] is None and result['r']['checkpoints']=={}

def test_report_uses_platform_clocks_and_ledger_tags():
    state={'uploads':[{'status':'uploaded','video_id':'y','growth_experiment':{'id':TRIAL,'variant':'tight_20s'},'facebook':{'status':'published','reel_id':'r'}}]}
    yt=[{'checked_at':'2026-11-21T00:00:00Z','videos':[{'id':'y','published_at':'2026-11-20T00:00:00Z','views':1000}]}]
    fb=[{'checked_at':'2026-11-21T00:00:00Z','videos':[{'id':'r','created_time':'2026-11-20T04:00:00Z','created_time_source':'meta','plays':2000}]}]
    result=build_report(state,yt,fb,dict(youtube='2026-11-21T00:00:00Z',facebook='2026-11-21T00:00:00Z'))
    assert result['checkpoints']['youtube']['tight_20s']['24']['median']==1000
    assert result['checkpoints']['facebook']['tight_20s']['24']['eligible']==0
    assert result['checkpoints']['facebook']['tight_20s']['24']['median'] is None
