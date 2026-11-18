"""Bounded YouTube live capture, chat spike detection, and local Shorts.

No uploads are performed by this command. See docs/live-clipping.md.
"""
import argparse
from collections import deque
import csv
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
import threading
import time
from game_schedule import active_game, search_query, title_matches

ROOT = Path(__file__).resolve().parent


def ffmpeg():
    if shutil.which('ffmpeg'):
        return shutil.which('ffmpeg')
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


class SpikeDetector:
    """Non-overlapping bins, unique chatter counts, and preceding-bin baseline."""
    def __init__(self, window=5, baseline=12, ratio=3, minimum=8, cooldown=90):
        self.window, self.baseline = window, baseline
        self.ratio, self.minimum, self.cooldown = ratio, minimum, cooldown
        self.history = deque(maxlen=baseline)
        self.bucket = None
        self.users = set()
        self.last_trigger = -math.inf

    def advance(self, timestamp, author=None):
        bucket = int(timestamp // self.window)
        if self.bucket is None:
            self.bucket = bucket
        if bucket < self.bucket:
            return []
        events = []
        while self.bucket < bucket:
            count = len(self.users)
            baseline = statistics.median(self.history) if self.history else 0
            at = (self.bucket + 1) * self.window
            spike = (len(self.history) == self.baseline and
                     count >= self.minimum and count >= max(1, baseline) * self.ratio and
                     at - self.last_trigger >= self.cooldown)
            events.append({'at': at, 'unique_chatters': count,
                           'baseline': baseline, 'spike': spike})
            if spike:
                self.last_trigger = at
            self.history.append(count)
            self.users.clear()
            self.bucket += 1
        if author:
            self.users.add(author)
        return events


def resolve(url):
    import yt_dlp
    with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True,
                          'socket_timeout': 20, 'retries': 2, 'source_address': '0.0.0.0',
                          'format': ('best[height<=720]/best' if 'twitch.tv/' in url else
                                     'bestvideo[height<=720]+bestaudio/best[height<=720]')}) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get('live_status') != 'is_live':
        raise ValueError('Source is not currently live')
    return info


def discover(query=None, limit=8):
    import yt_dlp
    from urllib.parse import quote
    with yt_dlp.YoutubeDL({'quiet': True, 'extract_flat': True,
                          'playlistend': limit, 'socket_timeout': 20}) as ydl:
        info = ydl.extract_info('https://www.youtube.com/results?search_query=' +
                               quote(query or search_query()) + '&sp=EgJAAQ%253D%253D', download=False)
    return [{'id': x['id'], 'title': x.get('title'), 'channel': x.get('channel'),
             'url': x.get('url'), 'live_status': x.get('live_status')}
            for x in info.get('entries', []) if x and x.get('live_status') == 'is_live'
            and title_matches(x.get('title'), active_game())]


def completed_segments(folder):
    listing = folder / 'segments.csv'
    if not listing.exists():
        return []
    result = []
    for row in csv.reader(listing.read_text(encoding='utf-8').splitlines()):
        if len(row) != 3:
            continue
        try:
            path = folder / Path(row[0]).name
            start, end = float(row[1]), float(row[2])
            if path.exists() and end > start:
                result.append((path, start, end))
        except ValueError:
            continue
    return result


def select_segments(segments, end, duration):
    start = end - duration
    if start < 0:
        raise ValueError('Not enough buffered video yet')
    selected = [s for s in segments if s[2] > start and s[1] < end]
    if not selected or selected[0][1] > start + .15 or selected[-1][2] < end - .15:
        raise ValueError('Requested interval is outside the completed buffer')
    if any(b[1] - a[2] > .15 for a, b in zip(selected, selected[1:])):
        raise ValueError('Gap in video buffer')
    return selected, start - selected[0][1]


def render(folder, end, duration, label):
    selected, offset = select_segments(completed_segments(folder), end, duration)
    output = folder / f'clip-{label}-{int(end)}.mp4'
    concat = folder / 'concat.txt'
    concat.write_text(''.join(f"file '{p.name}'\n" for p, _, _ in selected), encoding='utf-8')
    # Letterboxing preserves gameplay and facecam; no blind center crop.
    vf = 'scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1'
    subprocess.run([ffmpeg(), '-hide_banner', '-loglevel', 'error', '-y',
                    '-f', 'concat', '-safe', '0', '-i', str(concat), '-ss', str(offset),
                    '-t', str(duration), '-vf', vf, '-c:v', 'libx264', '-preset', 'veryfast',
                    '-crf', '23', '-c:a', 'aac', '-movflags', '+faststart', str(output)],
                   check=True, timeout=180)
    # Decode the entire result, not just its container header.
    checked = subprocess.run([ffmpeg(), '-v', 'error', '-xerror', '-i', str(output),
                    '-progress', 'pipe:1', '-f', 'null', '-'], check=True,
                    capture_output=True, text=True, timeout=90)
    times = [int(line.split('=', 1)[1]) / 1000000 for line in checked.stdout.splitlines()
             if line.startswith('out_time_us=') and line.split('=', 1)[1].isdigit()]
    if not times or abs(max(times) - duration) > 1:
        output.unlink(missing_ok=True)
        raise ValueError('Rendered clip does not have the requested duration')
    return output


def monitor(url, seconds=180, test_clip_after=None, duration=30, reaction_delay=5,
            buffer_seconds=180, max_clips=3, finish_clips=False, hook=None, should_stop=None):
    if seconds < duration + 10 or buffer_seconds < duration + reaction_delay + 20:
        raise ValueError('Capture/buffer too short for the requested clip')
    if test_clip_after is not None and test_clip_after < duration + reaction_delay + 10:
        raise ValueError('Test trigger must allow enough time to fill the buffer')
    info = resolve(url)
    scheduled_game = active_game()
    if not title_matches(info.get('title'), scheduled_game):
        raise ValueError(f'Source title does not unambiguously match the scheduled game: {scheduled_game}')
    folder = ROOT / 'work' / 'live' / (time.strftime('%Y%m%d-%H%M%S') + '-' + info['id'])
    folder.mkdir(parents=True)
    if shutil.disk_usage(folder).free < 2 * 1024**3:
        raise RuntimeError('At least 2 GB free is required')
    summary = {'source_url': info.get('webpage_url', url), 'source_id': info['id'],
               'channel': info.get('channel'), 'channel_id': info.get('channel_id'),
               'title': info.get('title'), 'scheduled_game': scheduled_game,
               'live_verified': True, 'clips': [],
               'chat_messages': 0, 'chat_errors': [], 'upload_performed': False,
               'reaction_delay_seconds': reaction_delay,
               'timing_note': 'Delay is relative to the latest completed captured segment; per-stream calibration still required.'}
    log_lock = threading.Lock()
    def log(event):
        with log_lock, (folder / 'events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(event) + '\n')
    stop = threading.Event()
    detector = SpikeDetector()
    triggers = deque()
    def chat_worker():
        from youtube_live_chat import YouTubeChat
        chat = None
        try:
            chat = YouTubeChat(info['id'])
            seen = set()
            while not stop.is_set():
                batch = chat.get()
                log({'type': 'chat_poll', 'received': len(batch)})
                for msg in batch:
                    if msg['id'] in seen:
                        continue
                    seen.add(msg['id'])
                    if len(seen) > 20000:
                        seen.clear()
                    stamp = msg['timestamp']
                    # Ignore initial history and delayed batches instead of inventing a spike.
                    if abs(time.time() - stamp) > 20:
                        continue
                    summary['chat_messages'] += 1
                    for event in detector.advance(stamp, msg['author']):
                        log({'type': 'chat_bin', **event})
                        if event['spike']:
                            triggers.append(('chat-spike', event))
                for event in detector.advance(time.time()):
                    log({'type': 'chat_bin', **event})
                    if event['spike']:
                        triggers.append(('chat-spike', event))
                stop.wait(chat.interval)
        except Exception as exc:
            summary['chat_errors'].append(type(exc).__name__ + ': ' + str(exc))
        finally:
            if chat:
                chat.close()
    inputs = info.get('requested_formats') or [info]
    command = [ffmpeg(), '-hide_banner', '-loglevel', 'warning', '-y']
    for source in inputs:
        headers = ''.join(f'{key}: {value}\r\n' for key, value in
                          source.get('http_headers', info.get('http_headers', {})).items())
        command += ['-rw_timeout', '20000000', '-reconnect', '1',
                    '-reconnect_streamed', '1', '-reconnect_delay_max', '5',
                    '-headers', headers, '-i', source['url']]
    if len(inputs) > 1:
        command += ['-map', '0:v:0', '-map', '1:a:0']
    else:
        command += ['-map', '0:v:0', '-map', '0:a:0?']
    command += ['-t', str(seconds), '-c', 'copy', '-f', 'segment',
                '-segment_time', '2', '-segment_list', str(folder / 'segments.csv'),
                '-segment_list_size', '200', '-reset_timestamps', '1',
                str(folder / 'segment-%06d.ts')]
    worker = threading.Thread(target=chat_worker, daemon=True)
    started = time.monotonic()
    last_progress = started
    last_segment_end = 0
    test_fired = False
    error = None
    with (folder / 'capture.log').open('w', encoding='utf-8') as capture_log:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=capture_log)
        worker.start()
        print(f'Monitoring {info["id"]}; artifacts: {folder}', flush=True)
        try:
            while process.poll() is None:
                if should_stop and should_stop():
                    raise KeyboardInterrupt('Local capture stopped from the dashboard')
                if active_game() != scheduled_game:
                    raise RuntimeError('Game schedule switched; stopping this source. Restart discovery for the new game.')
                elapsed = time.monotonic() - started
                segments = completed_segments(folder)
                if segments and segments[-1][2] > last_segment_end:
                    last_segment_end = segments[-1][2]
                    last_progress = time.monotonic()
                if elapsed > seconds + 60:
                    raise TimeoutError('Capture exceeded wall-clock deadline')
                if elapsed > 45 and not segments:
                    raise RuntimeError('Capture produced no completed segments; see capture.log')
                if segments and time.monotonic() - last_progress > 40:
                    raise RuntimeError('Live video capture stalled for 40 seconds')
                if shutil.disk_usage(folder).free < 1024**3:
                    raise RuntimeError('Capture stopped: less than 1 GB free')
                if test_clip_after and elapsed >= test_clip_after and not test_fired:
                    triggers.append(('manual-test', {'elapsed': elapsed}))
                    test_fired = True
                while triggers and len(summary['clips']) < max_clips:
                    label, event = triggers.popleft()
                    if not segments:
                        continue
                    end = segments[-1][2] - reaction_delay
                    try:
                        output = render(folder, end, duration, label)
                        record = {'path': str(output), 'trigger': label, 'event': event,
                                  'media_end': end, 'duration_requested': duration}
                        summary['clips'].append(record)
                        log({'type': 'clip', **record})
                        print(f'Clip validated: {output.name} ({label})', flush=True)
                        if finish_clips:
                            try:
                                from finish_video import finish
                                finished = finish(output, hook or f'{info.get("channel", "Streamer")} live moment',
                                    info.get('channel', 'Source creator'),
                                    category=('GTA VI / LIVE MOMENT' if scheduled_game == 'gta6' else 'GTA V / LIVE MOMENT'),
                                    source_url=info.get('webpage_url', url))
                                record['finished_path'] = str(finished)
                                log({'type': 'finished_clip', 'path': str(finished)})
                            except Exception as exc:
                                record['finishing_error'] = str(exc)
                                log({'type': 'finishing_error', 'error': str(exc)})
                    except (ValueError, subprocess.SubprocessError) as exc:
                        log({'type': 'clip_error', 'error': str(exc)})
                if len(summary['clips']) >= max_clips:
                    triggers.clear()
                if segments:
                    cutoff = segments[-1][2] - buffer_seconds
                    for path, _, end in segments:
                        if end < cutoff:
                            path.unlink(missing_ok=True)
                stop.wait(1)
            if process.returncode != 0:
                raise RuntimeError(f'Capture failed ({process.returncode}); see capture.log')
            if test_clip_after and not any(c['trigger'] == 'manual-test' for c in summary['clips']):
                raise RuntimeError('Test trigger did not produce a validated clip')
        except BaseException as exc:
            error = exc
            summary['error'] = type(exc).__name__ + ': ' + str(exc)
        finally:
            stop.set()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            worker.join(timeout=35)
            summary['elapsed_seconds'] = round(time.monotonic() - started, 2)
            (folder / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=True), flush=True)
    if error:
        raise error
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    search = sub.add_parser('discover')
    search.add_argument('--query', help='Defaults to the game scheduled for the current IST date')
    run = sub.add_parser('monitor')
    run.add_argument('--url', required=True)
    run.add_argument('--seconds', type=int, default=180)
    run.add_argument('--test-clip-after', type=int)
    run.add_argument('--reaction-delay', type=float, default=5)
    run.add_argument('--finish', action='store_true', help='Add local speech captions, title, and credit')
    run.add_argument('--hook', help='Reviewed on-screen hook; otherwise uses a neutral creator label')
    args = parser.parse_args()
    if args.command == 'discover':
        print(json.dumps(discover(args.query), indent=2, ensure_ascii=True))
    else:
        monitor(args.url, args.seconds, args.test_clip_after, reaction_delay=args.reaction_delay,
                finish_clips=args.finish, hook=args.hook)


if __name__ == '__main__':
    main()
