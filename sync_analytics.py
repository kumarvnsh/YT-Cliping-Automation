"""Read-only, channel-verified Shotsnip analytics export for the local dashboard."""
import argparse
import msvcrt
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


def public_snapshot(config):
    import yt_dlp
    options = {'quiet': True, 'no_warnings': True, 'extract_flat': True,
               'playlistend': 50, 'socket_timeout': 15, 'retries': 1}
    with yt_dlp.YoutubeDL(options) as ydl:
        channel = ydl.extract_info(config['url'], download=False)
    if channel.get('channel_id') != config['channel_id']:
        raise ValueError('Public channel ID does not match Shotsnip')
    entries = [x for x in channel.get('entries', []) if x and x.get('id')]
    def details(entry):
        try:
            with yt_dlp.YoutubeDL({**options, 'extract_flat': False, 'skip_download': True,
                                   'ignore_no_formats_error': True}) as ydl:
                item = ydl.extract_info('https://www.youtube.com/watch?v=' + entry['id'], download=False)
            if item.get('channel_id') != config['channel_id']:
                raise ValueError('Video belongs to a different channel')
            return {'id': item['id'], 'title': item.get('title'), 'views': item.get('view_count'),
                    'likes': item.get('like_count'), 'comments': item.get('comment_count'),
                    'duration': item.get('duration'), 'published_at': datetime.fromtimestamp(item['timestamp'], timezone.utc).isoformat() if item.get('timestamp') else None,
                    'url': 'https://www.youtube.com/shorts/' + item['id']}
        except Exception as exc:
            return {'id': entry['id'], 'title': entry.get('title'), 'views': entry.get('view_count'),
                    'likes': None, 'comments': None, 'published_at': None,
                    'url': 'https://www.youtube.com/shorts/' + entry['id'],
                    'detail_status': 'unavailable', 'detail_error': type(exc).__name__}
    with ThreadPoolExecutor(max_workers=3) as pool:
        videos = list(pool.map(details, entries))
    return {'channel_id': config['channel_id'], 'name': config['name'],
            'subscribers': channel.get('channel_follower_count'), 'videos': videos,
            'source': 'public_youtube_pages', 'coverage': 'latest 50 listed uploads at most',
            'checked_at': datetime.now(timezone.utc).isoformat(),
            'owner_analytics': {'status': 'not_connected', 'reason': 'Additional analytics authorization is required.'}}


def owner_snapshot(config, snapshot):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    credentials = Credentials.from_authorized_user_file(str(ROOT / 'analytics_token.json'))
    yt = build('youtube', 'v3', credentials=credentials, cache_discovery=False)
    channel = yt.channels().list(part='id,snippet,statistics', mine=True).execute().get('items', [])
    if len(channel) != 1 or channel[0]['id'] != config['channel_id']:
        raise ValueError('Analytics authorization is for a different YouTube channel')
    stats = channel[0].get('statistics', {})
    snapshot['subscribers'] = int(stats['subscriberCount']) if 'subscriberCount' in stats else None
    ids = [v['id'] for v in snapshot['videos']]
    if ids:
        response = yt.videos().list(part='statistics,snippet', id=','.join(ids)).execute()
        by_id = {x['id']: x for x in response.get('items', [])}
        for video in snapshot['videos']:
            item = by_id.get(video['id'])
            if item and item['snippet']['channelId'] == config['channel_id']:
                video['published_at'] = item['snippet']['publishedAt']
                for name, field in [('views','viewCount'), ('likes','likeCount'), ('comments','commentCount')]:
                    video[name] = int(item['statistics'][field]) if field in item['statistics'] else None
    end = datetime.now(timezone.utc).date() - timedelta(days=2)
    start = end - timedelta(days=27)
    analytics = build('youtubeAnalytics', 'v2', credentials=credentials, cache_discovery=False)
    common = {'ids': 'channel==MINE', 'startDate': str(start), 'endDate': str(end),
              'metrics': 'views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,subscribersLost'}
    report = analytics.reports().query(**common, dimensions='video', maxResults=200, sort='-views').execute()
    headers = [x['name'] for x in report.get('columnHeaders', [])]
    metrics = [dict(zip(headers, row)) for row in report.get('rows', [])]
    daily = analytics.reports().query(**common, dimensions='day', sort='day').execute()
    daily_headers = [x['name'] for x in daily.get('columnHeaders', [])]
    snapshot['owner_analytics'] = {'status': 'connected', 'start': str(start), 'end': str(end),
        'videos': metrics, 'daily': [dict(zip(daily_headers, row)) for row in daily.get('rows', [])],
        'source': 'YouTube Analytics API', 'note': 'Reporting may lag; window excludes the latest two UTC days.'}
    snapshot['source'] = 'public_pages_and_verified_owner_apis'


def _sync():
    config = json.loads((ROOT / 'channel.json').read_text(encoding='utf-8'))
    snapshot = public_snapshot(config)
    if (ROOT / 'analytics_token.json').exists():
        try:
            owner_snapshot(config, snapshot)
        except Exception as exc:
            snapshot['owner_analytics'] = {'status': 'error', 'reason': str(exc)[:250]}
    save(ROOT / 'work' / 'dashboard' / 'analytics.json', snapshot)
    # Keep observation time so recent uploads can be compared at similar ages.
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    save(ROOT/'work/dashboard/analytics-history'/f'{stamp}.json',snapshot)
    print(f"Refreshed {config['name']}: {len(snapshot['videos'])} videos; owner analytics {snapshot['owner_analytics']['status']}")
    return snapshot


def sync():
    folder=ROOT/'work/dashboard'
    folder.mkdir(parents=True,exist_ok=True)
    with (folder/'analytics.lock').open('a+b') as lock:
        if lock.tell()==0:
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            print('Shotsnip analytics refresh already running')
            return None
        try:
            return _sync()
        finally:
            lock.seek(0); msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)


if __name__ == '__main__':
    sync()
