"""Public replay discovery and whole-recording engagement-based window selection."""
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


def recent_end(start,duration,now,hours,explicit_end=None):
    if not start and not explicit_end:return None
    end=float(explicit_end or float(start)+float(duration or 0))
    age=now.timestamp()-end
    if age < -300 or age > hours*3600:return None
    return min(end,now.timestamp())


def replay_candidate(info,targeted=False,today=None,lookback_hours=None,now=None):
    now=now or datetime.now(IST)
    today=today or datetime.now(IST).date()
    if info.get('live_status') not in ('was_live','post_live') or (info.get('duration') or 0)<300:
        return None
    stamp=info.get('release_timestamp') or info.get('timestamp')
    ended=None
    if lookback_hours is not None:
        # release_timestamp is the broadcast's start on YouTube. If only a
        # publication timestamp is known, use it conservatively without adding duration.
        start=info.get('release_timestamp')
        explicit=info.get('live_end_timestamp') or info.get('end_timestamp')
        ended=recent_end(start,info.get('duration'),now,lookback_hours,
                         explicit or (info.get('timestamp') if not start else None))
        if ended is None:return None
    else:
        date=datetime.fromtimestamp(stamp,IST).date() if stamp else datetime.strptime(info.get('upload_date') or '19700101','%Y%m%d').date()
        if date!=today-timedelta(days=1):return None
    if not (any_gta(info.get('title')) if targeted else title_matches(info.get('title'),active_game())): return None
    views=info.get('view_count')
    if views is None: return None
    row=dict(platform='youtube',id=info['id'],title=info['title'],channel=info.get('channel'),
        url=info.get('webpage_url') or 'https://www.youtube.com/watch?v='+info['id'],
        viewers=views,targeted=targeted,game='gta' if targeted else active_game(),
        game_evidence='archived_stream_title',archive=True,metric='replay_views')
    if ended is not None:
        row.update(recent_replay=True,ended_at=ended,archive_age_hours=round((now.timestamp()-ended)/3600,2))
    return row


def discover_yesterday(lookback_hours=None,targets_only=False):
    watch=json.loads((ROOT/'watchlist.json').read_text(encoding='utf-8'))
    urls={}
    def listing(url,targeted):
        try:
            with yt_dlp.YoutubeDL({'quiet':True,'no_warnings':True,'extract_flat':True,'playlistend':3 if lookback_hours else 8,'socket_timeout':12,'retries':0}) as y:
                data=y.extract_info(url,download=False)
            return [(row.get('url') or 'https://www.youtube.com/watch?v='+row['id'],targeted)
                for row in data.get('entries',[]) if row and row.get('id')]
        except Exception: return []
    queries=[(s['url'].rstrip('/')+'/streams',True) for c in watch['creators'] if c.get('enabled',True)
        for s in c['sources'] if s['platform']=='youtube']
    if not targets_only:
        queries.append(('ytsearch15:'+('GTA 6' if active_game()=='gta6' else 'GTA 5')+' live stream',False))
    with ThreadPoolExecutor(max_workers=3) as pool:
        for batch in pool.map(lambda pair:listing(*pair),queries):
            for url,targeted in batch: urls[url]=urls.get(url,False) or targeted
    def probe(pair):
        try:
            with yt_dlp.YoutubeDL({'quiet':True,'no_warnings':True,'socket_timeout':12,'retries':0}) as y:
                return replay_candidate(y.extract_info(pair[0],download=False),pair[1],lookback_hours=lookback_hours)
        except Exception: return None
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows=[row for row in pool.map(probe,urls.items()) if row]
    logins={s['url'].rstrip('/').split('/')[-1].lower():True for c in watch['creators'] if c.get('enabled',True)
        for s in c['sources'] if s['platform']=='twitch'}
    state_path=ROOT/'work/scheduler/state.json'
    state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    for item in state.get('capture_history',[])+state.get('uploads',[]):
        source=item.get('source',{})
        if not targets_only and source.get('platform')=='twitch' and '/videos/' not in source.get('url',''):
            logins.setdefault(source['url'].rstrip('/').split('/')[-1].lower(),False)
    for login,targeted in logins.items():
        try:
            data=twitch_query('query { user(login:'+json.dumps(login)+') { displayName videos(first:5,type:ARCHIVE,sort:TIME) { edges { node { id title viewCount publishedAt lengthSeconds status game { name } } } } } }').get('user')
            for edge in ((data or {}).get('videos') or {}).get('edges',[]):
                node=edge['node']; game=(node.get('game') or {}).get('name','')
                if lookback_hours is not None and str(node.get('status','')).lower()!='recorded':continue
                if node.get('viewCount') is None:continue
                date=datetime.fromisoformat(node['publishedAt'].replace('Z','+00:00')).astimezone(IST).date()
                ended=None
                if lookback_hours is not None:
                    start=datetime.fromisoformat(node['publishedAt'].replace('Z','+00:00')).timestamp()
                    ended=recent_end(start,node['lengthSeconds'],datetime.now(IST),lookback_hours)
                    if ended is None:continue
                elif date!=datetime.now(IST).date()-timedelta(days=1):continue
                if node['lengthSeconds']<300: continue
                if not (any_gta(game+' '+node['title']) if targeted else title_matches(game,active_game())): continue
                row=dict(platform='twitch',id=node['id'],title=node['title'],channel=data['displayName'],
                    url='https://www.twitch.tv/videos/'+node['id'],viewers=node['viewCount'],targeted=targeted,
                    game='gta' if targeted else active_game(),archive=True,metric='replay_views',game_evidence='archive_category')
                if ended is not None:row.update(recent_replay=True,ended_at=ended)
                rows.append(row)
        except Exception: continue
    unique={(row['platform'],row['id']):row for row in rows}
    return sorted(unique.values(),key=lambda row:-row['viewers'])


def discover_recent(hours=8,targets_only=False):
    return discover_yesterday(lookback_hours=hours,targets_only=targets_only)


def chat_bins(lines,duration):
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
    return bins


def chat_peak(lines,duration):
    bins=chat_bins(lines,duration)
    if not bins:return None
    bucket=max(bins,key=lambda key:len(bins[key]))
    return {'end':min(duration,(bucket+1)*30),'unique_chatters':len(bins[bucket])}


def ranked_moments(info,bins):
    """Fuse global replay heat with locally normalized chat activity."""
    from statistics import median
    duration=float(info['duration'])
    chat=[]
    for bucket,authors in bins.items():
        count=len(authors)
        if count<3:continue
        baseline=max(1,median([len(bins.get(b,set())) for b in range(max(0,bucket-5),bucket)] or [1]))
        chat.append({'time':min(duration,(bucket+1)*30),'count':count,'ratio':min(10,count/baseline)})
    maximum=max((p['count'] for p in chat),default=1)
    for p in chat:p['strength']=.6*p['count']/maximum+.4*p['ratio']/10
    heat=[]
    for p in info.get('heatmap') or []:
        if not isinstance(p.get('value'),(int,float)) or p['value']<=0:continue
        start=p.get('start_time');end=p.get('end_time',start)
        if start is None or end is None:continue
        at=(start+end)/2
        if 0<=at<=duration:heat.append({'time':at,'strength':p['value']})
    hottest=max((p['strength'] for p in heat),default=1)
    for p in heat:p['strength']/=hottest
    rows=[]
    for point in chat+heat:
        nearby_chat=max((p for p in chat if abs(p['time']-point['time'])<=45),key=lambda p:p['strength'],default=None)
        hot=max((p['strength'] for p in heat if abs(p['time']-point['time'])<=45),default=0)
        chatting=nearby_chat['strength'] if nearby_chat else 0
        score=(chatting*.55+hot*.45) if chat and heat else chatting or hot
        rows.append({'source_time':point['time'],'score':round(score,4),
            'unique_chatters':nearby_chat['count'] if nearby_chat else 0,'heat_strength':round(hot,4),
            'method':'combined_replay_signals' if chatting and hot else 'replay_chat_peak' if chatting else 'most_replayed'})
    ranked=[]
    for row in sorted(rows,key=lambda r:r['score'],reverse=True):
        if all(abs(row['source_time']-r['source_time'])>30 for r in ranked):ranked.append(row)
        if len(ranked)==10:break
    return ranked


def audio_moment(info):
    """A bounded, disclosed sample across the VOD when engagement is absent."""
    import numpy as np
    from live_clipper import ffmpeg
    formats=info.get('requested_formats') or info.get('formats') or [info]
    candidates=[f for f in formats if f.get('url') and f.get('vcodec')=='none']
    if not candidates:candidates=[f for f in formats if f.get('url') and f.get('acodec')!='none']
    if not candidates:return None
    media=min(candidates,key=lambda f:f.get('abr') or f.get('tbr') or 9999)
    duration=float(info['duration']);best=None;sampled=0;heard=0
    headers=''.join(f'{k}: {v}\r\n' for k,v in media.get('http_headers',info.get('http_headers',{})).items())
    for at in np.linspace(0,max(0,duration-12),5):
        cmd=[ffmpeg(),'-v','error','-rw_timeout','10000000','-ss',str(float(at))]
        if headers:cmd+=['-headers',headers]
        cmd+=['-i',media['url'],'-t','12','-vn','-ac','1','-ar','8000','-f','s16le','-']
        try:
            result=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=20,check=True)
            pcm=np.frombuffer(result.stdout[:len(result.stdout)//2*2],dtype='<i2').astype(float)/32768
            n=len(pcm)//8000
            if not n:continue
            energy=np.sqrt(np.mean(pcm[:n*8000].reshape(n,8000)**2,axis=1))
            score=float(np.max(energy)-np.median(energy)+.25*np.max(energy))
            sampled+=1;heard+=n
            if score>1e-6 and (best is None or score>best['score']):
                best={'source_time':float(at)+int(np.argmax(energy))+.5,'score':score}
        except (subprocess.SubprocessError,ValueError):continue
    if best:best.update(method='distributed_audio_probe',sampled_windows=sampled,sampled_seconds=heard,
        coverage='Audio sampled at five positions across the VOD; not exhaustive content analysis')
    return best


def replay_window(info,folder,seconds):
    duration=float(info['duration']); bins={}
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
                with path.open(encoding='utf-8') as f:
                    for bucket,authors in chat_bins(f,duration).items():bins.setdefault(bucket,set()).update(authors)
    moments=ranked_moments(info,bins)
    best=moments[0] if moments else audio_moment(info)
    if not best:raise RuntimeError('Replay has no usable engagement or sampled audio signals; trying another source')
    offset=max(0,min(duration-seconds,best['source_time']-seconds*.7))
    return offset,{**best,'end':best['source_time'],'candidates':moments,
        'coverage':best.get('coverage','Available whole-VOD heatmap and downloaded chat replay')}
