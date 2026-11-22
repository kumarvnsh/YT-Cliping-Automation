"""Run one dashboard job under the same lock as hourly clipping."""
import hashlib
import json
import msvcrt
from pathlib import Path
import subprocess
import sys
import time
import uuid
import yt_dlp
import manual_jobs as jobs
import scheduled_clips as w
from media_safety import audio_bounds,safe_window


class Cancelled(Exception): pass


def inspect(url):
    with yt_dlp.YoutubeDL({'quiet':True,'no_warnings':True,'socket_timeout':20,'retries':1,
        'format':w.capture_format(url,w.read(w.ROOT/'scheduler.json',{}).get('capture_max_height',1080))}) as y:
        info=y.extract_info(url,download=False)
    return info


def inspect_logged(url,folder):
    try:
        return inspect(url)
    except Exception as exc:
        # Private local diagnostics; public job status never exposes signed URLs.
        with (folder/'source-inspection.log').open('a',encoding='utf-8') as log:
            log.write(type(exc).__name__+': '+str(exc)+'\n')
        raise


def report_failure(state,identity,message,publishing):
    state['last_manual_failure']={'job_id':identity,'at':w.now(),'error':message}
    # A failed preview/source request must not label the independent timed
    # worker as blocked. Keep uncertain publication outcomes visibly blocked.
    held=any(row.get('status') in ('pending','uncertain','facebook_pending','facebook_uncertain')
             for row in state.get('uploads',[]))
    if publishing or held:
        w.progress(state,'error',message)
    else:
        state.pop('error',None)
        w.progress(state,'waiting','Manual request failed; details in its job card. Automatic clipping remains scheduled.')


def exact_download(source,info,folder,window):
    start,end=window
    if info.get('duration') and info.get('live_status')!='is_live' and end>info['duration']:
        raise ValueError('End timestamp is beyond the replay duration')
    command=[sys.executable,'-m','yt_dlp','--no-playlist','--quiet','--no-warnings',
        '--socket-timeout','20','--retries','1','--download-sections',f'*{start}-{end}',
        '--force-keyframes-at-cuts','--merge-output-format','mp4','--remux-video','mp4',
        '--ffmpeg-location','D:/Clips-Kitty/vendor/ffmpeg','-f',
        w.capture_format(source['url'],w.read(w.ROOT/'scheduler.json',{}).get('capture_max_height',1080)),
        '-o',str(folder/'original.%(ext)s')]
    if info.get('live_status')=='is_live': command+=['--live-from-start']
    command.append(source['url'])
    try:
        with (folder/'download.log').open('w',encoding='utf-8') as log:
            subprocess.run(command,stdout=log,stderr=log,check=True,timeout=600)
    except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
        raise RuntimeError('Requested interval could not be downloaded. Live sources need DVR; try again when the replay is available.') from None
    output=folder/'original.mp4'
    if not output.is_file(): raise RuntimeError('No readable MP4 was produced')
    if not safe_window(audio_bounds(output),[end-start],duration=end-start):
        raise RuntimeError('Requested interval does not contain complete audio')
    return output


def publish_result(job,result,state):
    options=job['options']; video=Path(result['file']); metadata=w.read(video.parent/'metadata.json',{})
    from clip_titles import upload_title
    from local_library_batch import interval
    if interval(job) is None:
        metadata['title']=upload_title(result['source'],metadata.get('title') or metadata.get('hook'),metadata.get('creator'))
    w.save(video.parent/'metadata.json',metadata)
    destinations=options['platforms']
    if 'youtube' in destinations:
        record=w.publish(video,metadata,result['source'],state,destinations=destinations,
            privacy=options['privacy'],manual=True,run_now=interval(job) is not None)
        return {'youtube_id':record['video_id'],'youtube_privacy':record.get('returned_privacy'),
                'facebook':record.get('facebook')}
    import facebook_shotsnip as fb
    if fb._cfg().get('page_id')!='1435307842999791' or not fb.enabled():
        raise RuntimeError('Shotsnip Facebook Page is not connected')
    digest=hashlib.sha256(video.read_bytes()).hexdigest()
    for previous in state.get('uploads',[]):
        if previous.get('sha256')==digest and previous.get('facebook',{}).get('status')=='published':
            return {'facebook':previous['facebook']}
        if previous.get('sha256')==digest and previous.get('status') in ('facebook_pending','facebook_uncertain'):
            raise RuntimeError('Facebook outcome is uncertain; reconcile before retrying')
    record={'operation':uuid.uuid4().hex,'status':'facebook_pending','sha256':digest,
        'file':str(video),'source':result['source'],'started_at':w.now()}
    state.setdefault('uploads',[]).append(record); w.save(w.WORK/'state.json',state)
    fb.crosspost(record,video,metadata['title'],metadata['description'],w.ROOT)
    record['status']='facebook_published' if record.get('facebook',{}).get('status')=='published' else 'facebook_uncertain'
    if record['status']=='facebook_published': state['last_upload_at']=time.time()
    w.save(w.WORK/'state.json',state)
    return {'facebook':record.get('facebook')}


def defer_publication(job,state):
    # Retrying Facebook for an already-published YouTube clip is delivery
    # recovery, not a new clip competing with the posting window.
    if job['result'].get('publication',{}).get('youtube_id') and job['options']['platforms']==['facebook']:
        return False
    config=w.read(w.ROOT/'scheduler.json',{})
    eligible=max(state.get('last_upload_at',0)+jobs.publish_gap_minutes(job,config)*60,job['options'].get('not_before') or 0)
    if time.time()>=eligible:return False
    jobs.update(job['id'],status='queued_publish',stage='queued',result=job['result'],
        note='Finished clip queued for the next posting window')
    w.progress(state,'queued','Manual clip ready; waiting for the shared posting window')
    return True


def process(job):
    identity=job['id']; options=job['options']; state=w.read(w.WORK/'state.json',{'uploads':[]})
    folder=w.ROOT/'work/manual'/identity; folder.mkdir(exist_ok=True)
    def stage(name,note):
        if jobs.get(identity)['cancel']: raise Cancelled()
        jobs.update(identity,stage=name,note=note)
        state['manual_job_id']=identity
        state['studio_job_id']=identity
        w.progress(state,name,note)
    result=job['result']
    from local_library_batch import interval,prepare
    if interval(job) is not None and not result.get('prepared'):
        result=prepare(job,stage)
        job['result']=result
        jobs.update(identity,result=result)
    if job.get('publishing_only'):
        if defer_publication(job,state):return
        stage('uploading','Publishing the reviewed manual clip')
        jobs.update(identity,status='publishing')
        result.setdefault('publication',{}).update(publish_result(job,result,state))
        partial='facebook' in options['platforms'] and (result['publication'].get('facebook') or {}).get('status')!='published'
        jobs.update(identity,status='partial' if partial else 'published',stage='partial' if partial else 'published',result=result,note='YouTube completed; Facebook needs attention' if partial else 'Publication completed')
        w.progress(state,'published','Manual clip published; posting timer restarted')
        return
    stage('scanning','Preparing manual clip source')
    if options['mode']=='auto':
        rows,_=w.scan()
        for attempt in range(3):
            source=w.choose_varied(rows,state)
            if not source:
                from archive_streams import discover_recent
                rows=discover_recent(hours=w.read(w.ROOT/'scheduler.json',{}).get('replay_lookback_hours',8))
                source=w.choose_varied(rows,state)
            if not source: raise RuntimeError('No eligible live stream or replay from the last eight hours is available')
            source={**source,'manual':True}
            state['selected']=source
            jobs.update(identity,result={'source':source})
            w.save(folder/'source.json',source)
            try:
                info=inspect_logged(source['url'],folder)
                break
            except yt_dlp.utils.DownloadError:
                state.setdefault('capture_history',[]).append({'at':time.time(),'source':source})
                state['capture_history']=state['capture_history'][-100:]
                w.save(w.WORK/'state.json',state)
                if attempt==2: raise RuntimeError('Three automatic sources were unavailable; see the private source-inspection.log') from None
    else:
        info=inspect_logged(options['url'],folder)
        live=info.get('live_status')=='is_live'
        if options['mode']=='live' and not live: raise ValueError('This source is not live; choose replay or exact timestamps')
        if options['mode']=='replay' and live: raise ValueError('This source is live; choose live mode or an exact DVR interval')
        source={'id':info['id'],'platform':'twitch' if 'twitch.tv/' in options['url'] else 'youtube',
            'url':options['url'],'title':info.get('title','Manual source'),
            'channel':info.get('channel') or info.get('uploader') or 'Source creator',
            'game':'custom','manual':True,'targeted':False,'archive':not live,
            'viewers':info.get('concurrent_view_count') or info.get('view_count')}
    state['selected']=source
    result={'source':source}; jobs.update(identity,result=result)
    w.save(folder/'source.json',source)
    cached=None
    if options['mode']=='auto' and w.read(w.ROOT/'scheduler.json',{}).get('require_gta_moment_review'):
        from gta_moments import preflight
        preflight()
    if options['mode']=='exact':
        stage('capturing','Downloading the requested timestamp interval')
        raw=exact_download(source,info,folder,options['window'])
    else:
        stage('capturing',f"Recording a {options['capture_seconds']}-second window from {source['channel']}")
        video=w.capture(source,folder,options['capture_seconds'])
        stage('analyzing','Finding the best highlight with Clips Kitty')
        w.ensure_kitty(); selected=w.kitty_select(source,video,state)
        if not selected: raise RuntimeError('No highlight passed the quality threshold')
        window=safe_window(audio_bounds(video),[float(selected['end_s'])])
        if not window: raise RuntimeError('The selected highlight has no complete audio interval')
        start,end=window; raw=folder/'highlight.mp4'
        subprocess.run([w.ffmpeg(),'-v','error','-y','-ss',str(start),'-i',str(video),'-t','30',
            '-map','0:v:0','-map','0:a:0','-c:v','libx264','-preset','veryfast','-c:a','aac',str(raw)],check=True,timeout=120)
        video_id=str(selected.get('video_id',''))
        import re
        if re.fullmatch(r'[A-Za-z0-9_-]+',video_id):
            from clip_efficiency import slice_transcript
            transcript=w.read(Path('D:/Clips-Kitty/data/transcripts')/(video_id+'.json'),{})
            sliced=slice_transcript(transcript,start,end)
            if sliced['segments'] and options['language'] in ('auto',sliced['language']):
                cached=folder/'cached-transcript.json'; w.save(cached,sliced)
        w.save(folder/'selection.json',selected)
    moment_review=None
    if options['mode']=='auto' and w.read(w.ROOT/'scheduler.json',{}).get('require_gta_moment_review'):
        from gta_moments import review
        stage('analyzing','Checking the selected GTA action or funny in-game moment')
        moment_review=review(cached,video=raw)
        w.save(folder/'gta-moment-review.json',moment_review)
        if not moment_review['accepted']:raise RuntimeError(moment_review['reason'])
    stage('finishing','Adding the hook, speech captions and source credit')
    from finish_video import finish
    finished=finish(raw,(options['hook'] or (moment_review or {}).get('hook') or source['channel']+' | Stream highlight')[:85],source['channel'],
        transcript_file=cached,language=None if options['language']=='auto' else options['language'],
        allow_no_speech=bool(moment_review and moment_review.get('accepted') is True),
        source_url=source['url'],category='GAMING / STREAM HIGHLIGHT')
    if moment_review:
        metadata=w.read(finished.parent/'metadata.json',{})
        metadata['gta_moment_review']=moment_review
        w.save(finished.parent/'metadata.json',metadata)
    w.record_finished(finished, 'shotsnip', identity)
    result.update(file=str(finished),title=w.read(finished.parent/'metadata.json',{})['title'])
    jobs.update(identity,result=result)
    if options['publish']:
        job['result']=result
        if defer_publication(job,state):return
        stage('uploading','Publishing to the selected destinations')
        jobs.update(identity,status='publishing')
        result.setdefault('publication',{}).update(publish_result(job,result,state))
        partial='facebook' in options['platforms'] and (result['publication'].get('facebook') or {}).get('status')!='published'
        jobs.update(identity,status='partial' if partial else 'published',stage='partial' if partial else 'published',result=result,note='Publication needs attention on Facebook' if partial else 'Publication completed')
        w.progress(state,'published','Manual clip published; posting timer restarted')
    else:
        jobs.update(identity,status='ready',stage='ready',result=result,note='Preview ready; waiting for your publish command')
        w.progress(state,'ready','Manual preview ready; automatic schedule remains active')


def run():
    w.WORK.mkdir(parents=True,exist_ok=True)
    with (w.WORK/'worker.lock').open('a+b') as lock:
        if lock.tell()==0: lock.write(b'0'); lock.flush()
        lock.seek(0)
        try: msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError: return
        job=None
        try:
            jobs.recover_interrupted()
            job=jobs.claim()
            if job: process(job)
        except Cancelled:
            jobs.update(job['id'],status='cancelled',stage='cancelled',note='Stopped after the last completed stage')
            state=w.read(w.WORK/'state.json',{}); w.progress(state,'waiting','Manual job cancelled')
        except Exception as exc:
            if job:
                current=jobs.get(job['id']); publishing=current['status']=='publishing'
                message=str(exc) if isinstance(exc,(RuntimeError,ValueError)) else type(exc).__name__+'; inspect the local job log'
                jobs.update(job['id'],status='attention' if publishing else 'failed',stage='attention' if publishing else 'failed',
                    error=message,note='Publishing needs review' if publishing else 'Processing failed; retry or edit the request')
                state=w.read(w.WORK/'state.json',{}); report_failure(state,job['id'],message,publishing)
        finally:
            from local_library_batch import resume_if_complete
            resume_if_complete()
            lock.seek(0); msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)


if __name__=='__main__': run()
