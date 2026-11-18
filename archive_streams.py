"""Yesterday's public YouTube stream replays, ranked by observed video views."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from collections import defaultdict
import yt_dlp
from game_schedule import IST,active_game,title_matches
from stream_sources import ROOT,any_gta,twitch_query


def replay_candidate(info,targeted=False,today=None):
    today=today or datetime.now(IST).date()
    if info.get('live_status') not in ('was_live','post_live') or (info.get('duration') or 0)<300:
        return None
    stamp=info.get('release_timestamp') or info.get('timestamp')
    date=datetime.fromtimestamp(stamp,IST).date() if stamp else datetime.strptime(info.get('upload_date','19700101'),'%Y%m%d').date()
    if date!=today-timedelta(days=1): return None
    if not (any_gta(info.get('title')) if targeted else title_matches(info.get('title'),active_game())): return None
    views=info.get('view_count')
    if views is None: return None
    return dict(platform='youtube',id=info['id'],title=info['title'],channel=info.get('channel'),
        url=info.get('webpage_url') or 'https://www.youtube.com/watch?v='+info['id'],
        viewers=views,targeted=targeted,game='gta' if targeted else active_game(),
        game_evidence='archived_stream_title',archive=True,metric='replay_views')


def discover_yesterday():
    watch=json.loads((ROOT/'watchlist.json').read_text(encoding='utf-8'))
    urls={}
    def listing(url,targeted):
        try:
            with yt_dlp.YoutubeDL({'quiet':True,'no_warnings':True,'extract_flat':True,'playlistend':8,'socket_timeout':12,'retries':0}) as y:
                data=y.extract_info(url,download=False)
            return [(row.get('url') or 'https://www.youtube.com/watch?v='+row['id'],targeted)
                for row in data.get('entries',[]) if row and row.get('id')]
        except Exception: return []
    queries=[(s['url'].rstrip('/')+'/streams',True) for c in watch['creators'] if c.get('enabled',True)
        for s in c['sources'] if s['platform']=='youtube']
    queries.append(('ytsearch15:'+('GTA 6' if active_game()=='gta6' else 'GTA 5')+' live stream',False))
    with ThreadPoolExecutor(max_workers=3) as pool:
        for batch in pool.map(lambda pair:listing(*pair),queries):
            for url,targeted in batch: urls[url]=urls.get(url,False) or targeted
    def probe(pair):
        try:
            with yt_dlp.YoutubeDL({'quiet':True,'no_warnings':True,'socket_timeout':12,'retries':0}) as y:
                return replay_candidate(y.extract_info(pair[0],download=False),pair[1])
        except Exception: return None
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows=[row for row in pool.map(probe,urls.items()) if row]
    logins={s['url'].rstrip('/').split('/')[-1].lower():True for c in watch['creators'] if c.get('enabled',True)
        for s in c['sources'] if s['platform']=='twitch'}
    state_path=ROOT/'work/scheduler/state.json'
    state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    for item in state.get('capture_history',[])+state.get('uploads',[]):
        source=item.get('source',{})
        if source.get('platform')=='twitch': logins.setdefault(source['url'].rstrip('/').split('/')[-1].lower(),False)
    for login,targeted in logins.items():
        try:
            data=twitch_query('query { user(login:'+json.dumps(login)+') { displayName videos(first:5,type:ARCHIVE,sort:TIME) { edges { node { id title viewCount publishedAt lengthSeconds game { name } } } } } }').get('user')
            for edge in ((data or {}).get('videos') or {}).get('edges',[]):
                node=edge['node']; game=(node.get('game') or {}).get('name','')
                date=datetime.fromisoformat(node['publishedAt'].replace('Z','+00:00')).astimezone(IST).date()
                if date!=datetime.now(IST).date()-timedelta(days=1) or node['lengthSeconds']<300: continue
                if not (any_gta(game+' '+node['title']) if targeted else title_matches(game,active_game())): continue
                rows.append(dict(platform='twitch',id=node['id'],title=node['title'],channel=data['displayName'],
                    url='https://www.twitch.tv/videos/'+node['id'],viewers=node['viewCount'],targeted=targeted,
                    game='gta' if targeted else active_game(),archive=True,metric='replay_views',game_evidence='archive_category'))
        except Exception: continue
    unique={(row['platform'],row['id']):row for row in rows}
    return sorted(unique.values(),key=lambda row:-row['viewers'])


def chat_peak(lines,duration):
    bins=defaultdict(set)
    for line in lines:
        try: data=json.loads(line)
        except ValueError: continue
        replays=[data['replayChatItemAction']] if 'replayChatItemAction' in data else [a.get('replayChatItemAction',{}) for a in data.get('actions',[])]
        for replay in replays:
            offset=replay.get('videoOffsetTimeMsec')
            if offset is None: continue
            at=int(offset)/1000
            if not 0<=at<=duration: continue
            children=replay.get('actions',[])
            for child in children:
                msg=child.get('addChatItemAction',{}).get('item',{}).get('liveChatTextMessageRenderer',{})
                author=msg.get('authorExternalChannelId')
                if author: bins[int(at//30)].add(author)
    if not bins: return None
    bucket=max(bins,key=lambda key:len(bins[key]))
    return {'end':min(duration,(bucket+1)*30),'unique_chatters':len(bins[bucket])}


def replay_window(info,folder,seconds):
    duration=float(info['duration']); peak=None
    with tempfile.TemporaryDirectory(prefix='replay-chat-',dir=folder) as temp:
        assert Path(temp).resolve().is_relative_to(Path(folder).resolve())
        try:
            if 'youtube.com' not in info['webpage_url']: raise ValueError('Replay chat not provided by this adapter')
            subprocess.run([sys.executable,'-m','yt_dlp','--quiet','--no-warnings','--skip-download',
                '--write-subs','--sub-langs','live_chat','-o',str(Path(temp)/'chat.%(ext)s'),
                info['webpage_url']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90)
        except (subprocess.TimeoutExpired,subprocess.CalledProcessError,ValueError): pass
        for path in Path(temp).glob('*.json*'):
            if path.stat().st_size<50*1024**2:
                with path.open(encoding='utf-8') as f: peak=chat_peak(f,duration) or peak
    if peak:
        offset=max(0,min(duration-seconds,peak['end']-seconds*.7))
        return offset,{'method':'replay_chat_peak',**peak}
    heat=info.get('heatmap') or []
    if heat:
        point=max(heat,key=lambda row:row.get('value',0))
        offset=max(0,min(duration-seconds,point['start_time']-seconds/2))
        return offset,{'method':'most_replayed','source_time':point['start_time']}
    return max(0,duration-seconds),{'method':'tail_window_no_engagement_data'}
