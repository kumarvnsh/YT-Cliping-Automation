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
