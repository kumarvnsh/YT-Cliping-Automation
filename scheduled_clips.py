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
from live_clipper import capture_format,ffmpeg, resolve
from stream_sources import scan as scan_live, choose, choose_varied, any_gta

ROOT = Path(__file__).resolve().parent
WORK = ROOT / 'work' / 'scheduler'
KITTY = 'http://127.0.0.1:8765'


class StopRequested(Exception): pass


def scan():
    """Add recent missed target broadcasts to the regular discovery pool."""
    from archive_streams import discover_recent
    hours=read(ROOT/'scheduler.json',{}).get('replay_lookback_hours',8)
    rows,errors=scan_live()
    try:rows+=discover_recent(hours=hours,targets_only=True)
    except Exception as exc:errors.append('Recent target replays: '+type(exc).__name__)
    return rows,errors


def stop_after_stage():
    flag=WORK/'stop-after-stage'
    if flag.exists():
        flag.unlink()
        raise StopRequested()


def source_allowed(source):
    return bool(source.get('manual')) or bool(source.get('targeted') and source.get('game')=='gta') or source.get('game')==active_game()


def save(path, value):
    if path.resolve() == (WORK/'state.json').resolve():
        if not isinstance(value.get('uploads'),list):
            raise RuntimeError('Refusing to replace the publishing ledger with incomplete state')
        if path.exists():
            previous=json.loads(path.read_text(encoding='utf-8'))
            if isinstance(previous.get('uploads'),list):
                save(path.with_name('state.previous.json'),previous)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
        for attempt in range(10):
            try:
                temp.replace(path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(.1 * (attempt + 1))
    finally:
        temp.unlink(missing_ok=True)


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
    if payload is None:
        for attempt in range(3):
            try:
                response=requests.get(KITTY+path,timeout=30)
                break
            except (requests.Timeout,requests.ConnectionError):
                if attempt==2: raise
                time.sleep(1)
    else:
        response=requests.post(KITTY+path,json=payload,timeout=60)
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
    height=read(ROOT/'scheduler.json',{}).get('capture_max_height',1080)
    if source.get('archive'):
        import yt_dlp
        with yt_dlp.YoutubeDL({'quiet':True,'no_warnings':True,'socket_timeout':20,
            'format':capture_format(source['url'],height)}) as y:
            info=y.extract_info(source['url'],download=False)
        twitch_vod=source['platform']=='twitch' and not info.get('is_live') and (info.get('duration') or 0)>=300
        if info.get('live_status') not in ('was_live','post_live') and not twitch_vod and not source.get('manual'):
            raise RuntimeError('Replay is no longer a completed public stream')
        from archive_streams import replay_window
        offset,signal=replay_window(info,folder,seconds)
        save(folder/'replay-signal.json',{'offset':offset,**signal})
    else:
        info = resolve(source['url'],max_height=height)
        offset=None
    if not source.get('manual') and source['platform']=='youtube' and not (any_gta(info.get('title')) if source.get('targeted') else title_matches(info.get('title'),source['game'])):
        raise RuntimeError('Live source title changed game since discovery; capture refused')
    output = folder / 'capture.mp4'
    command = [ffmpeg(), '-hide_banner', '-loglevel', 'warning', '-y']
    inputs = info.get('requested_formats') or [info]
    for item in inputs:
        headers = ''.join(f'{k}: {v}\r\n' for k, v in item.get('http_headers', info.get('http_headers', {})).items())
        command += ['-rw_timeout','20000000','-headers',headers]
        if offset is not None: command+=['-ss',str(offset)]
        command+=['-i',item['url']]
    command += ['-map','0:v:0','-map','1:a:0' if len(inputs)>1 else '0:a:0?',
                '-t',str(seconds),'-c','copy','-shortest','-movflags','+faststart',str(output)]
    from live_signals import LiveSignals
    signals=LiveSignals(source).start() if offset is None else None
    try:
        with (folder / 'capture.log').open('w', encoding='utf-8') as log:
            subprocess.run(command, stdout=subprocess.DEVNULL, stderr=log, check=True, timeout=seconds+90)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise RuntimeError('Live capture failed or timed out; inspect its private capture.log') from None
    finally:
        if signals:
            save(folder/'chat-signals.json',signals.close())
        else:
            events=[]
            if signal.get('unique_chatters'):
                events=[{'capture_offset':signal['end']-offset,'unique_chatters':signal['unique_chatters'],'baseline':1}]
            save(folder/'chat-signals.json',{'spikes':events,'method':signal['method']})
    return output


def kitty_select(source, video, state=None):
    preflight = api('/health/preflight')
    blockers = [c for c in preflight.get('checks', []) if c.get('blocking') and not c.get('ok')]
    if blockers:
        raise RuntimeError('Clips Kitty preflight: ' + '; '.join(c['name'] for c in blockers))
    from gta_moments import FOCUS
    job = api('/videos/local', {'path':str(video), 'title':source['title'],
        'channel':source['channel'], 'max_clips':1, 'min_score':55, 'captions':False,
        'focus':FOCUS,'gaming_scoring':source.get('game') in ('gta','gta5','gta6')})
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
            from kitty_recovery import decoder_failure, recover
            if decoder_failure(result.get('error')):
                queue = api('/queue')
                if queue.get('processing') or queue.get('queued'):
                    raise RuntimeError('Software recovery deferred: editor has another active or queued job')
                if state is not None:
                    progress(state,'analyzing','Graphics decoder unavailable; recovering this recording with software decoding')
                    state['last_repair']={'at':now(),'kind':'software_decode','job_id':job_id,'status':'running'}
                def heartbeat():
                    if state is not None:
                        state['heartbeat_at']=time.time(); save(WORK/'state.json',state)
                selected = recover(video, result['video_id'], heartbeat)
                if state is not None:
                    state['last_repair']['status']='recovered' if selected else 'no_quality_clip'
                    progress(state,'analyzing','Software decoding recovered the highlight' if selected else 'Software analysis completed; no highlight passed the quality threshold')
                return selected
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


def publish(video, metadata, source, state, destinations=None, privacy='public', manual=False, run_now=False):
    from googleapiclient.http import MediaFileUpload
    if not source_allowed(source):
        raise RuntimeError('Game schedule switched during processing; old-game upload refused')
    if not manual and not read(ROOT/'scheduler.json',{}).get('enabled'):
        raise RuntimeError('Automatic posting was paused during processing')
    if any(row['status'] in ('pending','uncertain') for row in state.get('uploads',[])):
        raise RuntimeError('Unresolved upload; publishing held to prevent duplicates')
    config=read(ROOT/'scheduler.json',{})
    from gta_moments import approved
    if not manual and config.get('require_gta_moment_review') and not approved(metadata.get('gta_moment_review')):
        raise RuntimeError('Automatic clip has not passed the requested GTA moment review')
    if not run_now and time.time()-state.get('last_upload_at',0)<max(0,config.get('post_minutes',120)-config.get('early_publish_minutes',0))*60:
        raise RuntimeError('Upload window has not opened')
    digest=hashlib.sha256(video.read_bytes()).hexdigest()
    if any(row.get('sha256')==digest and row['status'] in ('uploaded','pending','uncertain','quarantined') for row in state.get('uploads',[])):
        raise RuntimeError('This exact clip is already uploaded or quarantined; choose another clip')
    yt = youtube()  # Verify before creating any remote video.
    from clip_titles import upload_title
    if not metadata.get('commentary'):
        metadata['title']=upload_title(source,metadata.get('title') or metadata.get('hook'),metadata.get('creator'))
    progress(state,'uploading','Uploading the finished clip to verified Shotsnip')
    operation = uuid.uuid4().hex
    marker = 'Shotsnip capture ' + operation
    record = dict(operation=operation, marker=marker, status='pending', started_at=now(),
        file=str(video), sha256=digest, source=source)
    tag = metadata.get('growth_experiment')
    if not manual and isinstance(tag, dict) and tag.get('id'):
        record['growth_experiment'] = dict(tag)
        record['rendered_seconds'] = metadata.get('duration_seconds')
    state.setdefault('uploads', []).append(record)
    save(WORK/'state.json', state)
    try:
        result = yt.videos().insert(part='snippet,status', body={
            'snippet':{'title':metadata['title'][:100], 'description':metadata['description']+'\n\n'+marker,
                'categoryId':'20', 'tags':['Gaming' if source.get('manual') else 'GTA' if source['game']=='gta' else 'GTA6' if source['game']=='gta6' else 'GTA5','Shorts','Shotsnip']},
            'status':{'privacyStatus':privacy,'selfDeclaredMadeForKids':False}},
            media_body=MediaFileUpload(str(video),mimetype='video/mp4',resumable=True)).execute(num_retries=0)
    except Exception as exc:
        from googleapiclient.errors import HttpError
        rejected=isinstance(exc,HttpError) and 400<=exc.resp.status<500 and exc.resp.status!=408
        record['status']='rejected' if rejected else 'uncertain'
        if rejected:
            try:
                error=json.loads(exc.content)['error']
                record['rejection_reason']=error.get('errors',[{}])[0].get('reason','http_'+str(exc.resp.status))
            except (ValueError,KeyError,IndexError):
                record['rejection_reason']='http_'+str(exc.resp.status)
        save(WORK/'state.json', state)
        if rejected:
            raise RuntimeError('YouTube rejected upload: '+record['rejection_reason']) from None
        raise RuntimeError('Upload response uncertain. Publishing held to prevent duplicates; reconcile channel uploads.') from None
    record.update(status='uploaded',video_id=result['id'],finished_at=now(),
        requested_privacy=privacy,returned_privacy=result.get('status',{}).get('privacyStatus'))
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
    import facebook_shotsnip as fb  # best-effort FB Page cross-post; never reverses the YT publish
    if destinations is None or 'facebook' in destinations:
        fb.crosspost(record, video, metadata.get('title',''), metadata.get('description',''), ROOT)
    save(WORK/'state.json', state)
    progress(state,'published','Clip published; waiting for the next posting window')
    return record


def publish_ready(state, config):
    from gta_moments import approved
    queue=state.get('ready_clips',[])
    keep=[]
    for item in queue:
        if any(row.get('status')=='uploaded' and Path(row.get('file','')).resolve()==Path(item['file']).resolve()
               for row in state.get('uploads',[])):
            continue
        reviewed=not config.get('require_gta_moment_review') or approved(read(Path(item['file']).parent/'metadata.json',{}).get('gta_moment_review'))
        if reviewed and source_allowed(item['source']) and datetime.fromtimestamp(item['ready_at'],IST).date()==datetime.fromtimestamp(time.time(),IST).date() and Path(item['file']).is_file():
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


def attempt_tick(scan_only=False,run_now=False):
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
        retry=state.pop('retry_pending',False)
        try:
            config=read(ROOT/'scheduler.json',{})
            import manual_jobs
            if manual_jobs.pending(ROOT):
                return {'status':'manual_job_queued'}
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
                if not run_now and not retry and time.time()-base<config.get('scan_minutes',90)*60 and state.get('game',active_game())==active_game():
                    return {'status':'cycle_not_due'}
                state['last_cycle_at']=time.time(); save(WORK/'state.json',state)
            if not scan_only and not combined:
                publish_ready(state,config)
            scan_due=retry or combined or time.time()-state.get('last_scan_at',0)>=config.get('scan_minutes',30)*60 or state.get('game',active_game())!=active_game()
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
            if not chosen and not scan_only:
                progress(state,'scanning','Checking other public stream replays from the last eight hours')
                from archive_streams import discover_recent
                replays=discover_recent(hours=config.get('replay_lookback_hours',8))
                state['replay_candidates']=replays
                chosen=choose_varied(replays,state,config.get('streamer_cooldown_hours',6))
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
            if config.get('require_gta_moment_review'):
                from gta_moments import preflight
                preflight()
            ensure_kitty()
            api('/health/preflight')
            folder=WORK/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+chosen['id'])
            folder.mkdir()
            save(folder/'source.json',chosen)
            state.setdefault('capture_history',[]).append({'at':time.time(),'source':chosen})
            state['capture_history']=state['capture_history'][-100:]
            progress(state,'capturing',f"Capturing {config.get('capture_seconds',180)} seconds from {chosen['channel']}")
            video=capture(chosen,folder,config.get('capture_seconds',180))
            stop_after_stage()
            progress(state,'analyzing','Clips Kitty is finding and scoring highlights')
            selected=kitty_select(chosen,video,state)
            stop_after_stage()
            if not selected:
                if combined:
                    fallback=publish_ready(state,config)
                    if fallback: return fallback
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
            from media_safety import audio_bounds,safe_window
            window=safe_window(audio_bounds(video),[end,float(selected['end_s'])])
            if not window:
                progress(state,'waiting','No selected 30-second highlight has complete audio; skipping this recording')
                return {'status':'no_audio_safe_clip'}
            start,end=window
            if end!=float(selected['end_s']) and not spikes:
                raise RuntimeError('Unexpected clip timing')
            if spikes and end==float(selected['end_s']):
                spikes=[]
            raw=folder/'highlight.mp4'
            progress(state,'finishing','Cutting the highlight and adding hook, captions and source credit')
            subprocess.run([ffmpeg(),'-v','error','-y','-ss',str(start),'-i',str(video),'-t','30',
                '-c:v','libx264','-preset','veryfast','-c:a','aac',str(raw)],check=True,timeout=120)
            import av
            has_audio=safe_window(audio_bounds(raw),[30]) is not None
            if not has_audio and spikes:
                # A chat peak can land after a stream's audio stops. Use the scored moment instead.
                end=float(selected['end_s']); start=max(0,end-30); spikes=[]
                subprocess.run([ffmpeg(),'-v','error','-y','-ss',str(start),'-i',str(video),'-t','30',
                    '-c:v','libx264','-preset','veryfast','-c:a','aac',str(raw)],check=True,timeout=120)
                with av.open(str(raw)) as media:
                    has_audio=bool(media.streams.audio)
            if not has_audio or safe_window(audio_bounds(raw),[30]) is None:
                raise RuntimeError('Selected clip has no audio; skipped before captioning')
            subprocess.run([ffmpeg(),'-v','error','-xerror','-i',str(raw),'-f','null','-'],check=True,timeout=90)
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
            from growth_experiment import assignment
            experiment=assignment(state,config.get('growth_experiment',{}).get('enabled',False))
            review_duration=30
            if experiment['id'] and experiment['source_seconds']==20:
                tight=folder/'highlight-tight.mp4'
                subprocess.run([ffmpeg(),'-v','error','-y','-ss','10','-i',str(raw),'-t','20',
                    '-c:v','libx264','-preset','veryfast','-c:a','aac',str(tight)],check=True,timeout=120)
                if safe_window(audio_bounds(tight),[20],duration=20) is None:
                    raise RuntimeError('Compact experiment clip lacks complete audio')
                raw=tight; review_duration=20
                if cached:
                    cached_data=read(cached,{})
                    cached=folder/'cached-transcript-tight.json'
                    save(cached,slice_transcript(cached_data,10,30))
            moment_review=None
            if config.get('require_gta_moment_review'):
                from gta_moments import review
                progress(state,'analyzing','Checking the footage for a clear GTA action, discovery or funny gameplay moment')
                moment_review=review(cached,video=raw,duration=review_duration)
                save(folder/'gta-moment-review.json',moment_review)
                if not moment_review['accepted']:
                    progress(state,'waiting','No clearly supported GTA gameplay peak; trying another source')
                    return {'status':'no_quality_clip','reason':moment_review['reason']}
                hook=moment_review.get('hook') or hook
            if spikes and not moment_review:
                hook=chosen['channel']+' | GTA live reaction'
            finished=finish(raw,hook,chosen['channel'],transcript_file=cached,source_url=chosen['url'],
                allow_no_speech=bool(moment_review and moment_review.get('accepted') is True),
                category='GTA / LIVE MOMENT' if chosen['game']=='gta' else 'GTA VI / LIVE MOMENT' if chosen['game']=='gta6' else 'GTA V / LIVE MOMENT')
            if not source_allowed(chosen):
                raise RuntimeError('Schedule changed during editing; old-game clip will not be uploaded')
            metadata=read(finished.parent/'metadata.json',{})
            metadata['growth_experiment']=experiment
            if moment_review:
                metadata['gta_moment_review']=moment_review
            save(finished.parent/'metadata.json',metadata)
            if config.get('commentary',{}).get('enabled'):
                # Reach now favours Shorts that add the uploader's own contribution, so
                # every automatic clip carries a narrated Shotsnip breakdown.
                from commentary import CommentaryRejected,narrate
                progress(state,'finishing','Writing and voicing the Shotsnip breakdown')
                try:
                    finished=narrate(finished,metadata,finished.parent/'transcript.json',chosen,
                        voice=config['commentary'].get('voice','en-US-AndrewMultilingualNeural'),rate=config['commentary'].get('rate','+0%'),
                        mode=config['commentary'].get('mode','intro'))
                except CommentaryRejected as exc:
                    progress(state,'waiting',str(exc))
                    return {'status':'no_quality_clip','reason':str(exc)}
                metadata=read(finished.parent/'metadata.json',{})
            save(folder/'selection.json',selected)
            stop_after_stage()
            if not read(ROOT/'scheduler.json',{}).get('enabled'):
                state.setdefault('ready_clips',[]).append({'file':str(finished),'source':chosen,'title':metadata['title'],'ready_at':time.time()})
                progress(state,'ready','Automatic publishing paused; finished clip saved in reserve')
                return {'status':'ready'}
            if combined:
                return publish(finished,metadata,chosen,state,run_now=run_now)
            state.setdefault('ready_clips',[]).append({'file':str(finished),'source':chosen,
                'title':metadata['title'],'ready_at':time.time()})
            state['status']='queued'; state.pop('error',None)
            progress(state,'queued','Finished clip ready; waiting for its posting window')
            result=publish_ready(state,config)
            return result or {'status':'queued','ready_clips':len(state['ready_clips'])}
        except StopRequested:
            progress(state,'waiting','Stopped after the completed stage; automatic clipping remains configured')
            return {'status':'cancelled'}
        except Exception as exc:
            state['failed_phase']=state.get('phase')
            state.update(status='error',error=str(exc),failed_at=now())
            progress(state,'error',str(exc))
            save(WORK/'state.json',state)
            raise
        finally:
            lock.seek(0); msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)


def tick(scan_only=False,run_now=False):
    for number in range(1,4):
        error=None
        try:
            result=attempt_tick(scan_only,run_now=True) if run_now else attempt_tick(scan_only)
            retry=not scan_only and result.get('status')=='no_quality_clip'
        except Exception as exc:
            error=exc
            state=read(WORK/'state.json',{})
            retry=state.get('failed_phase') in ('scanning','capturing','analyzing','finishing')
        state=read(WORK/'state.json',{})
        held=any(row.get('status') in ('pending','uncertain') for row in state.get('uploads',[]))
        if retry and not held and number<3 and read(ROOT/'scheduler.json',{}).get('enabled'):
            state['retry_pending']=True; state['retry_attempt']=number+1
            progress(state,'retrying',f'Clip attempt {number} failed; trying another stream now ({number+1}/3)')
            continue
        if error: raise error
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tick',action='store_true')
    parser.add_argument('--scan-only',action='store_true')
    parser.add_argument('--run-now',action='store_true',help='Explicit one-run cadence override; content, channel and duplicate guards remain active')
    args=parser.parse_args()
    print(json.dumps(tick(args.scan_only,args.run_now),ensure_ascii=True))
