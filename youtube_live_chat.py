"""Read the public YouTube live chat web endpoint without account cookies.

This unofficial adapter can break when the website changes. It raises on missing
chat configuration instead of treating a broken parser as an empty chat.
"""
import json
import re
import requests

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36')


def embedded_json(html, pattern):
    match = re.search(pattern, html)
    if not match:
        raise ValueError('YouTube live chat configuration is missing')
    return json.JSONDecoder().raw_decode(html[match.end():].lstrip())[0]


def continuation(renderer):
    for item in renderer.get('continuations', []):
        for key in ('invalidationContinuationData', 'timedContinuationData', 'reloadContinuationData'):
            value = item.get(key, {})
            if value.get('continuation'):
                return value['continuation'], min(10, max(1, value.get('timeoutMs', 5000) / 1000))
    raise ValueError('Live chat continuation missing; chat may have ended or be disabled')


def transient(exc):
    """True for brief network or server hiccups worth retrying (timeouts, 429, 5xx)."""
    if isinstance(exc, requests.HTTPError):
        status = getattr(exc.response, 'status_code', None)
        return status == 429 or (status is not None and status >= 500)
    return isinstance(exc, (requests.Timeout, requests.ConnectionError))


def messages(renderer):
    """Yield only ordinary text messages; no viewer identities are logged."""
    for action in renderer.get('actions', []):
        item = action.get('addChatItemAction', {}).get('item', {})
        msg = item.get('liveChatTextMessageRenderer')
        if not msg or not msg.get('authorExternalChannelId') or not msg.get('timestampUsec'):
            continue
        yield {'id': msg['id'], 'author': msg['authorExternalChannelId'],
               'timestamp': int(msg['timestampUsec']) / 1000000}


class YouTubeChat:
    def __init__(self, video_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise ValueError('Invalid YouTube video ID')
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT, 'Origin': 'https://www.youtube.com'})
        try:
            response = self.session.get('https://www.youtube.com/live_chat',
                params={'v': video_id, 'is_popout': '1'}, timeout=(10, 20))
            response.raise_for_status()
            config = embedded_json(response.text, r'ytcfg.set\(\s*(?=\{)')
            initial = embedded_json(response.text, r'window\["ytInitialData"\]\s*=\s*')
            self.context = config['INNERTUBE_CONTEXT']
            self.token, self.interval = continuation(initial['contents']['liveChatRenderer'])
            self.session.headers.update({'X-Youtube-Client-Name': '1',
                'X-Youtube-Client-Version': self.context['client']['clientVersion']})
        except BaseException:
            self.session.close()
            raise

    def get(self):
        response = self.session.post('https://www.youtube.com/youtubei/v1/live_chat/get_live_chat',
            params={'prettyPrint': 'false'},
            json={'context': self.context, 'continuation': self.token}, timeout=(10, 20))
        response.raise_for_status()
        renderer = response.json()['continuationContents']['liveChatContinuation']
        self.token, self.interval = continuation(renderer)
        return list(messages(renderer))

    def close(self):
        self.session.close()
