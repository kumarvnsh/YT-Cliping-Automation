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


def video_graph(width,height,content,camera,template):
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
        top=even(template.get('camera_height',640));bottom=outh-top
        # Crop the game's centre to the pane aspect ratio, without stretching.
        gw,gh=cw,ch
        if gw/gh>outw/bottom:gw=even(gh*outw/bottom)
        else:gh=even(gw*bottom/outw)
        gx=even(cx+(cw-gw)/2);gy=even(cy+(ch-gh)/2)
        graph=(f'[0:v]split=2[c][g];'
            f'[c]crop={camw}:{camh}:{camx}:{camy},scale={outw}:{top}:force_original_aspect_ratio=increase:flags=lanczos,'
            f'crop={outw}:{top}[cam];'
            f'[g]crop={gw}:{gh}:{gx}:{gy},scale={outw}:{bottom}:flags=lanczos[game];'
            '[cam][game]vstack=inputs=2[framed];')
        details.update(mode='camera_top_gameplay_bottom',camera_crop=[camw,camh,camx,camy],
            gameplay_crop=[gw,gh,gx,gy],camera_height=top)
    else:
        graph=(f'[0:v]crop={cw}:{ch}:{cx}:{cy},scale={outw}:{outh}:force_original_aspect_ratio=increase:flags=lanczos,'
            f'crop={outw}:{outh}[framed];')
    graph+=f'[framed]ass=overlays.ass,fps={template["fps"]},setsar=1[out]'
    return graph,details


def probe(source,creator):
    sys.path.insert(0,str(KITTY))
    from gaming import detect
    from video.capture import video_capture
    import cv2
    profiles=json.loads((ROOT/'templates/streamer-framing.json').read_text(encoding='utf-8'))
    profile=profiles.get(creator.casefold(),{})
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
            return {'camera':box,'status':'detected','method':'validated_creator_profile','present_frames':found,'sampled_frames':sampled}
    box=valid_camera(detect.clip_cam(source,n=6))
    return {'camera':box,'status':'detected' if box else 'not_detected','method':'steady_bordered_overlay'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--creator',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.write_text(json.dumps(probe(args.source,args.creator),indent=2),encoding='utf-8')
