"""Bounded recovery for the editor's known hardware-decoder failure.

Runs the same multimodal scorer on the existing capture and cached transcript,
with software decoding. It never downloads media or uploads anything.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

INSTALL = Path('D:/Clips-Kitty')
from gta_moments import FOCUS


def decoder_failure(error):
    text = str(error or '').lower()
    return 'visual decode failed' in text and any(marker in text for marker in
        ('direct3d', 'device creation failed', 'dxva2', 'd3d11', 'cuda', 'nvdec'))


def recover(video, video_id, heartbeat=None, timeout=1200):
    if not re.fullmatch(r'local_[a-f0-9]+', str(video_id)):
        raise RuntimeError('Decoder recovery needs a verified local capture ID')
    output = video.parent/'cpu-selection.json'
    output.unlink(missing_ok=True)
    command = [str(INSTALL/'.venv/Scripts/python.exe'), '-u', str(Path(__file__).resolve()),
               '--video', str(video.resolve()), '--video-id', video_id, '--output', str(output.resolve())]
    with (video.parent/'cpu-recovery.log').open('w', encoding='utf-8') as log:
        child = subprocess.Popen(command, cwd=INSTALL, stdout=log, stderr=log,
            env={**os.environ, 'PYTHONUTF8':'1'}, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline = time.monotonic()+timeout
            while child.poll() is None:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Software decoder recovery exceeded 20 minutes; no upload attempted')
                if heartbeat: heartbeat()
                time.sleep(5)
            if child.returncode or not output.is_file():
                raise RuntimeError('Software decoder recovery failed; inspect private cpu-recovery.log')
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
    return json.loads(output.read_text(encoding='utf-8')).get('selected')


def score_cpu(video, video_id, output):
    import sys
    sys.path.insert(0, str(INSTALL))
    from video import encoding
    # All FFmpeg consumers of this helper now avoid the unavailable Windows
    # graphics device. This affects this short-lived recovery process only.
    encoding.hwaccel_input_args = lambda: ['-hwaccel', 'none']
    from main import load_config
    from core.models import Segment
    from core.pipeline import clip_direction, _with_usable_model
    from analysis.fusion import find_clips
    from llm.registry import create_backend
    config = load_config(INSTALL/'config/shotsnip.yaml')
    config['clips'].update(min_score=55, max_clips_per_video=1, focus=FOCUS, gaming_scoring=True)
    transcript = json.loads((INSTALL/'data/transcripts'/(video_id+'.json')).read_text(encoding='utf-8'))
    segments = [Segment(start=s['start'], end=s['end'], text=s['text'], words=s.get('words'))
                for s in (transcript.get('segments') or [])]
    if not segments:
        raise RuntimeError('No cached transcript available for decoder recovery')
    llm = create_backend(_with_usable_model(config['llm']))
    intent = clip_direction(config, llm, max(s.end for s in segments))
    from analysis.gaming import profile_for
    source_path=video.parent/'source.json'
    source=json.loads(source_path.read_text(encoding='utf-8')) if source_path.is_file() else {}
    profile=profile_for(config,title=source.get('title','GTA gameplay'))
    candidates, _ = find_clips(video, segments, llm, config, intent=intent,
                              gaming=profile,measure_reaction=False)
    candidates = [c for c in candidates if c.score >= 55 and c.end > c.start]
    selected = None
    if candidates:
        best = max(candidates, key=lambda c:c.score)
        selected = {'start_s':best.start, 'end_s':best.end, 'score':best.score,
                    'title':best.hook, 'video_id':video_id, 'recovery':'software_decode'}
    output.write_text(json.dumps({'selected':selected}, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--video-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'local_[a-f0-9]+', args.video_id):
        parser.error('Invalid local capture ID')
    score_cpu(args.video, args.video_id, args.output)
