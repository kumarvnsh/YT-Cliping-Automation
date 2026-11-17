"""One durable, non-overlapping Shotsnip scheduling tick (run every 30 minutes)."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import subprocess
import shutil
import time
import uuid
import requests
from game_schedule import active_game, title_matches, IST
from live_clipper import ffmpeg, resolve
from stream_sources import scan, choose, choose_varied, any_gta

ROOT = Path(__file__).resolve().parent
WORK = ROOT / 'work' / 'scheduler'
KITTY = 'http://127.0.0.1:8765'


def source_allowed(source):
    return bool(source.get('targeted') and source.get('game')=='gta') or source.get('game')==active_game()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


def read(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def now():
    return datetime.now(timezone.utc).isoformat()


def progress(state, phase, note):
    at=time.time()
    state.update(phase=phase,phase_started_at=at,heartbeat_at=at,note=note)
    state.setdefault('events',[]).append({'at':at,'phase':phase,'note':note})
    state['events']=state['events'][-30:]
    save(WORK/'state.json',state)


def usable_hook(selected, source):
    title=selected.get('title','').strip()
    # English template: avoid putting garbled cross-language ASR back into a hook.
    if title and title.isascii() and len(title)<=100:
        return title
    game='GTA' if source['game']=='gta' else 'GTA VI' if source['game']=='gta6' else 'GTA V'
    return f"{source['channel']}'s {game} live moment"[:100]


def api(path, payload=None):
    response = (requests.get(KITTY + path, timeout=30) if payload is None else
                requests.post(KITTY + path, json=payload, timeout=60))
    response.raise_for_status()
    return response.json()


def ensure_kitty():
    try:
        return api('/health')
    except requests.ConnectionError:
        install=Path('D:/Clips-Kitty')
        with (WORK/'kitty-service.log').open('a',encoding='utf-8') as log:
            subprocess.Popen([str(install/'.venv/Scripts/python.exe'),'-u',str(install/'main.py'),
                '--config',str(install/'config/shotsnip.yaml'),'serve','--host','127.0.0.1','--port','8765'],
                cwd=install,stdout=log,stderr=log,env={**os.environ,'PYTHONUTF8':'1'},
                creationflags=subprocess.CREATE_NO_WINDOW)
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            try:
                return api('/health')
            except requests.ConnectionError:
                time.sleep(2)
        raise RuntimeError('Clips Kitty could not start; inspect local kitty-service.log')


def capture(source, folder, seconds=180):
    """Bounded public media capture; Twitch uses an anonymous playback token."""
    if shutil.disk_usage(folder).free<3*1024**3:
        raise RuntimeError('Less than 3 GB free; capture paused')
    info = resolve(source['url'])
    if source['platform']=='youtube' and not (any_gta(info.get('title')) if source.get('targeted') else title_matches(info.get('title'),source['game'])):
        raise RuntimeError('Live source title changed game since discovery; capture refused')
    output = folder / 'capture.mp4'
    command = [ffmpeg(), '-hide_banner', '-loglevel', 'warning', '-y']
    inputs = info.get('requested_formats') or [info]
    for item in inputs:
        headers = ''.join(f'{k}: {v}\r\n' for k, v in item.get('http_headers', info.get('http_headers', {})).items())
        command += ['-rw_timeout','20000000','-headers',headers,'-i',item['url']]
    command += ['-map','0:v:0','-map','1:a:0' if len(inputs)>1 else '0:a:0?',
                '-t',str(seconds),'-c','copy','-movflags','+faststart',str(output)]
    from live_signals import LiveSignals
    signals=LiveSignals(source).start()
    try:
        with (folder / 'capture.log').open('w', encoding='utf-8') as log:
            subprocess.run(command, stdout=subprocess.DEVNULL, stderr=log, check=True, timeout=seconds+90)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise RuntimeError('Live capture failed or timed out; inspect its private capture.log') from None
    finally:
        save(folder/'chat-signals.json',signals.close())
    return output


def kitty_select(source, video, state=None):
    preflight = api('/health/preflight')
    blockers = [c for c in preflight.get('checks', []) if c.get('blocking') and not c.get('ok')]
    if blockers:
        raise RuntimeError('Clips Kitty preflight: ' + '; '.join(c['name'] for c in blockers))
    job = api('/videos/local', {'path':str(video), 'title':source['title'],
        'channel':source['channel'], 'max_clips':1, 'min_score':55, 'captions':False,
        'focus':'A funny or surprising GTA gameplay moment with a clear payoff. Avoid loading screens and dead air.'})
    job_id = job.get('job_id', job.get('id'))
    save(video.parent/'kitty-job.json',job)
    if job_id is None:
        raise RuntimeError('Clips Kitty did not return a job ID')
    api('/queue/resume', {})
    deadline = time.monotonic()+2100
    while time.monotonic() < deadline:
        if state is not None:
            state['heartbeat_at']=time.time(); save(WORK/'state.json',state)
        result = api(f'/jobs/{job_id}')
        if result['status'] == 'failed':
            raise RuntimeError('Clips Kitty job failed; inspect its local job log')
        if result['status'] == 'done':
            clips = api('/videos/' + result['video_id'] + '/clips')
            if not clips:
                return None
            return max(clips, key=lambda clip: clip.get('score', 0))
        time.sleep(5)
    raise TimeoutError('Clips Kitty job exceeded 35 minutes; no upload was attempted')


def youtube():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    credentials = Credentials.from_authorized_user_file(str(ROOT / 'token.json'))
    yt = build('youtube','v3',credentials=credentials, cache_discovery=False)
    identity = yt.channels().list(part='id',mine=True).execute().get('items', [])
    expected = read(ROOT / 'channel.json', {})['channel_id']
    if len(identity) != 1 or identity[0]['id'] != expected:
        raise RuntimeError('Upload blocked: connected YouTube channel is not Shotsnip')
    return yt


def publish(video, metadata, source, state):
    from googleapiclient.http import MediaFileUpload
    if not source_allowed(source):
        raise RuntimeError('Game schedule switched during processing; old-game upload refused')
    if not read(ROOT/'scheduler.json',{}).get('enabled'):
        raise RuntimeError('Automatic posting was paused during processing')
    if any(row['status'] in ('pending','uncertain') for row in state.get('uploads',[])):
        raise RuntimeError('Unresolved upload; publishing held to prevent duplicates')
    config=read(ROOT/'scheduler.json',{})
    if time.time()-state.get('last_upload_at',0)<max(0,config.get('post_minutes',120)-config.get('early_publish_minutes',0))*60:
        raise RuntimeError('Upload window has not opened')
    digest=hashlib.sha256(video.read_bytes()).hexdigest()
    if any(row.get('sha256')==digest and row['status']=='uploaded' for row in state.get('uploads',[])):
        raise RuntimeError('This exact finished clip has already been uploaded')
    yt = youtube()  # Verify before creating any remote video.
    progress(state,'uploading','Uploading the finished clip to verified Shotsnip')
    operation = uuid.uuid4().hex
    marker = 'Shotsnip capture ' + operation
    record = dict(operation=operation, marker=marker, status='pending', started_at=now(),
        file=str(video), sha256=digest, source=source)
    state.setdefault('uploads', []).append(record)
    save(WORK/'state.json', state)
    try:
        result = yt.videos().insert(part='snippet,status', body={
            'snippet':{'title':metadata['title'][:100], 'description':metadata['description']+'\n\n'+marker,
                'categoryId':'20', 'tags':['GTA' if source['game']=='gta' else 'GTA6' if source['game']=='gta6' else 'GTA5','Shorts','Shotsnip']},
            'status':{'privacyStatus':'public','selfDeclaredMadeForKids':False}},
            media_body=MediaFileUpload(str(video),mimetype='video/mp4',resumable=True)).execute(num_retries=0)
    except Exception:
        record['status']='uncertain'
        save(WORK/'state.json', state)
        raise RuntimeError('Upload response uncertain. Publishing held to prevent duplicates; reconcile channel uploads.') from None
    record.update(status='uploaded',video_id=result['id'],finished_at=now(),
        requested_privacy='public',returned_privacy=result.get('status',{}).get('privacyStatus'))
    state['last_upload_at']=time.time()
    save(WORK/'state.json', state)
    latest=read(ROOT/'scheduler.json',{})
    if latest.get('pending_scan_minutes'):
        latest['scan_minutes']=latest.pop('pending_scan_minutes')
        if latest.get('pending_post_minutes'):
            latest['post_minutes']=latest.pop('pending_post_minutes')
            latest['early_publish_minutes']=0
        latest['mode']='scan_capture_upload'
        save(ROOT/'scheduler.json',latest)
    metadata.update(uploaded=True,uploaded_video_id=result['id'],published_at=now())
    save(video.parent/'metadata.json', metadata)
    progress(state,'published','Clip published; waiting for the next posting window')
    return record


def publish_ready(state, config):
    queue=state.get('ready_clips',[])
    keep=[]
    for item in queue:
        if any(row.get('status')=='uploaded' and Path(row.get('file','')).resolve()==Path(item['file']).resolve()
               for row in state.get('uploads',[])):
            continue
        if source_allowed(item['source']) and datetime.fromtimestamp(item['ready_at'],IST).date()==datetime.fromtimestamp(time.time(),IST).date() and Path(item['file']).is_file():
            keep.append(item)
    state['ready_clips']=keep
    save(WORK/'state.json',state)
    if not keep or time.time()-state.get('last_upload_at',0)<max(0,config.get('post_minutes',120)-config.get('early_publish_minutes',0))*60:
        return None
    item=keep[0]; video=Path(item['file'])
    result=publish(video,read(video.parent/'metadata.json',{}),item['source'],state)
    state['ready_clips']=keep[1:]
    save(WORK/'state.json',state)
    return result


def tick(scan_only=False):
    WORK.mkdir(parents=True,exist_ok=True)
    with (WORK/'worker.lock').open('a+b') as lock:
        if lock.tell()==0:
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            return {'status':'already_running'}
        state=read(WORK/'state.json',{'uploads':[]})
        try:
            config=read(ROOT/'scheduler.json',{})
            from clip_efficiency import cleanup
            save(ROOT/'work/dashboard/cleanup.json',cleanup(ROOT,config.get('raw_retention_days',3),'D:/Clips-Kitty'))
            if not config.get('enabled',False):
                progress(state,'paused','Automatic posting is paused')
                return {'status':'disabled'}
            if any(row['status'] in ('pending','uncertain') for row in state['uploads']):
                raise RuntimeError('Unresolved upload in ledger; publishing held to prevent duplicates')
            combined=config.get('mode')=='scan_capture_upload'
            if combined:
                base=max(state.get('last_cycle_at',0),state.get('last_upload_at',0))
                if time.time()-base<config.get('scan_minutes',90)*60:
                    return {'status':'cycle_not_due'}
                state['last_cycle_at']=time.time(); save(WORK/'state.json',state)
            if not scan_only and not combined:
                publish_ready(state,config)
            scan_due=combined or time.time()-state.get('last_scan_at',0)>=config.get('scan_minutes',30)*60 or state.get('game',active_game())!=active_game()
            if not scan_due and (scan_only or not state.get('selected') or len(state.get('ready_clips',[]))>=config.get('max_ready_clips',2)):
                return {'status':'scan_not_due'}
            state.pop('error',None)
            chosen=state.get('selected')
            if scan_due:
                progress(state,'scanning','Checking target channels and public live streams')
                rows,errors=scan()
                chosen=choose_varied(rows,state,config.get('streamer_cooldown_hours',6))
                state.update(last_scan_at=time.time(),checked_at=now(),game=active_game(),
                             streams=rows,discovery_errors=errors,selected=chosen,status='scanned')
                save(WORK/'state.json',state)
            chosen=choose_varied(state.get('streams',[]),state,config.get('streamer_cooldown_hours',6))
            state['selected']=chosen
            save(WORK/'state.json',state)
            if scan_only or not chosen:
                if combined and not scan_only:
                    publish_ready(state,config)
                progress(state,'waiting','No fresh streamer available; recent creators are on cooldown' if not chosen else 'Stream ranking refreshed')
                return {'status':'scanned','selected':chosen}
            if not combined and len(state.get('ready_clips',[]))>=config.get('max_ready_clips',2):
                progress(state,'queued','Ready-clip queue is full; waiting for the posting window')
                return {'status':'queue_full'}
            # An unavailable editor must not waste capture or post an arbitrary interval.
            ensure_kitty()
            api('/health/preflight')
            folder=WORK/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+chosen['id'])
            folder.mkdir()
            save(folder/'source.json',chosen)
            state.setdefault('capture_history',[]).append({'at':time.time(),'source':chosen})
            state['capture_history']=state['capture_history'][-100:]
            progress(state,'capturing',f"Capturing {config.get('capture_seconds',180)} seconds from {chosen['channel']}")
            video=capture(chosen,folder,config.get('capture_seconds',180))
            progress(state,'analyzing','Clips Kitty is finding and scoring highlights')
            selected=kitty_select(chosen,video,state)
            if not selected:
                if combined:
                    publish_ready(state,config)
                state['status']='no_quality_clip'; save(WORK/'state.json',state)
                progress(state,'waiting','No highlight passed the quality check; retrying at a later scan')
                return {'status':'no_quality_clip'}
            # Use Kitty's selected timing on ORIGINAL full-frame media, keeping our template.
            end=float(selected['end_s'])
            spikes=[event for event in read(folder/'chat-signals.json',{}).get('spikes',[])
                    if 40 <= event['capture_offset'] <= config.get('capture_seconds',180)-5]
            if spikes:
                peak=max(spikes,key=lambda event:event['unique_chatters']/max(1,event['baseline']))
                end=peak['capture_offset']-5
            start=max(0,end-30)
            if end-start<29:
                raise RuntimeError('Selected moment cannot supply a full 30-second clip')
            raw=folder/'highlight.mp4'
            progress(state,'finishing','Cutting the highlight and adding hook, captions and source credit')
            subprocess.run([ffmpeg(),'-v','error','-y','-ss',str(start),'-i',str(video),'-t','30',
                '-c:v','libx264','-preset','veryfast','-c:a','aac',str(raw)],check=True,timeout=120)
            from finish_video import finish
            cached=None
            video_id=str(selected.get('video_id',''))
            import re
            if re.fullmatch(r'[A-Za-z0-9_-]+',video_id):
                transcript=read(Path('D:/Clips-Kitty/data/transcripts')/(video_id+'.json'),{})
                from clip_efficiency import slice_transcript
                sliced=slice_transcript(transcript,start,end)
                if sliced['segments']:
                    cached=folder/'cached-transcript.json'; save(cached,sliced)
            hook=usable_hook({} if spikes else selected,chosen)
            if spikes:
                hook=chosen['channel']+' | GTA live reaction'
            finished=finish(raw,hook,chosen['channel'],transcript_file=cached,source_url=chosen['url'],
                category='GTA / LIVE MOMENT' if chosen['game']=='gta' else 'GTA VI / LIVE MOMENT' if chosen['game']=='gta6' else 'GTA V / LIVE MOMENT')
            if not source_allowed(chosen):
                raise RuntimeError('Schedule changed during editing; old-game clip will not be uploaded')
            metadata=read(finished.parent/'metadata.json',{})
            save(folder/'selection.json',selected)
            if combined:
                return publish(finished,metadata,chosen,state)
            state.setdefault('ready_clips',[]).append({'file':str(finished),'source':chosen,
                'title':metadata['title'],'ready_at':time.time()})
            state['status']='queued'; state.pop('error',None)
            progress(state,'queued','Finished clip ready; waiting for its posting window')
            result=publish_ready(state,config)
            return result or {'status':'queued','ready_clips':len(state['ready_clips'])}
        except Exception as exc:
            state.update(status='error',error=str(exc),failed_at=now())
            progress(state,'error',str(exc))
            save(WORK/'state.json',state)
            raise
        finally:
            lock.seek(0); msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tick',action='store_true')
    parser.add_argument('--scan-only',action='store_true')
    args=parser.parse_args()
    print(json.dumps(tick(args.scan_only),ensure_ascii=True))
