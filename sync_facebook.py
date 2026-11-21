"""Read-only Shotsnip Page metrics; never exports tokens or paging URLs."""
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import requests
import facebook_shotsnip as fb

ROOT=Path(__file__).resolve().parent
REEL_METRICS=('fb_reels_total_plays','blue_reels_play_count','fb_reels_replay_count')


def metric_values(report):
    """Read named numeric values only; empty data is not a zero count."""
    values={}
    for metric in report.get('data',[]):
        rows=metric.get('values') or []
        value=rows[-1].get('value') if rows else None
        if isinstance(value,(int,float)) and not isinstance(value,bool) and value>=0:
            values[metric.get('name')]=value
    return values


def video_metrics(get, video):
    errors=[];values={}
    try:
        values=metric_values(get(video['id']+'/video_insights',metric=','.join(REEL_METRICS),period='lifetime'))
    except RuntimeError as exc:errors.append(str(exc))
    # Older API surfaces can reject a combined metric list while still
    # supporting the initial-play metric on its own.
    if not any(name in values for name in REEL_METRICS[:2]):
        try:
            values.update(metric_values(get(video['id']+'/video_insights',metric='blue_reels_play_count',period='lifetime')))
        except RuntimeError as exc:errors.append(str(exc))
    if not any(name in values for name in REEL_METRICS[:2]):
        try:
            values.update(metric_values(get(video['id']+'/video_insights',metric='total_video_views',period='lifetime')))
        except RuntimeError as exc:errors.append(str(exc))
    metric=next((name for name in (*REEL_METRICS[:2],'total_video_views') if name in values),None)
    row={**video,'views':values.get(metric),'views_metric':metric,
         'plays':values.get('fb_reels_total_plays'),'initial_plays':values.get('blue_reels_play_count'),
         'replays':values.get('fb_reels_replay_count'),'three_second_views':values.get('total_video_views'),
         'insight_status':'available' if metric else 'unavailable'}
    if not metric:
        row['error']='; '.join(errors) or 'Meta has not returned a view metric for this video yet.'
    return row


def sync():
    cfg=fb._cfg(); token=fb._page_token(fb._token(),cfg['page_id'],cfg['api_version'])
    def get(node,**params):
        r=requests.get(fb.GRAPH+'/'+cfg['api_version']+'/'+node,params=params,
            headers={'Authorization':'Bearer '+token},timeout=25)
        try: data=r.json()
        except ValueError: raise RuntimeError('Facebook returned a non-JSON response') from None
        if not r.ok or data.get('error'):
            raise RuntimeError('Facebook metrics unavailable (code '+str(data.get('error',{}).get('code',r.status_code))+')')
        return data
    page=get(cfg['page_id'],fields='id,name,followers_count,fan_count')
    if page['id']!=cfg['page_id']: raise RuntimeError('Facebook Page identity mismatch')
    now=datetime.now(timezone.utc)
    result={'status':'connected','page_id':page['id'],'name':page['name'],'checked_at':now.isoformat(),
        'followers':page.get('followers_count'),'page_likes':page.get('fan_count'),'videos':[],
        'source':'Meta Graph API','coverage':'Latest 200 Page videos plus tracked cross-posts',
        'daily':[],'errors':[],
        'view_definition':'Reel plays include initial plays and replays. Initial plays exclude replays. These are not three-second views or unique viewers.',
        'window_view_definition':'Page video views: plays of at least 3 seconds, over the reported daily window; differs from Reel plays.'}
    ids={}; after=None
    for _ in range(4):
        response=get(cfg['page_id']+'/videos',fields='id,title,description,created_time,permalink_url',limit=50,after=after)
        for row in response.get('data',[]): ids[row['id']]=row
        after=response.get('paging',{}).get('cursors',{}).get('after')
        if not response.get('paging',{}).get('next') or not after: break
    meta_created_ids={id_ for id_, row in ids.items() if row.get('created_time')}
    state_path=ROOT/'work/scheduler/state.json'
    state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    for upload in state.get('uploads',[]):
        reel=upload.get('facebook',{}).get('reel_id')
        if reel:
            row=ids.setdefault(reel,{'id':reel})
            metadata={}
            path=Path(upload.get('file','')).parent/'metadata.json'
            if path.is_file():
                try:metadata=json.loads(path.read_text(encoding='utf-8'))
                except (OSError,ValueError):pass
            row['title']=upload.get('title') or metadata.get('title') or row.get('title') or (row.get('description') or '').split('\n')[0] or 'Shotsnip Reel'
            row['creator']=upload.get('source',{}).get('channel') or metadata.get('creator')
            row['youtube_id']=upload.get('video_id')
            row.setdefault('created_time',upload.get('finished_at') or upload.get('started_at'))
            row.setdefault('permalink_url','https://www.facebook.com/reel/'+str(reel)+'/')
    for video in ids.values():
        video['title']=video.get('title') or (video.get('description') or '').split('\n')[0] or 'Shotsnip Reel '+video['id']
        video.pop('description',None)
        row=video_metrics(get,video)
        row['created_time_source']='meta' if video['id'] in meta_created_ids else 'unverified'
        row['checked_at']=now.isoformat()
        result['videos'].append(row)
    videos=result['videos']
    metrics={v['views_metric'] for v in videos}
    result['video_views']=sum(v['views'] for v in videos) if all(v['views'] is not None for v in videos) and len(metrics)<=1 else None
    result['aggregate_views_metric']=next(iter(metrics)) if len(metrics)==1 else None
    reels=[v for v in videos if '/reel/' in v.get('permalink_url','') or v['plays'] is not None or v['initial_plays'] is not None]
    result['reel_plays_total']=sum(v['plays'] for v in reels) if all(v['plays'] is not None for v in reels) else None
    result['measured_videos']=sum(v['views'] is not None for v in videos)
    result['missing_videos']=len(videos)-result['measured_videos']
    result['metrics_status']='partial' if result['missing_videos'] else 'complete'
    try:
        report=get(cfg['page_id']+'/insights',metric='page_video_views',period='day',
            since=(now.date()-timedelta(days=27)).isoformat(),until=(now.date()+timedelta(days=1)).isoformat())
        for metric in report.get('data',[]):
            result['daily']=metric.get('values',[])
        result['window_views']=sum(row['value'] for row in result['daily']) if result['daily'] else None
    except RuntimeError as exc:
        result['window_views']=None; result['errors'].append(str(exc))
    from sync_analytics import save
    save(ROOT/'work/dashboard/facebook-analytics.json',result)
    save(ROOT/'work/dashboard/facebook-analytics-history'/f'{now.strftime("%Y%m%dT%H%M%SZ")}.json',result)
    save(ROOT/'work/dashboard/facebook-health.json',{'status':'connected','checked_at':result['checked_at'],
        'metrics_status':result['metrics_status'],'measured_videos':result['measured_videos'],'missing_videos':result['missing_videos']})
    return result


if __name__=='__main__':
    r=sync(); print('Facebook metrics refreshed:',r['name'],len(r['videos']),'videos')
