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
