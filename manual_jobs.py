"""Persistent manual clipping queue, shared by dashboard and local worker."""
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlparse,parse_qs
from contextlib import contextmanager
import uuid
from studio_events import scope as studio_scope, emit as studio_emit

ROOT=Path(__file__).resolve().parent


@contextmanager
def connection(root=None):
    folder=Path(root or ROOT)/'work/manual'; folder.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(folder/'jobs.sqlite',timeout=10)
    db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,status TEXT,created REAL,updated REAL,stage TEXT,note TEXT,options TEXT,result TEXT,error TEXT,cancel INTEGER DEFAULT 0)')
    try:
        with db: yield db
    finally:
        db.close()


def source_url(value):
    p=urlparse(str(value).strip())
    if p.scheme!='https' or p.username or p.password or p.port or p.fragment:
        raise ValueError('Use a normal HTTPS YouTube or Twitch URL')
    host=(p.hostname or '').lower()
    if host in ('youtube.com','www.youtube.com','m.youtube.com'):
        if p.path=='/watch' and re.fullmatch(r'[\w-]{11}',parse_qs(p.query).get('v',[''])[0]):
            return 'https://www.youtube.com/watch?v='+parse_qs(p.query)['v'][0]
        if re.fullmatch(r'/@[A-Za-z0-9_.-]+/live',p.path): return 'https://www.youtube.com'+p.path
    if host=='youtu.be' and re.fullmatch(r'/[\w-]{11}',p.path): return 'https://www.youtube.com/watch?v='+p.path[1:]
    if host in ('twitch.tv','www.twitch.tv') and re.fullmatch(r'/(?:videos/\d+|[A-Za-z0-9_]+)',p.path.rstrip('/')):
        return 'https://www.twitch.tv'+p.path.rstrip('/')
    raise ValueError('Enter a YouTube video/live URL or a Twitch channel/VOD URL')


def seconds(value):
    parts=str(value).strip().split(':')
    if len(parts)>3 or any(not re.fullmatch(r'\d+(?:\.\d+)?',p) for p in parts):
        raise ValueError('Use seconds or HH:MM:SS for timestamps')
    if any(float(p)>=60 for p in parts[1:]): raise ValueError('Minutes and seconds must be below 60')
    result=0
    for p in parts: result=result*60+float(p)
    if result>72*3600: raise ValueError('Timestamp is beyond 72 hours')
    return result


def validate(body):
    mode=body.get('mode','auto')
    if mode not in ('auto','live','replay','exact'): raise ValueError('Unknown clipping mode')
    platforms=body.get('platforms',['youtube','facebook'])
    if not isinstance(platforms,list) or not platforms or any(p not in ('youtube','facebook') for p in platforms):
        raise ValueError('Choose YouTube, Facebook, or both')
    privacy=body.get('privacy','public')
    if privacy not in ('public','unlisted','private'): raise ValueError('Invalid YouTube privacy')
    if privacy!='public' and 'facebook' in platforms: raise ValueError('Facebook Reels are public; choose YouTube only for private/unlisted')
    title=str(body.get('hook','')).strip()
    if len(title)>85: raise ValueError('Hook must be 85 characters or fewer')
    language=body.get('language','auto')
    if language not in ('auto','en','hi','es','pt','id'): raise ValueError('Unsupported caption language')
    capture=int(body.get('capture_seconds',300))
    if not 60<=capture<=600: raise ValueError('Record between 60 and 600 seconds')
    ranges=[]
    if mode=='exact':
        text=str(body.get('ranges','')).strip()
        if not text: raise ValueError('Enter a start and end time, such as 01:20 - 01:50')
        for line in text.splitlines():
            pair=re.split(r'\s*[-–]\s*',line.strip())
            if len(pair)!=2: raise ValueError('Each line needs start - end')
            start,end=map(seconds,pair)
            if not 5<=end-start<=60: raise ValueError('Each clip must be between 5 and 60 seconds')
            ranges.append([start,end])
        if len(ranges)>5: raise ValueError('Submit at most five moments at a time')
    return dict(mode=mode,url=source_url(body.get('url','')) if mode!='auto' else '',
        platforms=list(dict.fromkeys(platforms)),privacy=privacy,hook=title,language=language,
        capture_seconds=capture,publish=body.get('publish') is True,ranges=ranges)


def decode(row):
    if row is None: return None
    value=dict(row); value['options']=json.loads(value['options']); value['result']=json.loads(value['result'] or '{}')
    return value


def enqueue(body,root=None):
    with connection(root) as db:
        return enqueue_options(db, validate(body))


def enqueue_options(db, options):
    return [enqueue_job(db, dict(options, window=window)) for window in options['ranges'] or [None]]


def enqueue_job(db, options):
    identity=uuid.uuid4().hex
    at=time.time()
    db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,0)',
               (identity,'queued',at,at,'queued','Waiting for the current job to finish',json.dumps(options),'{}',None))
    return identity


def list_jobs(root=None):
    with connection(root) as db: return [decode(r) for r in db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 100')]


def get(identity,root=None):
    with connection(root) as db: return decode(db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone())


def update(identity,root=None,**fields):
    allowed={'status','stage','note','result','error','cancel','options'}
    if not fields or not set(fields)<=allowed: raise ValueError('Invalid job update')
    if 'result' in fields: fields['result']=json.dumps(fields['result'])
    if 'options' in fields: fields['options']=json.dumps(fields['options'])
    fields['updated']=time.time()
    with connection(root) as db:
        db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),identity))
    with studio_scope(Path(root or ROOT), 'shotsnip', identity, fields.get('stage', 'queue')):
        studio_emit('job_updated', status=fields.get('status'), cancel=fields.get('cancel'))


def publishing_paused(root=None):
    """The owner paused manual publishing: waiting clips stay queued and live operations do not wait for them."""
    try:
        return bool(json.loads((Path(root or ROOT)/'scheduler.json').read_text(encoding='utf-8')).get('manual_publishing_paused'))
    except (OSError,ValueError,AttributeError):
        return False


def pending(root=None):
    statuses="('queued','running','publishing')" if publishing_paused(root) else "('queued','queued_publish','running','publishing')"
    with connection(root) as db:
        return bool(db.execute(f"SELECT 1 FROM jobs WHERE status IN {statuses} LIMIT 1").fetchone())


def recover_interrupted(root=None):
    """Caller must own the shared worker lock."""
    with connection(root) as db:
        db.execute("UPDATE jobs SET status='failed',stage='failed',error='Worker stopped during processing; retry is available' WHERE status='running'")
        db.execute("UPDATE jobs SET status='attention',stage='attention',error='Worker stopped while publishing; reconcile platform status before retry' WHERE status='publishing'")


MAX_SCHEDULE_DAYS=30
MANUAL_GAP_CHOICES=(30,60,90,120,180,240,360,480)


def publish_gap_minutes(job,config):
    """Minutes a manual clip waits after the previous upload, shared with the posting window."""
    if isinstance(config.get('manual_post_minutes'),int):
        return config['manual_post_minutes']
    if job.get('options',{}).get('local_library_batch'):
        return 120
    return max(0,config.get('post_minutes',120)-config.get('early_publish_minutes',0))


def due(job,now=None):
    """A clip scheduled for later is not claimed before its time."""
    after=job.get('options',{}).get('not_before')
    return not isinstance(after,(int,float)) or after<=(now or time.time())


def estimate(rows,last_upload_at,config,now=None):
    """Expected publish times for waiting clips, following the worker's claim order and gaps."""
    now=now or time.time(); waiting=[r for r in rows if r['status']=='queued_publish']
    waiting.sort(key=lambda r:(r['created'],r['id'])); times={}; last=last_upload_at or 0; clock=now
    while waiting:
        def ready_at(job):
            return max(clock,last+publish_gap_minutes(job,config)*60,job['options'].get('not_before') or 0)
        job=min(waiting,key=lambda r:(ready_at(r),r['created'],r['id']))
        clock=ready_at(job); times[job['id']]=clock; last=clock; waiting.remove(job)
    return times


def claim(root=None):
    with connection(root) as db:
        db.execute('BEGIN IMMEDIATE')
        rows=[decode(r) for r in db.execute("SELECT * FROM jobs WHERE status IN ('queued','queued_publish') ORDER BY created,id")]
        paused=publishing_paused(root)
        job=next((r for r in rows if r['status']=='queued' or (due(r) and not paused)),None)
        if not job: return None
        job['publishing_only']=job['status']=='queued_publish'
        db.execute("UPDATE jobs SET status='running',updated=? WHERE id=?",(time.time(),job['id']))
        return job


def control(identity,action,root=None):
    with connection(root) as db:
        db.execute('BEGIN IMMEDIATE')
        control_job(db, identity, action)


def control_job(db, identity, action, expected=None, value=None):
    job=decode(db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone())
    if not job: raise ValueError('Job not found')
    if expected is not None and job['updated'] != expected:
        raise ValueError('The job changed. Refresh and review it again.')

    def change(**fields):
        if 'options' in fields:
            fields['options']=json.dumps(fields['options'])
        fields['updated']=time.time()
        db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),identity))

    if action=='cancel':
        if job['status'] in ('queued','queued_publish'): change(status='cancelled',stage='cancelled',note='Cancelled before starting')
        elif job['status']=='running': change(cancel=1,note='Stopping after this stage')
        else: raise ValueError('This job is not active')
    elif action=='retry':
        if job['status']!='failed': raise ValueError('Only a failed processing job can be retried')
        change(status='queued',stage='queued',cancel=0,error=None)
    elif action=='publish':
        if job['status']!='ready': raise ValueError('Only a ready preview can be published')
        change(status='queued_publish',stage='queued',cancel=0)
    elif action=='retry_facebook':
        publication=job['result'].get('publication',{})
        facebook=publication.get('facebook') or {}
        if job['status']!='partial' or not publication.get('youtube_id') or not re.search(r'failed \((?:400|401|403|404|422)\)',facebook.get('error','')):
            raise ValueError('Facebook outcome must be a confirmed rejection before retrying it')
        options={**job['options'],'platforms':['facebook']}
        change(options=options,status='queued_publish',stage='queued',cancel=0,error=None)
    elif action in ('move_earlier','move_later'):
        # Claim order is creation time: swap places with the neighbouring waiting clip.
        if job['status'] not in ('queued','queued_publish'): raise ValueError('Only a waiting clip can be moved')
        waiting=[r['id'] for r in db.execute("SELECT id FROM jobs WHERE status IN ('queued','queued_publish') ORDER BY created,id")]
        stamps=[r[0] for r in db.execute("SELECT created FROM jobs WHERE status IN ('queued','queued_publish') ORDER BY created,id")]
        index=waiting.index(identity); other=index-1 if action=='move_earlier' else index+1
        if not 0<=other<len(waiting): raise ValueError('This clip is already '+('first' if action=='move_earlier' else 'last'))
        waiting[index],waiting[other]=waiting[other],waiting[index]
        # Keep the original times but make them strictly increasing so equal timestamps still reorder.
        for position,(job_id,stamp) in enumerate(zip(waiting,stamps)):
            db.execute('UPDATE jobs SET created=? WHERE id=?',(stamp+position*1e-6,job_id))
        db.execute('UPDATE jobs SET updated=? WHERE id=?',(time.time(),identity))
    elif action=='schedule':
        if job['status'] not in ('ready','queued','queued_publish'): raise ValueError('Only a waiting clip can be scheduled')
        options=dict(job['options'])
        if value is None:
            options.pop('not_before',None)
        else:
            if not isinstance(value,(int,float)) or isinstance(value,bool) or not time.time()-60<=value<=time.time()+MAX_SCHEDULE_DAYS*86400:
                raise ValueError(f'Choose a time within the next {MAX_SCHEDULE_DAYS} days')
            options['not_before']=float(value)
        change(options=options)
    elif action=='prioritize':
        # Claim order is creation time, so moving to the front means sorting before every queued job.
        if job['status'] not in ('queued','queued_publish'): raise ValueError('Only a waiting job can move to the front')
        first=db.execute("SELECT MIN(created) FROM jobs WHERE status IN ('queued','queued_publish')").fetchone()[0]
        if first is not None and first<job['created']:
            change(created=first-1)
    else: raise ValueError('Unknown job control')
