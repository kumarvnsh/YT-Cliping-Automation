"""Ephemeral public chat counts; no stored chatter names or message text."""
import random
import threading
import time
from live_clipper import SpikeDetector


class LiveSignals:
    def __init__(self, source):
        self.source=source; self.stop=threading.Event(); self.spikes=[]; self.messages=0
        self.error=None; self.started=time.monotonic(); self.thread=None
        self.connected=False; self.mode='public'

    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True)
        self.thread.start()
        return self

    def run(self):
        detector=SpikeDetector(); chat=None
        def add(author):
            self.messages+=1
            for event in detector.advance(time.time(),author):
                if event['spike']:
                    self.spikes.append({**event,'capture_offset':time.monotonic()-self.started})
        try:
            if self.source['platform']=='twitch':
                import websocket
                from twitch_auth import credentials
                token=credentials()
                chat=websocket.create_connection('wss://irc-ws.chat.twitch.tv:443',timeout=5)
                nick=token['login'] if token else 'justinfan'+str(random.randint(10000,999999))
                password='oauth:'+token['access_token'] if token else 'SCHMOOPIIE'
                self.mode='authenticated_read_only' if token else 'anonymous'
                login=self.source['url'].rstrip('/').split('/')[-1]
                # Twitch's WebSocket transport expects one command per frame.
                chat.send('PASS '+password)
                chat.send('NICK '+nick)
                chat.send('JOIN #'+login)
                while not self.stop.is_set():
                    try:
                        lines=chat.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not lines:
                        break
                    for line in lines.splitlines():
                        if ' 001 ' in line:
                            self.connected=True
                        if line.startswith('PING'):
                            chat.send('PONG :tmi.twitch.tv\r\n')
                        elif ' PRIVMSG ' in line:
                            add(line.split('!',1)[0])
            else:
                from youtube_live_chat import YouTubeChat
                chat=YouTubeChat(self.source['id']); seen=set()
                while not self.stop.is_set():
                    for message in chat.get():
                        if message['id'] not in seen and abs(time.time()-message['timestamp'])<20:
                            seen.add(message['id']); add(message['author'])
                    if len(seen)>20000:
                        seen.clear()
                    self.stop.wait(chat.interval)
        except Exception as exc:
            self.error=type(exc).__name__
        finally:
            if chat:
                chat.close()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=35)
        return {'messages':self.messages,'spikes':list(self.spikes),'error':self.error,
                'connected':self.connected,'mode':self.mode}
