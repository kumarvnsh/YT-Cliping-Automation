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


def test_reel_views_fall_back_to_reels_play_count(tmp_path,monkeypatch):
    monkeypatch.setattr(collector,'ROOT',tmp_path)
    monkeypatch.setattr(fb,'_cfg',lambda:{'page_id':'123','api_version':'v21.0'})
    monkeypatch.setattr(fb,'_token',lambda:'private-token')
    monkeypatch.setattr(fb,'_page_token',lambda *a:'private-page-token')
    class Response:
        ok=True
        def __init__(self,data): self.data=data
        def json(self): return self.data
    def get(url,params=None,**kw):
        if url.endswith('/123'): return Response({'id':'123','name':'Shotsnip','followers_count':5,'fan_count':1})
        if url.endswith('/123/videos'): return Response({'data':[{'id':'r1','title':'Reel'},{'id':'v2','title':'Video'}]})
        if url.endswith('/r1/video_insights'):
            if params['metric']=='total_video_views': return Response({'data':[]})
            return Response({'data':[{'name':'blue_reels_play_count','values':[{'value':306}]}]})
        if url.endswith('/v2/video_insights'):
            if params['metric']=='total_video_views': return Response({'data':[{'values':[{'value':153}]}]})
            raise AssertionError('classic video must not need the Reels metric')
        return Response({'data':[{'name':'page_video_views','values':[{'value':306},{'value':153}]}]})
    monkeypatch.setattr(collector.requests,'get',get)
    result=collector.sync()
    assert [v['views'] for v in result['videos']]==[306,153]
    assert result['video_views']==459 and result['window_views']==459
    assert all('error' not in v for v in result['videos'])
