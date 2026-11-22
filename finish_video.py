"""Turn a captured clip into a captioned vertical Short, entirely locally."""
import argparse
from collections import Counter
from functools import lru_cache
import json
from pathlib import Path
import re
import subprocess
import textwrap
import time

from live_clipper import ffmpeg

ROOT = Path(__file__).resolve().parent


def load_template():
    return json.loads((ROOT / 'templates' / 'gaming-short-v1.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def load_model(model_name):
    from faster_whisper import WhisperModel
    return WhisperModel(model_name, device='cpu', compute_type='int8',
                        cpu_threads=6, download_root=str(ROOT / 'work' / 'models'))


def transcribe(source, model_name='small', language=None):
    model = load_model(model_name)
    segments, info = model.transcribe(str(source), beam_size=5, word_timestamps=True,
                                     vad_filter=True, condition_on_previous_text=False,
                                     language=language)
    source_language = info.language
    translated = source_language not in ('hi', 'en')
    if translated:
        # Language detection occurs before lazy segment decoding. Translate only
        # the selected clip, retaining Whisper's timing for the English words.
        segments, info = model.transcribe(str(source), beam_size=5, word_timestamps=True,
                                         vad_filter=True, condition_on_previous_text=False,
                                         language=source_language, task='translate')
    rows = []
    for segment in segments:
        rows.append({'start': segment.start, 'end': segment.end, 'text': segment.text,
                     'words': [{'start': w.start, 'end': w.end, 'word': w.word,
                                'probability': w.probability} for w in segment.words or []]})
    return {'language': 'en' if translated else source_language,
            'source_language': source_language, 'translated': translated,
            'language_probability': info.language_probability,
            'duration': info.duration, 'segments': rows,
            'model': model_name, 'review_status': 'automatic_transcript_needs_review'}


def caption_transcript(source, model='small', language=None, transcript_file=None):
    if transcript_file:
        cached = json.loads(Path(transcript_file).read_text(encoding='utf-8'))
        if cached.get('language') in ('hi', 'en') and (not language or language == cached.get('source_language', cached['language'])):
            cached.setdefault('source_language', cached['language'])
            cached.setdefault('translated', False)
            return cached, True
    return transcribe(source, model, language), False


def ass_escape(text):
    # Untrusted speech/title text must never inject ASS override commands.
    return str(text).replace('\\', '＼').replace('{', '｛').replace('}', '｝').replace('\n', r'\N').replace('\r', '')


def stamp(seconds, srt=False):
    ticks = max(0, round(seconds * (1000 if srt else 100)))
    base = 1000 if srt else 100
    whole, fraction = divmod(ticks, base)
    minutes, second = divmod(whole, 60)
    hours, minute = divmod(minutes, 60)
    if srt:
        return f'{hours:02}:{minute:02}:{second:02},{fraction:03}'
    return f'{hours}:{minute:02}:{second:02}.{fraction:02}'


def caption_groups(transcript):
    groups, group = [], []
    for segment in transcript['segments']:
        words = []
        pending = ''
        for original in segment.get('words') or []:
            word = dict(original)
            if word['end'] <= word['start']:
                pending += word['word']
                continue
            if pending:
                word['word'] = pending + word['word']
                pending = ''
            words.append(word)
        if pending and words:
            words[-1]['word'] += pending
        # Never fabricate word timing when the recognizer did not return it.
        if not words and segment.get('text', '').strip():
            if group:
                groups.append(group)
                group = []
            groups.append([{'start': segment['start'], 'end': segment['end'],
                            'word': segment['text'].strip()}])
            continue
        for word in words:
            if not word['word'].strip() or word['end'] <= word['start']:
                continue
            if group and (len(group) >= 4 or word['start'] - group[-1]['end'] > .5 or
                          len(' '.join(w['word'].strip() for w in group + [word])) > 30 or
                          word['end'] - group[0]['start'] > 2):
                groups.append(group)
                group = []
            group.append(word)
        if group and re.search(r'[.!?]$', group[-1]['word'].strip()):
            groups.append(group)
            group = []
    if group:
        groups.append(group)
    return groups


def build_subtitles(transcript, hook, creator, category='GTA V / ROLEPLAY'):
    template = load_template()
    duration = transcript['duration']
    title = '\n'.join(textwrap.wrap(hook.upper(), width=template['hook_wrap_characters'], break_long_words=False))
    if not template.get('captions_only') and (len(title.splitlines()) > 3 or len(hook) > 90):
        raise ValueError('Use a hook of at most 90 characters / three short lines')
    header = (ROOT / 'templates' / template['styles_file']).read_text(encoding='utf-8')
    events = []
    def event(start, end, style, text):
        end = min(duration, end)
        if end > start:
            events.append(f'Dialogue: 0,{stamp(start)},{stamp(end)},{style},,0,0,0,,{text}')
    def position(name):
        x, y = template['positions'][name]
        return '{' + f'\\pos({x},{y})' + '}'
    if not template.get('captions_only'):
        event(0, duration, 'Kicker', position('kicker') + ass_escape(category.upper()))
        event(0, duration, 'Hook', position('hook') + r'{\fad(120,0)}' + ass_escape(title))
        event(0, duration, 'CTA', position('cta') + ass_escape(template['cta']))
        event(0, duration, 'Credit', position('credit') + ass_escape(f'SOURCE / {creator.upper()}'))
    srt = []
    groups = caption_groups(transcript)
    for number, group in enumerate(groups, 1):
        start, end = group[0]['start'], min(duration, group[-1]['end'])
        if start >= duration or end <= start:
            continue
        plain = ' '.join(w['word'].strip() for w in group)
        srt.append(f'{number}\n{stamp(start, True)} --> {stamp(end, True)}\n{plain}\n')
        for i, word in enumerate(group):
            # Small pauses remain pauses: do not hold captions across long silences.
            until = min(end, group[i+1]['start']) if i+1 < len(group) else end
            if until <= word['start']:
                continue
            parts = []
            for j, item in enumerate(group):
                text = ass_escape(item['word'].strip().upper())
                parts.append((r'{\c&H0000E8FF&}' + text + r'{\c&H00FFFFFF&}') if j == i else text)
            event(word['start'], until, 'Caption', position('caption') + ' '.join(parts))
    return header + '\n'.join(events) + '\n', '\n'.join(srt), len(groups)


def detect_content(source):
    import av
    with av.open(str(source)) as container:
        stream = container.streams.video[0]
        width, height = stream.width, stream.height
    result = subprocess.run([ffmpeg(), '-hide_banner', '-i', str(source), '-t', '12',
        '-vf', 'fps=1,cropdetect=limit=20:round=2:reset=0', '-an', '-f', 'null', '-'],
        capture_output=True, text=True, check=True, timeout=60)
    crops = [tuple(map(int, m)) for m in re.findall(r'crop=(\d+):(\d+):(\d+):(\d+)', result.stderr)]
    # Only remove letterboxing, never let a dark scene cause a tight subject crop.
    valid = [c for c in crops if c[0] >= width * .95 and c[1] >= height * .25 and
             c[2] + c[0] <= width and c[3] + c[1] <= height]
    return Counter(valid).most_common(1)[0][0] if valid else (width, height, 0, 0)


def check_caption_evidence(transcript,source,allow_no_speech=False):
    if transcript.get('segments'):return
    if not allow_no_speech:
        raise ValueError('No speech detected; no captions were invented')
    checked=subprocess.run([ffmpeg(),'-hide_banner','-nostats','-i',str(source),'-vn',
        '-af','volumedetect','-f','null','-'],capture_output=True,text=True,
        encoding='utf-8',errors='replace',timeout=30)
    peak=re.search(r'max_volume:\s*(-?\d+(?:\.\d+)?) dB',checked.stderr)
    if checked.returncode or not peak or float(peak.group(1))<=-60:
        raise ValueError('No audible source sound; skipping the uncaptioned action clip')
    transcript['segments']=[]
    transcript['review_status']='speech_not_recognized_no_generated_captions'


def finish(source, hook, creator, output_dir=None, transcript_file=None,
           model='small', language=None, category='GTA V / ROLEPLAY', source_url=None,allow_no_speech=False):
    total_started = time.perf_counter()
    timings = {}
    template = load_template()
    source = Path(source).resolve()
    output_dir = Path(output_dir).resolve() if output_dir else source.parent / (source.stem + '-finished')
    if output_dir == source.parent:
        raise ValueError('Use a separate output folder')
    output_dir.mkdir(parents=True, exist_ok=True)
    print('Preparing speech captions...', flush=True)
    stage_started = time.perf_counter()
    transcript, used_cache = caption_transcript(source, model, language, transcript_file)
    timings['transcription_seconds'] = round(time.perf_counter() - stage_started, 2)
    stage_started = time.perf_counter()
    import av
    with av.open(str(source)) as container:
        source_width=container.streams.video[0].width
        source_height=container.streams.video[0].height
        if container.duration:
            transcript['duration'] = container.duration / av.time_base
    check_caption_evidence(transcript,source,allow_no_speech)
    (output_dir / 'transcript.json').write_text(json.dumps(transcript, indent=2, ensure_ascii=False), encoding='utf-8')
    ass, srt, count = build_subtitles(transcript, hook, creator, category)
    (output_dir / 'overlays.ass').write_text(ass, encoding='utf-8-sig')
    (output_dir / 'captions.srt').write_text(srt, encoding='utf-8')
    crop = detect_content(source)
    width, height, x, y = crop
    framing=None
    if template.get('layout')=='clean_split':
        from adaptive_framing import detect_camera,video_graph
        detection=detect_camera(source,creator,output_dir)
        graph,framing=video_graph(source_width,source_height,crop,detection.get('camera'),template,
            presentation=detection.get('presentation'))
        framing['detection']=detection
    timings['layout_and_crop_seconds'] = round(time.perf_counter() - stage_started, 2)
    box, accent, background = template['gameplay_box'], template['accent'], template['background']
    # Foreground retains the complete gameplay; a dark blurred copy fills the canvas.
    legacy_graph = (f'[0:v]crop={width}:{height}:{x}:{y},split=2[bg][fg];'
             '[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,'
             f'boxblur=18:2,scale={template["width"]}:{template["height"]},'
             f'eq=brightness={background["brightness"]}:saturation={background["saturation"]},'
             f'drawbox=x=0:y=0:w=iw:h=ih:color=black@{background["darken"]}:t=fill[back];'
             f'[fg]scale={box["width"]}:{box["height"]}:force_original_aspect_ratio=decrease[front];'
             f'[back][front]overlay=x=(W-w)/2:y={box["top"]}+({box["height"]}-h)/2,'
             f'drawbox=x={accent["x"]}:y={accent["y"]}:w={accent["width"]}:h={accent["height"]}:color={accent["color"]}:t=fill,'
             f'ass=overlays.ass,fps={template["fps"]},setsar=1[out]')
    if framing is None:graph=legacy_graph
    music={'enabled':False}
    inputs=[];audio_map='0:a:0'
    if template.get('music',{}).get('enabled'):
        from music_bed import original_bed
        bed=original_bed(ROOT/'work/music')
        gain=float(template['music'].get('volume',.1))
        fade=max(0,transcript['duration']-1.5)
        graph+=(';[0:a]asplit=2[original][sidechain];'
            f'[1:a]volume={gain},afade=t=in:d=0.7,afade=t=out:st={fade}:d=1.5[bed];'
            '[bed][sidechain]sidechaincompress=threshold=0.08:ratio=3:attack=20:release=400:makeup=1[ducked];'
            '[original][ducked]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[aout]')
        inputs=['-stream_loop','-1','-i',str(bed)]
        audio_map='[aout]'
        music={'enabled':True,'track':'original-gaming-beat-v1','type':'original_instrumental',
               'volume':gain,'speech_ducking':True,'trending_track':False}
    output = output_dir / 'finished-short.mp4'
    print('Rendering finished Short...', flush=True)
    stage_started = time.perf_counter()
    subprocess.run([ffmpeg(), '-hide_banner', '-loglevel', 'error', '-y', '-hwaccel','none','-i', str(source),*inputs,
        '-filter_complex', graph, '-map', '[out]', '-map', audio_map,'-t',str(transcript['duration']),'-c:v', 'libx264',
        '-preset', 'veryfast', '-crf', '20', '-c:a', 'aac', '-b:a', '160k',
        '-movflags', '+faststart', str(output)], cwd=output_dir, check=True, timeout=300)
    timings['render_seconds'] = round(time.perf_counter() - stage_started, 2)
    stage_started = time.perf_counter()
    checked = subprocess.run([ffmpeg(), '-v', 'error', '-xerror', '-i', str(output),
        '-progress', 'pipe:1', '-f', 'null', '-'], capture_output=True, text=True, check=True, timeout=90)
    times = [int(v.split('=',1)[1])/1e6 for v in checked.stdout.splitlines()
             if v.startswith('out_time_us=') and v.split('=',1)[1].isdigit()]
    if not times or abs(max(times)-transcript['duration']) > 1:
        raise ValueError('Finished video duration check failed')
    metadata = {'title': hook[:100], 'description': f'{category}\n\nSource: {creator}\n' + (source_url or ''),
                'source_url': source_url, 'creator': creator, 'hook': hook, 'language': transcript['language'],
                'source_language': transcript.get('source_language', transcript['language']),
                'captions_translated': transcript.get('translated', False),
                'caption_groups': count, 'transcript_review': transcript.get('review_status', 'automatic_transcript_needs_review'),
                'input': str(source), 'output': str(output), 'content_crop': crop,
                'duration_seconds': max(times), 'uploaded': False,
                'template': template['name'], 'template_version': template['version'],
                'framing':framing,'music':music}
    (output_dir / 'metadata.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding='utf-8')
    for sec in (1, 10, 20):
        if sec < transcript['duration']:
            subprocess.run([ffmpeg(), '-v', 'error', '-y', '-ss', str(sec), '-i', str(output),
                            '-frames:v', '1', str(output_dir / f'preview-{sec}.jpg')], check=True, timeout=30)
    timings['validation_and_previews_seconds'] = round(time.perf_counter() - stage_started, 2)
    timings['total_seconds'] = round(time.perf_counter() - total_started, 2)
    timings['used_cached_transcript'] = used_cache
    timings['includes_capture_or_upload'] = False
    (output_dir / 'timings.json').write_text(json.dumps(timings, indent=2), encoding='utf-8')
    print(json.dumps(timings), flush=True)
    from studio_events import record_finished
    record_finished(output, 'shotsnip', source.parent.name)
    print(f'Finished: {output}', flush=True)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--hook', required=True)
    parser.add_argument('--creator', required=True)
    parser.add_argument('--output-dir')
    parser.add_argument('--transcript')
    parser.add_argument('--model', default='small')
    parser.add_argument('--language')
    parser.add_argument('--category', default='GTA V / ROLEPLAY')
    parser.add_argument('--source-url')
    args = parser.parse_args()
    finish(args.input, args.hook, args.creator, args.output_dir, args.transcript,
           args.model, args.language, args.category, args.source_url)


if __name__ == '__main__':
    main()
