import sys
from types import SimpleNamespace
from live_signals import LiveSignals


def test_chat_uses_separate_frames_and_does_not_export_identity(monkeypatch):
    signal=LiveSignals({'platform':'twitch','url':'https://www.twitch.tv/creator'})
    class Socket:
        def send(self,text): frames.append(text)
        def recv(self):
            signal.stop.set()
            return ':tmi.twitch.tv 001 viewer :Welcome\r\n:name!name@host PRIVMSG #creator :hello'
        def close(self): pass
    frames=[]
    monkeypatch.setitem(sys.modules,'twitch_auth',SimpleNamespace(credentials=lambda:{
        'login':'viewer','access_token':'private-fixture'}))
    monkeypatch.setattr('websocket.create_connection',lambda *a,**k:Socket())
    signal.run()
    assert frames==['PASS oauth:private-fixture','NICK viewer','JOIN #creator']
    result=signal.close()
    assert result['connected'] and result['messages']==1
    assert 'private-fixture' not in str(result)
    assert 'name' not in str(result)


def test_youtube_chat_survives_read_timeout_and_503(monkeypatch):
    import requests
    import time
    import youtube_live_chat
    signal=LiveSignals({'platform':'youtube','id':'abcdefghijk'})
    bad=requests.Response(); bad.status_code=503
    errors=[requests.ReadTimeout('read timed out'),requests.HTTPError('503',response=bad)]
    class Chat:
        interval=0
        def __init__(self,video_id): pass
        def get(self):
            if errors: raise errors.pop(0)
            signal.stop.set()
            return [{'id':'m1','author':'a','timestamp':time.time()}]
        def close(self): pass
    monkeypatch.setattr(youtube_live_chat,'YouTubeChat',Chat)
    monkeypatch.setattr(signal.stop,'wait',lambda seconds=None:signal.stop.is_set())
    signal.run()
    result=signal.close()
    assert result['error'] is None and result['messages']==1


def test_youtube_chat_gives_up_after_repeated_503(monkeypatch):
    import requests
    import youtube_live_chat
    signal=LiveSignals({'platform':'youtube','id':'abcdefghijk'})
    bad=requests.Response(); bad.status_code=503
    calls=[]
    class Chat:
        interval=0
        def __init__(self,video_id): pass
        def get(self):
            calls.append(1); raise requests.HTTPError('503',response=bad)
        def close(self): pass
    monkeypatch.setattr(youtube_live_chat,'YouTubeChat',Chat)
    monkeypatch.setattr(signal.stop,'wait',lambda seconds=None:False)
    signal.run()
    assert signal.error=='HTTPError' and len(calls)==5
