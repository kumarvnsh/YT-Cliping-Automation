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


ACTIVE_JOBS = ('queued', 'running', 'queued_publish', 'publishing', 'attention')


def stale_runs(root, days=3, at=None):
    """Clip folders that were never posted and can no longer be: older than `days`, not in the upload
    ledger (posted clips are backed up to Drive and cleaned by Studio HQ), not waiting in the ready
    queue, not part of a manual job that is still going, and not marked .studio-hq-keep.

    The owner chose (23 Nov) to delete these: before that day finished clips that missed the posting
    window were abandoned, and 70 folders (4.7 GB) had piled up. Deleting them is permanent.
    """
    root = Path(root).resolve()
    cutoff = (at or time.time()) - days*86400
    try:
        state = json.loads((root/'work/scheduler/state.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []  # without the ledger nothing can be proven unused
    used = json.dumps([state.get('uploads', []), state.get('ready_clips', []), state.get('selected')],
                      ensure_ascii=False).replace('\\\\', '/').casefold()
    active = None
    try:
        import manual_jobs
        with manual_jobs.connection(root) as db:
            active = {r['id'] for r in db.execute('SELECT id,status FROM jobs') if r['status'] in ACTIVE_JOBS or r['status'] is None}
    except Exception:  # noqa: BLE001 - unknown manual state: manual folders are kept
        active = None
    found = []
    for base in ('scheduler', 'manual'):
        folder = root/'work'/base
        if not folder.is_dir():
            continue
        for run in folder.iterdir():
            if not run.is_dir() or (run/'.studio-hq-keep').exists():
                continue
            if base == 'manual' and (active is None or run.name in active):
                continue
            files = [f for f in run.rglob('*') if f.is_file()]
            if not files or max(f.stat().st_mtime for f in files) >= cutoff:
                continue
            if run.name.casefold() in used:
                continue
            found.append(run)
    return found


def finished_manual_records(root):
    """Kitty records of manual / library jobs that are over (published, cancelled or failed).

    Their Clips Kitty downloads (local_*.mp4) were never cleaned: only scheduler records were checked.
    A job still queued, publishing or needing attention keeps its download.
    """
    done=set()
    try:
        import manual_jobs
        with manual_jobs.connection(root) as db:
            done={r['id'] for r in db.execute("SELECT id FROM jobs WHERE status IN ('published','cancelled','failed')")}
    except Exception:  # noqa: BLE001 - no job database: nothing manual is cleaned
        return []
    folder=Path(root)/'work/manual'
    return [p for job in done for name in ('kitty-job.json','selection.json') if (p:=folder/job/name).is_file()]


def cleanup(root,days=3,kitty_root=None):
    """Only named raw artifacts; preserve final clips, metadata and analytics."""
    root=Path(root).resolve(); cutoff=time.time()-days*86400; removed=0; freed=0
    candidates=list((root/'work/scheduler').glob('*/capture.mp4'))
    candidates+=list((root/'work/scheduler').glob('*/highlight.mp4'))
    # Raw transport segments are handled by the lock- and queue-aware retention guard.
    candidates+=list((root/'work/live').glob('*/clip-*.mp4'))
    for path in candidates:
        resolved=path.resolve()
        if resolved.is_relative_to(root/'work') and path.is_file() and path.stat().st_mtime<cutoff:
            freed+=path.stat().st_size; path.unlink(); removed+=1
    if kitty_root:
        downloads=(Path(kitty_root)/'data/downloads').resolve()
        records=list((root/'work/scheduler').glob('*/selection.json'))+list((root/'work/scheduler').glob('*/kitty-job.json'))
        records+=finished_manual_records(root)
        for selection in records:
            if selection.stat().st_mtime>=cutoff: continue
            item=json.loads(selection.read_text(encoding='utf-8'))
            identity=str(item.get('video_id',''))
            if not re.fullmatch(r'[A-Za-z0-9_-]+',identity): continue
            path=(downloads/(identity+'.mp4')).resolve()
            if path.is_relative_to(downloads) and path.is_file() and path.stat().st_mtime<cutoff:
                freed+=path.stat().st_size; path.unlink(); removed+=1
    import shutil
    for run in stale_runs(root, 0.25):  # never posted and not queued for 6 hours: can never post, removed for good (owner, 23 Nov)
        size=sum(f.stat().st_size for f in run.rglob('*') if f.is_file())
        shutil.rmtree(run, ignore_errors=True)
        if not run.exists():
            freed+=size; removed+=1
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
            for hour,tolerance in [(1,.5),(6,.5),(24,1),(72,2)]:
                key=str(hour); previous=row['checkpoints'].get(key)
                if abs(age-hour)<=tolerance and (previous is None or abs(age-hour)<abs(previous['age_hours']-hour)):
                    row['checkpoints'][key]={'views':video['views'],'age_hours':round(age,4),
                        'observed_at':snapshot['checked_at']}
    return [v for v in videos.values() if v['checkpoints']]
