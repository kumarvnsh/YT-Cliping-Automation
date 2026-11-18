"""Cached word timing, bounded raw-media cleanup and matched-age observations."""
from datetime import datetime
import json
from pathlib import Path
import time
import re


def slice_transcript(transcript,start,end):
    result={'language':transcript.get('language','auto'),'duration':end-start,'segments':[],
            'review_status':'automatic_transcript_needs_review','model':'clips_kitty_cached'}
    for segment in transcript.get('segments') or []:
        words=[]
        for word in segment.get('words') or []:
            if word['end']>start and word['start']<end:
                words.append({**word,'start':max(0,word['start']-start),
                    'end':min(end-start,word['end']-start)})
        if words:
            result['segments'].append({'start':words[0]['start'],'end':words[-1]['end'],
                'text':' '.join(w['word'].strip() for w in words),'words':words})
    return result


def cleanup(root,days=3,kitty_root=None):
    """Only named raw artifacts; preserve final clips, metadata and analytics."""
    root=Path(root).resolve(); cutoff=time.time()-days*86400; removed=0; freed=0
    candidates=list((root/'work/scheduler').glob('*/capture.mp4'))
    candidates+=list((root/'work/scheduler').glob('*/highlight.mp4'))
    candidates+=list((root/'work/live').glob('*/segment-*.ts'))
    candidates+=list((root/'work/live').glob('*/clip-*.mp4'))
    for path in candidates:
        resolved=path.resolve()
        if resolved.is_relative_to(root/'work') and path.is_file() and path.stat().st_mtime<cutoff:
            freed+=path.stat().st_size; path.unlink(); removed+=1
    if kitty_root:
        downloads=(Path(kitty_root)/'data/downloads').resolve()
        records=list((root/'work/scheduler').glob('*/selection.json'))+list((root/'work/scheduler').glob('*/kitty-job.json'))
        for selection in records:
            if selection.stat().st_mtime>=cutoff: continue
            item=json.loads(selection.read_text(encoding='utf-8'))
            identity=str(item.get('video_id',''))
            if not re.fullmatch(r'[A-Za-z0-9_-]+',identity): continue
            path=(downloads/(identity+'.mp4')).resolve()
            if path.is_relative_to(downloads) and path.is_file() and path.stat().st_mtime<cutoff:
                freed+=path.stat().st_size; path.unlink(); removed+=1
    return {'files_removed':removed,'bytes_freed':freed,'checked_at':time.time()}


def checkpoints(snapshots):
    """Nearest real observation within tolerance; never interpolate an age.

    The 1h tolerance must exceed half the ~30 minute refresh cadence, otherwise a
    slightly late refresh lets the whole window fall between two observations.
    A publish time seen in any snapshot is reused for snapshots where the
    per-video detail lookup failed, so their real view counts still count."""
    published={}
    for snapshot in snapshots:
        for video in snapshot.get('videos',[]):
            if video.get('id') and video.get('published_at'):
                published.setdefault(video['id'],video['published_at'])
    videos={}
    for snapshot in snapshots:
        observed=datetime.fromisoformat(snapshot['checked_at']).timestamp()
        for video in snapshot.get('videos',[]):
            published_at=video.get('published_at') or published.get(video.get('id'))
            if not published_at or video.get('views') is None: continue
            age=(observed-datetime.fromisoformat(published_at.replace('Z','+00:00')).timestamp())/3600
            if age<0: continue
            row=videos.setdefault(video['id'],{'id':video['id'],'title':video.get('title'),'checkpoints':{}})
            for hour,tolerance in [(1,.5),(6,.5),(24,1)]:
                key=str(hour); previous=row['checkpoints'].get(key)
                if abs(age-hour)<=tolerance and (previous is None or abs(age-hour)<abs(previous['age_hours']-hour)):
                    row['checkpoints'][key]={'views':video['views'],'age_hours':round(age,4),
                        'observed_at':snapshot['checked_at']}
    return [v for v in videos.values() if v['checkpoints']]
