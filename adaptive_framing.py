"""Bounded webcam detection and clean, full-canvas clip layouts."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
KITTY=Path('D:/Clips-Kitty')


def valid_camera(box):
    if not isinstance(box,(list,tuple)) or len(box)!=4:return None
    try:x,y,w,h=map(float,box)
    except (ValueError,TypeError):return None
    if not all(math.isfinite(v) for v in (x,y,w,h)):return None
    if min(x,y)<0 or min(w,h)<=0 or x+w>1 or y+h>1 or w*h>.35:return None
    return [x,y,w,h]


def detect_camera(source,creator,folder):
    output=folder/'camera-detection.json'
    output.unlink(missing_ok=True)
    python=KITTY/'.venv/Scripts/python.exe'
    if not python.is_file() or not (KITTY/'yolov8n-pose.pt').is_file():
        return {'camera':None,'status':'unavailable','reason':'Local camera detector is not installed'}
    try:
        with (folder/'camera-detection.log').open('w',encoding='utf-8') as log:
            subprocess.run([str(python),str(Path(__file__).resolve()),'--source',str(source),
                '--creator',creator,'--output',str(output)],cwd=KITTY,stdout=log,stderr=log,
                check=True,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        result=json.loads(output.read_text(encoding='utf-8'))
        result['camera']=valid_camera(result.get('camera'))
        return result
    except (subprocess.SubprocessError,OSError,ValueError):
        return {'camera':None,'status':'unavailable','reason':'Camera probe failed or timed out; using footage only'}


def even(value):return max(0,int(round(value))//2*2)


def video_graph(width,height,content,camera,template,presentation=None):
    """Return a render graph plus the exact framing recorded in metadata."""
    outw,outh=template['width'],template['height']
    cw,ch,cx,cy=content
    camera=valid_camera(camera)
    details={'mode':'gameplay_only','camera':camera,'content_crop':list(content)}
    if camera:
        x,y,w,h=camera
        camw,camh=max(2,even(w*width)),max(2,even(h*height))
        camx=min(even(x*width) if x else 0,width-camw)
        camy=min(even(y*height) if y else 0,height-camh)
        if presentation=='small_corner':
            factor=min(outw*.26/camw,outh*.20/camh,float(template.get('max_camera_upscale',4)))
            fw,fh=max(2,even(camw*factor)),max(2,even(camh*factor))
            graph=(f'[0:v]split=2[c][g];'
                f'[g]crop={cw}:{ch}:{cx}:{cy},scale={outw}:{outh}:force_original_aspect_ratio=increase:flags=lanczos,'
                f'crop={outw}:{outh}[game];'
                f'[c]crop={camw}:{camh}:{camx}:{camy},scale={fw}:{fh}:flags=lanczos[cam];'
                '[game][cam]overlay=x=24:y=24[framed];'
                f'[framed]ass=overlays.ass,fps={template["fps"]},setsar=1[out]')
            details.update(mode='gameplay_with_small_camera',camera_crop=[camw,camh,camx,camy],
                camera_foreground_size=[fw,fh],camera_upscale=round(factor,3),camera_position=[24,24])
            return graph,details
        top=even(template.get('camera_height',480));bottom=outh-top
        # Crop the game's centre to the pane aspect ratio, without stretching.
        gw,gh=cw,ch
        if gw/gh>outw/bottom:gw=even(gh*outw/bottom)
        else:gh=even(gw*bottom/outw)
        gx=even(cx+(cw-gw)/2);gy=even(cy+(ch-gh)/2)
        limit=float(template.get('max_camera_upscale',4))
        scale=min(outw/camw,top/camh,limit)
        fw,fh=max(2,even(camw*scale)),max(2,even(camh*scale))
        graph=(f'[0:v]split=2[c][g];'
            f'[c]crop={camw}:{camh}:{camx}:{camy},split=2[cb][cf];'
            f'[cb]scale={outw}:{top}:force_original_aspect_ratio=increase:flags=lanczos,'
            f'crop={outw}:{top},boxblur=18:2,eq=brightness=-0.18[cback];'
            f'[cf]scale={fw}:{fh}:flags=lanczos[cfront];'
            '[cback][cfront]overlay=x=(W-w)/2:y=(H-h)/2[cam];'
            f'[g]crop={gw}:{gh}:{gx}:{gy},scale={outw}:{bottom}:flags=lanczos[game];'
            '[cam][game]vstack=inputs=2[framed];')
        details.update(mode='camera_top_gameplay_bottom',camera_crop=[camw,camh,camx,camy],
            gameplay_crop=[gw,gh,gx,gy],camera_height=top,camera_fit='contain',
            camera_foreground_size=[fw,fh],camera_upscale=round(scale,3))
    else:
        graph=(f'[0:v]crop={cw}:{ch}:{cx}:{cy},scale={outw}:{outh}:force_original_aspect_ratio=increase:flags=lanczos,'
            f'crop={outw}:{outh}[framed];')
    graph+=f'[framed]ass=overlays.ass,fps={template["fps"]},setsar=1[out]'
    return graph,details


def camera_sample_health(camera_samples,scene_samples,sharpness,native_size):
    import numpy as np
    if len(camera_samples)<5:
        return {'usable':False,'reason':'Insufficient webcam samples'}
    camera_delta=[float(np.abs(b.astype(float)-a.astype(float)).mean()) for a,b in zip(camera_samples,camera_samples[1:])]
    scene_delta=[float(np.abs(b.astype(float)-a.astype(float)).mean()) for a,b in zip(scene_samples,scene_samples[1:])]
    run=longest=0
    for c,g in zip(camera_delta,scene_delta):
        run=run+1 if c<0.12 and g>1.0 else 0
        longest=max(longest,run)
    score=float(np.median(sharpness)) if sharpness else 0
    reason=('Source webcam appears frozen while gameplay moves' if longest>=3 else
            'Source webcam is too small for a clear top pane' if min(native_size)<80 else
            'Source webcam is too soft for a clear top pane' if score<10 else '')
    return {'usable':not reason,'reason':reason,'longest_frozen_seconds':longest,
            'median_native_sharpness':round(score,2),'native_size':list(native_size),
            'sample_count':len(camera_samples)}


def checked_camera(source,box,**details):
    if not box:return {'camera':None,'status':'not_detected',**details}
    import cv2
    from video.capture import video_capture
    samples=[];scenes=[];sharpness=[];native=(0,0)
    with video_capture(source,required=False) as cap:
        if cap is None:return {'camera':None,'status':'unavailable','reason':'Cannot inspect webcam motion'}
        fps=cap.get(cv2.CAP_PROP_FPS) or 30
        duration=cap.get(cv2.CAP_PROP_FRAME_COUNT)/fps
        for sec in range(min(60,int(duration))):
            cap.set(cv2.CAP_PROP_POS_MSEC,(sec+.5)*1000)
            ok,frame=cap.read()
            if not ok:continue
            h,w=frame.shape[:2];x,y,bw,bh=box
            roi=frame[int(y*h):int((y+bh)*h),int(x*w):int((x+bw)*w)]
            if not roi.size:continue
            native=(roi.shape[1],roi.shape[0])
            # Ignore the overlay border when detecting stuck frames.
            ih,iw=roi.shape[:2];inner=roi[max(0,ih//10):ih-ih//10,max(0,iw//10):iw-iw//10]
            gray=cv2.cvtColor(inner,cv2.COLOR_BGR2GRAY)
            sharpness.append(float(cv2.Laplacian(gray,cv2.CV_64F).var()))
            samples.append(cv2.resize(gray,(128,96)))
            # Gameplay motion is checked away from the webcam overlay.
            scene=frame[:,w//3:]
            scenes.append(cv2.resize(cv2.cvtColor(scene,cv2.COLOR_BGR2GRAY),(128,96)))
    health=camera_sample_health(samples,scenes,sharpness,native)
    return {'camera':box,'presentation':'top_split' if health['usable'] else 'small_corner',
            'status':'detected' if health['usable'] else 'detected_quality_limited',
            'quality':health,**details}


def probe(source,creator):
    sys.path.insert(0,str(KITTY))
    from gaming import detect
    from video.capture import video_capture
    import cv2
    profiles=json.loads((ROOT/'templates/streamer-framing.json').read_text(encoding='utf-8'))
    key=''.join(c for c in creator.casefold() if c.isalnum())
    profile=next((v for k,v in profiles.items() if ''.join(c for c in k.casefold() if c.isalnum())==key),{})
    box=valid_camera(profile.get('camera'))
    if box:
        found=sampled=0
        with video_capture(source,required=False) as cap:
            if cap is not None:
                total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                model=detect._get_model('yolov8n-pose.pt')
                for i in range(6):
                    cap.set(cv2.CAP_PROP_POS_FRAMES,int((i+.5)*total/6))
                    ok,frame=cap.read()
                    if not ok:continue
                    fh,fw=frame.shape[:2];x,y,w,h=box
                    crop=frame[int(y*fh):int((y+h)*fh),int(x*fw):int((x+w)*fw)]
                    if not crop.size:continue
                    sampled+=1
                    if any(d[5] is not None for d in detect._detect(model,crop,.45)):found+=1
        if sampled>=4 and found>=max(4,sampled-2):
            return checked_camera(source,box,method='validated_creator_profile',present_frames=found,sampled_frames=sampled)
    box=valid_camera(detect.clip_cam(source,n=6))
    return checked_camera(source,box,method='steady_bordered_overlay')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--creator',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.write_text(json.dumps(probe(args.source,args.creator),indent=2),encoding='utf-8')
