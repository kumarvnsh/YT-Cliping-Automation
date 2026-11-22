"""Owner-approved local-library reformat queue, using the shared manual worker."""
from pathlib import Path
import json

BATCH='local-library-20261121'

def interval(job):
    return 120 if job.get('options',{}).get('local_library_batch')==BATCH else None

def enforce_hold():
    import scheduled_clips as w
    config=w.read(w.ROOT/'scheduler.json',{})
    flag=w.ROOT/'work/dashboard/live-operations-batch-hold.json'
    if (config.get('ended_batch_hold') or {}).get('batch')==BATCH:
        # The owner ended this hold; their decision stands and is never re-applied automatically.
        flag.unlink(missing_ok=True)
        if config.pop('live_operations_batch_hold',None) is not None:w.save(w.ROOT/'scheduler.json',config)
        return
    hold=w.read(flag,{}) or config.get('live_operations_batch_hold') or {}
    if hold.get('batch')!=BATCH or len(hold.get('job_ids',[]))!=15:return
    if not flag.exists():w.save(flag,hold)
    if config.get('enabled') or config.get('live_operations_batch_hold')!=hold:
        config.update(enabled=False,live_operations_batch_hold=hold)
        w.save(w.ROOT/'scheduler.json',config)

def resume_if_complete():
    """Resume live operations only after every explicitly listed batch job finishes."""
    import scheduled_clips as w
    import manual_jobs as jobs
    enforce_hold()
    config=w.read(w.ROOT/'scheduler.json',{})
    hold=config.get('live_operations_batch_hold') or {}
    identities=hold.get('job_ids',[])
    if hold.get('batch')!=BATCH or len(identities)!=15:return False
    for identity in identities:
        job=jobs.get(identity)
        publication=(job or {}).get('result',{}).get('publication',{})
        if not job or job.get('status')!='published' or not publication.get('youtube_id') or publication.get('facebook',{}).get('status')!='published':
            return False
    config['enabled']=True
    config.pop('live_operations_batch_hold',None)
    config['last_completed_library_batch']={'batch':BATCH,'completed_at':w.now()}
    w.save(w.ROOT/'scheduler.json',config)
    (w.ROOT/'work/dashboard/live-operations-batch-hold.json').unlink(missing_ok=True)
    return True

def prepare(job, stage):
    import scheduled_clips as w
    from finish_video import finish
    from gta_moments import review
    from clip_titles import game_label
    result=job['result']; original=Path(result['original_file'])
    raw=Path(result['raw_file'])
    if not raw.resolve().is_relative_to((w.ROOT/'work/live').resolve()):
        raise ValueError('Local source is outside the clip library')
    stage('analyzing','Reviewing the local moment for an accurate title and description')
    cached=original.parent/'transcript.json'
    check=review(cached if cached.exists() else None,video=raw)
    if check.get('review_unavailable') or not check.get('hook') or not check.get('summary'):
        raise RuntimeError('Cannot establish an accurate title; local clip retained for review')
    creator=result['source']['channel'];game=game_label(result['source'])
    hook=check['hook'].strip().rstrip('.')
    title=f'{creator}: {hook} ({game})'
    if len(title)>100:title=f'{creator}: {hook[:max(8,94-len(creator)-len(game))].rsplit(" ",1)[0]} ({game})'
    stage('finishing','Applying current camera layout and speech captions')
    output=w.ROOT/'work/manual'/job['id']/'reformatted'
    finished=finish(raw,title,creator,output_dir=output,transcript_file=cached if cached.exists() else None,
                    category=game,source_url=result['source']['url'])
    metadata=w.read(finished.parent/'metadata.json',{})
    metadata.update(title=title,description=f"{creator} in {game}: {check['summary']}\n\nOriginal stream: {creator}\n{result['source']['url']}",
                    owner_approved_batch=BATCH,packaging_evidence=check)
    w.save(finished.parent/'metadata.json',metadata)
    return {**result,'file':str(finished),'title':title,'prepared':True}
