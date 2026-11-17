"""Public live discovery. Viewer counts are snapshots, never subscriber counts."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import requests
import re
from game_schedule import active_game, title_matches

ROOT = Path(__file__).resolve().parent


def twitch_query(query):
    import yt_dlp
    from yt_dlp.extractor.twitch import TwitchBaseIE
    with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
        client_id = TwitchBaseIE(ydl)._CLIENT_ID
    response = requests.post('https://gql.twitch.tv/gql',
        headers={'Client-ID': client_id}, json={'query': query}, timeout=25)
    response.raise_for_status()
    result = response.json()
    if result.get('errors'):
        raise RuntimeError('Public Twitch discovery query failed')
    return result['data']


def twitch_directory(game, targets):
    slug = 'grand-theft-auto-v' if game == 'gta5' else 'grand-theft-auto-vi'
    data = twitch_query('query { game(slug:' + json.dumps(slug) + ') { streams(first:100) '
        '{ edges { node { id viewersCount title broadcaster { login displayName } } } } } }')
    rows = []
    for edge in ((data.get('game') or {}).get('streams') or {}).get('edges', []):
        node = edge['node']; broadcaster = node['broadcaster']
        login = broadcaster['login'].lower()
        # The actual directory category is evidence; conflicting game titles are refused.
        opposite = 'gta6' if game == 'gta5' else 'gta5'
        conflict = r'\b(?:gta|grand\s+theft\s+auto)\s*[-:]?\s*' + ('(?:6|vi)\b' if game == 'gta5' else '(?:5|v)\b')
        if re.search(conflict, node['title'], re.I):
            continue
        rows.append(dict(platform='twitch', id=node['id'], title=node['title'],
            channel=broadcaster['displayName'], url='https://www.twitch.tv/' + login,
            viewers=node['viewersCount'], targeted=login in targets, game=game,
            game_evidence='live_category'))
    return rows


def youtube_probe(url, targeted=False):
    import yt_dlp
    try:
        with yt_dlp.YoutubeDL({'quiet':True, 'no_warnings':True, 'skip_download':True,
            'socket_timeout':12, 'retries':0, 'extractor_retries':0}) as ydl:
            info = ydl.extract_info(url, download=False)
        game = active_game()
        if info.get('live_status') != 'is_live' or not title_matches(info.get('title'), game):
            return None
        viewers = info.get('concurrent_view_count')
        if viewers is None:
            return None
        return dict(platform='youtube', id=info['id'], title=info['title'],
            channel=info.get('channel'), url=info['webpage_url'], viewers=int(viewers),
            targeted=targeted, game=game, game_evidence='unambiguous_live_title')
    except Exception:
        return None


def choose(rows):
    """Highest measured live viewers; a target wins an equal-count tie."""
    return max(rows, key=lambda x: (x['viewers'], x['targeted']), default=None)


def scan():
    from live_clipper import discover
    watch = json.loads((ROOT / 'watchlist.json').read_text(encoding='utf-8'))
    entries = watch if isinstance(watch, list) else watch.get('creators', watch.get('targets', []))
    urls = {}; twitch_targets = set(); errors = []
    for entry in entries:
        if not entry.get('enabled', True):
            continue
        # Support the dashboard's source list and original platform objects.
        for source in entry.get('sources', []):
            url = source.get('url', '')
            if 'twitch.tv/' in url:
                twitch_targets.add(url.rstrip('/').split('/')[-1].lower())
            elif 'youtube.com/' in url:
                urls[url.rstrip('/') + '/live'] = True
    try:
        for item in discover(limit=50):
            urls.setdefault(item['url'], False)
    except Exception as exc:
        errors.append('YouTube search: ' + type(exc).__name__)
    rows = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for item in pool.map(lambda pair: youtube_probe(*pair), urls.items()):
            if item:
                rows.append(item)
    try:
        rows += twitch_directory(active_game(), twitch_targets)
    except Exception as exc:
        errors.append('Twitch directory: ' + type(exc).__name__)
    unique = {(row['platform'], row['id']): row for row in rows}
    return sorted(unique.values(), key=lambda row: (-row['viewers'], not row['targeted'])), errors
