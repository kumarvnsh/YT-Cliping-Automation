"""Read-only Shotsnip Page metrics; never exports tokens or paging URLs."""
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import requests
import facebook_shotsnip as fb

ROOT=Path(__file__).resolve().parent


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
        'daily':[],'errors':[],'view_definition':'Facebook views: at least 3 seconds; differs from YouTube.'}
    ids={}; after=None
    for _ in range(4):
        response=get(cfg['page_id']+'/videos',fields='id,title,created_time,permalink_url',limit=50,after=after)
        for row in response.get('data',[]): ids[row['id']]=row
        after=response.get('paging',{}).get('cursors',{}).get('after')
        if not response.get('paging',{}).get('next') or not after: break
    state_path=ROOT/'work/scheduler/state.json'
    state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    for upload in state.get('uploads',[]):
        reel=upload.get('facebook',{}).get('reel_id')
        if reel: ids.setdefault(reel,{'id':reel,'title':'Shotsnip Reel'})
    for video in ids.values():
        row={**video,'views':None}
        # Classic videos report total_video_views; Reels leave it empty or reject it and
        # report plays as blue_reels_play_count instead, so try both before giving up.
        for metric in ('total_video_views','blue_reels_play_count'):
            try:
                metrics=get(video['id']+'/video_insights',metric=metric,period='lifetime')
                values=metrics.get('data',[])
                row['views']=next((m['values'][-1]['value'] for m in values if m.get('values')),None)
            except RuntimeError as exc:
                row['error']=str(exc); continue
            if row['views'] is not None:
                row.pop('error',None); break
        result['videos'].append(row)
    result['video_views']=sum(v['views'] for v in result['videos']) if all(v['views'] is not None for v in result['videos']) else None
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
    save(ROOT/'work/dashboard/facebook-health.json',{'status':'connected','checked_at':result['checked_at']})
    return result


if __name__=='__main__':
    r=sync(); print('Facebook metrics refreshed:',r['name'],len(r['videos']),'videos')
