"""Persistent manual clipping queue, shared by dashboard and local worker."""
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlparse,parse_qs
from contextlib import contextmanager
import uuid

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
    options=validate(body); ids=[]
    for window in options['ranges'] or [None]:
        item={**options,'window':window}; identity=uuid.uuid4().hex; at=time.time()
        with connection(root) as db:
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,0)',(identity,'queued',at,at,'queued','Waiting for the current job to finish',json.dumps(item),'{}',None))
        ids.append(identity)
    return ids


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


def pending(root=None):
    with connection(root) as db:
        return bool(db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','queued_publish','running','publishing') LIMIT 1").fetchone())


def recover_interrupted(root=None):
    """Caller must own the shared worker lock."""
    with connection(root) as db:
        db.execute("UPDATE jobs SET status='failed',stage='failed',error='Worker stopped during processing; retry is available' WHERE status='running'")
        db.execute("UPDATE jobs SET status='attention',stage='attention',error='Worker stopped while publishing; reconcile platform status before retry' WHERE status='publishing'")


def claim(root=None):
    with connection(root) as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT * FROM jobs WHERE status IN ('queued','queued_publish') ORDER BY created LIMIT 1").fetchone()
        if not row: return None
        job=decode(row); job['publishing_only']=job['status']=='queued_publish'
        db.execute("UPDATE jobs SET status='running',updated=? WHERE id=?",(time.time(),job['id']))
        return job


def control(identity,action,root=None):
    job=get(identity,root)
    if not job: raise ValueError('Job not found')
    if action=='cancel':
        if job['status'] in ('queued','queued_publish'): update(identity,root,status='cancelled',stage='cancelled',note='Cancelled before starting')
        elif job['status']=='running': update(identity,root,cancel=1,note='Stopping after this stage')
        else: raise ValueError('This job is not active')
    elif action=='retry':
        if job['status']!='failed': raise ValueError('Only a failed processing job can be retried')
        update(identity,root,status='queued',stage='queued',cancel=0,error=None)
    elif action=='publish':
        if job['status']!='ready': raise ValueError('Only a ready preview can be published')
        update(identity,root,status='queued_publish',stage='queued',cancel=0)
    elif action=='retry_facebook':
        publication=job['result'].get('publication',{})
        facebook=publication.get('facebook') or {}
        if job['status']!='partial' or not publication.get('youtube_id') or not re.search(r'failed \((?:400|401|403|404|422)\)',facebook.get('error','')):
            raise ValueError('Facebook outcome must be a confirmed rejection before retrying it')
        options={**job['options'],'platforms':['facebook']}
        update(identity,root,options=options,status='queued_publish',stage='queued',cancel=0,error=None)
    elif action=='prioritize':
        # Claim order is creation time, so moving to the front means sorting before every queued job.
        if job['status'] not in ('queued','queued_publish'): raise ValueError('Only a waiting job can move to the front')
        with connection(root) as db:
            first=db.execute("SELECT MIN(created) FROM jobs WHERE status IN ('queued','queued_publish')").fetchone()[0]
            if first is not None and first<job['created']:
                db.execute('UPDATE jobs SET created=?,updated=? WHERE id=?',(first-1,time.time(),identity))
    else: raise ValueError('Unknown job control')
