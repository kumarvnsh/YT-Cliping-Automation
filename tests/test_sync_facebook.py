import sync_facebook as collector
import facebook_shotsnip as fb


def test_empty_page_is_zero_and_has_no_tokens(tmp_path,monkeypatch):
    monkeypatch.setattr(collector,'ROOT',tmp_path)
    monkeypatch.setattr(fb,'_cfg',lambda:{'page_id':'123','api_version':'v21.0'})
    monkeypatch.setattr(fb,'_token',lambda:'private-token')
    monkeypatch.setattr(fb,'_page_token',lambda *a:'private-page-token')
    class Response:
        ok=True
        def json(self): return {'id':'123','name':'Shotsnip','followers_count':0,'fan_count':0}
    class Empty(Response):
        def json(self): return {'data':[]}
    monkeypatch.setattr(collector.requests,'get',lambda url,**kw:Response() if url.endswith('/123') else Empty())
    result=collector.sync()
    assert result['followers']==0 and result['video_views']==0
    assert result['window_views'] is None
    assert 'private-token' not in str(result)


def test_reel_specific_metrics_include_explicit_zero():
    def get(node,**params):
        assert params['period']=='lifetime'
        return {'data':[
            {'name':'fb_reels_total_plays','values':[{'value':0}]},
            {'name':'blue_reels_play_count','values':[{'value':0}]},
            {'name':'fb_reels_replay_count','values':[{'value':0}]}]}
    row=collector.video_metrics(get,{'id':'reel'})
    assert row['views']==0 and row['plays']==0 and row['replays']==0
    assert row['views_metric']=='fb_reels_total_plays'
    assert row['insight_status']=='available'


def test_empty_and_error_metrics_stay_unavailable():
    def empty(*a,**kw):return {'data':[]}
    def failed(*a,**kw):raise RuntimeError('Facebook metrics unavailable (code 200)')
    for get in (empty,failed):
        row=collector.video_metrics(get,{'id':'reel'})
        assert row['views'] is None and row['plays'] is None
        assert row['insight_status']=='unavailable' and row['error']


def test_legacy_video_fallback_is_not_mislabeled_as_reel_plays():
    def get(node,**params):
        if params['metric']=='total_video_views':
            return {'data':[{'name':'total_video_views','values':[{'value':42}]}]}
        return {'data':[]}
    row=collector.video_metrics(get,{'id':'video'})
    assert row['views']==42 and row['three_second_views']==42
    assert row['plays'] is None and row['views_metric']=='total_video_views'


def test_reel_fallback_survives_an_unsupported_combined_metric_request():
    def get(node,**params):
        if ',' in params['metric']:raise RuntimeError('Unsupported metric list')
        assert params['metric']=='blue_reels_play_count'
        return {'data':[{'name':'blue_reels_play_count','values':[{'value':306}]}]}
    row=collector.video_metrics(get,{'id':'reel'})
    assert row['views']==306 and row['initial_plays']==306
    assert row['plays'] is None and 'error' not in row


def test_tracked_reels_are_deduplicated_and_linked_to_youtube(tmp_path,monkeypatch):
    import json
    monkeypatch.setattr(collector,'ROOT',tmp_path)
    monkeypatch.setattr(fb,'_cfg',lambda:{'page_id':'123','api_version':'v21.0'})
    monkeypatch.setattr(fb,'_token',lambda:'private-token')
    monkeypatch.setattr(fb,'_page_token',lambda *a:'private-page-token')
    folder=tmp_path/'work/scheduler';folder.mkdir(parents=True)
    (folder/'state.json').write_text(json.dumps({'uploads':[{'video_id':'yt1','title':'Creator | GTA 6 | Reaction',
        'source':{'channel':'Creator'},'facebook':{'reel_id':'r1'}}]}))
    class Response:
        ok=True
        def __init__(self,data):self.data=data
        def json(self):return self.data
    def get(url,**kw):
        if url.endswith('/123'):return Response({'id':'123','name':'Shotsnip'})
        if url.endswith('/videos'):return Response({'data':[{'id':'r1','permalink_url':'/reel/r1/'}]})
        if url.endswith('/insights'):return Response({'data':[]})
        return Response({'data':[{'name':'fb_reels_total_plays','values':[{'value':123}]},
            {'name':'blue_reels_play_count','values':[{'value':100}]},
            {'name':'fb_reels_replay_count','values':[{'value':23}]}]})
    monkeypatch.setattr(collector.requests,'get',get)
    result=collector.sync()
    assert len(result['videos'])==1 and result['reel_plays_total']==123
    assert result['measured_videos']==1 and result['missing_videos']==0
    assert result['videos'][0]['youtube_id']=='yt1'
    assert result['videos'][0]['title']=='Creator | GTA 6 | Reaction'
    assert 'private-page-token' not in json.dumps(result)
