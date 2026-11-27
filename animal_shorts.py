"""Shotsnip animal & brainrot mode: most-viewed Shorts, re-cut with Shotsnip's own twist, credited, gated.

Turned on by "niche": "animals" in scheduler.json (Studio HQ > Posting rules > Channel content).
Each scheduler tick does at most one of:
  1. a held private upload: check YouTube's verdict; publish it when clean and the posting rules allow,
     or quarantine it (kept private) when YouTube rejects or blocks it;
  2. otherwise, when a cycle is due: find the most-viewed matching Short not used yet, add the twist
     (hook text, a spoken reaction, funny captions), and upload it PRIVATE for the copyright check.
The original creator is always credited by channel name at the bottom of the description (no link).
"""
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parent
WORK = ROOT / 'work' / 'animals'
CATEGORY = '15'  # Pets & Animals
MAX_SECONDS = 59.0

DEFAULTS = {
    # Real funny animal clips only. A wide, specific list so searches reach far past the same top 50 viral clips;
    # titles stay broad anyway (SPECIFIC_ANIMALS), so naming species here only widens the pool.
    'queries': ['funny animal video', 'funny cats', 'funny dogs', 'funny animals', 'funny pets', 'animal fails',
                'funny animal moments', 'cat zoomies', 'dramatic cat', 'dog reaction funny', 'puppy being silly',
                'husky talking back', 'golden retriever funny', 'cat knocking things over', 'dog caught red handed',
                'funny parrot talking', 'screaming goat', 'funny horse', 'funny cow', 'funny duck', 'goose chasing',
                'raccoon stealing', 'funny monkey', 'capybara chill', 'otter funny', 'funny hamster',
                'funny bird', 'farm animals funny', 'cat vs cucumber', 'pets being dramatic', 'animal sneeze',
                'dog guilty face', 'cat jump fail', 'baby animals funny', 'wild animal funny moment'],
    'search_orders': ['viewCount', 'relevance'],  # alternate, so it is not always the same most-viewed few
    'min_views': 1_000_000,
    # Quality: a reupload farm's clip has views but few likes. Below this like share it is skipped (when likes show).
    'min_like_ratio': 0.01,
    # Recycled formats, not a funny moment of their own: ranking/tier-list edits and compilations cut to a Short.
    'skip_title_words': ['ranking', 'ranked', 'tier list', 'top 10', 'top 5', 'compilation', 'part 2', 'part 3',
                         'brainrot', 'minecraft', 'roblox', 'beamng', 'ai generated', 'stick figure'],
    'recent_channel_gap': 15,     # never take a creator again within their last 15 animal uploads
    'max_source_seconds': 60,
    'private_first': False,       # owner (24 Nov): upload straight to public in its slot; owner handles claims
    'ready_lead_minutes': 60,     # direct mode: start making the next clip this long before its slot
    'check_minutes': 30,          # private_first only: how long a private upload waits for YouTube's check
    'windows_days': [0, 30, 7],   # rotate: all-time, last 30 days, last 7 days (0 = all time)
    'max_channel_subscribers': 5_000_000,  # big media channels Content-ID everything; their clips only get blocked
    'skip_channels': [],
    'long_form_every': 3,         # every 3rd cycle clips the most-replayed moment of a long compilation (0 = never)
    'long_min_views': 500_000,
    'long_clip_seconds': 30,
    'vision_model': 'qwen3-vl:4b-instruct',
}


def settings(config):
    own = config.get('animal_shorts') if isinstance(config.get('animal_shorts'), dict) else {}
    return {**DEFAULTS, **own}


def parse_duration(iso):
    m = re.fullmatch(r'P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso or '')
    if not m:
        return 0
    h, mn, s = (int(g or 0) for g in m.groups())
    return h * 3600 + mn * 60 + s


# ---------- discovery ----------

def query_plan(cfg, cycle):
    """Which search runs this cycle. Every query is used once per round, in a different shuffled order each round,
    and the time window and sort order change between rounds, so results keep changing."""
    import random
    queries, windows = list(cfg['queries']), cfg['windows_days']
    orders = cfg.get('search_orders') or ['viewCount']
    rnd, slot = divmod(cycle, len(queries))
    random.Random(rnd).shuffle(queries)
    return queries[slot], windows[rnd % len(windows)], orders[(rnd + slot) % len(orders)]


def recent_channels(state, limit):
    """Channel IDs and names of the last `limit` animal uploads, so one creator does not fill the feed."""
    rows = [r.get('source') or {} for r in state.get('uploads', [])
            if r.get('status') == 'uploaded' and (r.get('source') or {}).get('game') == 'animals'][-limit:] if limit else []
    return {x for r in rows for x in (r.get('channel_id'), str(r.get('channel') or '').strip().casefold()) if x}


def score(views, likes):
    """Views matter, but a well-liked clip beats a slightly bigger one that people scrolled past."""
    import math
    ratio = (likes / views) if views and likes is not None else 0.03
    return math.log10(max(views, 1)) + 25 * min(ratio, 0.08)


def rank(items, used, own_channel, cfg, subscribers=None, long=False, avoid_channels=()):
    """Eligible Shorts (or long compilations when long=True), best first by views and like share."""
    low, high = (240, 3 * 3600) if long else (1, cfg['max_source_seconds'])
    min_views = cfg['long_min_views'] if long else cfg['min_views']
    out, subscribers = [], subscribers or {}
    skip = {str(x).casefold() for x in cfg.get('skip_channels', [])} | set(avoid_channels)
    bad_words = [] if long else [str(w).casefold() for w in cfg.get('skip_title_words', [])]
    for v in items:
        cid = v['snippet'].get('channelId')
        title = v['snippet'].get('title', '')
        if (subscribers.get(cid, 0) > cfg['max_channel_subscribers']
                or str(v['snippet'].get('channelTitle', '')).strip().casefold() in skip or cid in skip
                or any(w in title.casefold() for w in bad_words)):
            continue
        stats, status = v.get('statistics', {}), v.get('status', {})
        seconds = parse_duration(v.get('contentDetails', {}).get('duration'))
        views = int(stats.get('viewCount') or 0)
        likes = int(stats['likeCount']) if stats.get('likeCount') is not None else None
        if (v['id'] in used or v['snippet'].get('channelId') == own_channel or status.get('madeForKids')
                or not low <= seconds <= high or views < min_views):
            continue
        if likes is not None and views and likes / views < cfg.get('min_like_ratio', 0):
            continue
        out.append({'id': v['id'], 'title': title,
                    'description': v['snippet'].get('description', ''), 'tags': v['snippet'].get('tags', []),
                    'channel': v['snippet'].get('channelTitle', ''), 'channel_id': v['snippet'].get('channelId'),
                    'views': views, 'likes': likes, 'seconds': seconds, 'score': score(views, likes),
                    'long': long, 'url': (f"https://www.youtube.com/watch?v={v['id']}" if long
                                          else f"https://www.youtube.com/shorts/{v['id']}")})
    return sorted(out, key=lambda c: c['score'], reverse=True)


def discover(yt, cfg, cycle, used, own_channel, long=False, avoid_channels=()):
    query, days, order = query_plan(cfg, cycle)
    if long:
        query += ' compilation'
    params = dict(part='id', q=query, type='video', order=order,
                  videoDuration=('medium' if cycle % 2 else 'long') if long else 'short',
                  maxResults=50, safeSearch='moderate')
    if days:
        params['publishedAfter'] = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    ids = [i['id']['videoId'] for i in yt.search().list(**params).execute().get('items', [])]
    if not ids:
        return query, []
    items = yt.videos().list(part='snippet,contentDetails,statistics,status', id=','.join(ids)).execute().get('items', [])
    channels = sorted({v['snippet']['channelId'] for v in items})
    subscribers = {}
    for n in range(0, len(channels), 50):
        for ch in yt.channels().list(part='statistics', id=','.join(channels[n:n + 50])).execute().get('items', []):
            subscribers[ch['id']] = int(ch.get('statistics', {}).get('subscriberCount') or 0)
    return query, rank(items, used, own_channel, cfg, subscribers, long=long, avoid_channels=avoid_channels)


# ---------- long-form: the most-replayed moment ----------

def best_window(heatmap, duration, clip):
    """Start second of the `clip`-long window with the highest summed most-replayed value.

    The opening is skipped: everyone watches it, so it always looks "most replayed"."""
    best, score = None, -1.0
    intro = min(60.0, duration * 0.05)
    for h in heatmap or []:
        start = max(intro, min(float(h['start_time']), duration - clip))
        total = sum(x['value'] for x in heatmap if x['start_time'] < start + clip and x['end_time'] > start)
        if total > score:
            best, score = start, total
    return best


def long_window(c, cfg):
    """Pick the moment viewers replay most; None when YouTube shows no replay graph."""
    import yt_dlp
    opts = {'quiet': True, 'skip_download': True}
    if (ROOT / 'cookies.txt').exists():
        opts['cookiefile'] = str(ROOT / 'cookies.txt')
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(c['url'], download=False)
    start = best_window(info.get('heatmap'), info.get('duration') or c['seconds'], cfg['long_clip_seconds'])
    if start is None:
        return None
    start = round(start)
    return start, start + cfg['long_clip_seconds']


# ---------- twist ----------

def describe_clip(src, model, frames=4):
    """What actually happens in the clip, from a local vision model looking at a few frames ('' if unavailable)."""
    import base64
    import requests
    from commentary import media_seconds
    from live_clipper import ffmpeg
    try:
        seconds = media_seconds(src)
        images = []
        for n in range(frames):
            r = subprocess.run([ffmpeg(), '-v', 'error', '-ss', f'{seconds * (n + 0.5) / frames:.2f}', '-i', str(src),
                                '-frames:v', '1', '-vf', 'scale=384:-2', '-f', 'image2pipe', '-vcodec', 'mjpeg', '-'],
                               capture_output=True, timeout=60)
            if r.stdout:
                images.append(base64.b64encode(r.stdout).decode())
        if not images:
            return ''
        response = requests.post('http://127.0.0.1:11434/api/generate', json={
            'model': model, 'images': images, 'stream': False, 'keep_alive': '60s',
            'prompt': ('These are frames, in order, from one short funny video. In at most 3 sentences, say what '
                       'happens: which animals or characters, what they do, and the funny moment. Plain facts only.'),
            'options': {'temperature': 0.2, 'num_predict': 160, 'num_ctx': 8192}}, timeout=240)
        response.raise_for_status()
        return ' '.join(response.json().get('response', '').split())[:600]
    except Exception:
        return ''


# Shotsnip titles stay broad ("funny animal clips", "ragebaiting pets"), never naming the species or a narrow
# detail, so one title style fits the whole channel. A title naming any of these is redrafted.
SPECIFIC_ANIMALS = set('''cat kitten kitty kittens cats dog dogs puppy puppies pup pups doggo doggos pupper husky corgi
retriever labrador poodle chihuahua pug beagle shiba goat goats cow cows calf horse horses pony donkey parrot parrots
bird birds duck ducks goose geese chicken chickens rooster hen capybara capybaras monkey monkeys gorilla raccoon
raccoons hamster hamsters rabbit rabbits bunny bunnies bear bears lion lions tiger tigers fox foxes pig pigs piglet
sheep lamb frog frogs otter otters penguin penguins seal seals elephant elephants giraffe giraffes squirrel squirrels
ferret ferrets lizard lizards snake snakes owl owls hedgehog hedgehogs panda pandas deer kangaroo koala sloth crab
fish shark dolphin whale turtle tortoise alpaca llama camel zebra hippo rhino crow cockatoo mouse mice rat rats'''.split())

TITLE_STYLE = (
    '"title": "a broad, catchy scroll-stopping title, 25-60 chars, in the style of a channel that posts funny animal '
    'clips: say animals, pets, this little guy, furballs or the gang, NEVER the species or breed (no cat, dog, goat, '
    'parrot...) and no narrow details of the clip; carry it with an energy word or a punchline about the vibe '
    '(e.g. Ragebaiting pets be like 😭 / Funny animals with zero chill / Pets were NOT having it today / '
    'This little guy chose violence / Animals that woke up and chose chaos), max one emoji at the end, '
    'NOT the source title, no hashtags, and worded differently from the recent titles listed above"'
)


def twist_prompt(c, avoid=()):
    seen = f"What happens (seen in the frames): {c['seen']}\n" if c.get('seen') else ''
    recent = ('Recent Shotsnip titles (do not repeat their wording):\n' + '\n'.join(f'- {t}' for t in avoid) + '\n'
              if avoid else '')
    return (
        "You are the writer for Shotsnip, a Shorts channel that re-shares viral funny animal clips (cats, dogs, "
        "pets, farm and wild animals) with its own comedy twist.\n"
        f"{seen}Source title: {c['title']}\nSource description: {c['description'][:300]}\n"
        f"Tags: {', '.join(c['tags'][:12])}\n{recent}\n"
        "Base the hook, voice line and captions on what happens. Reply with ONLY a JSON object:\n"
        '{' + TITLE_STYLE + ',\n'
        ' "hook": "<= 7 words of on-screen text for the top of the video",\n'
        ' "voice_line": "<= 12 words, the animal\'s funny inner thoughts or a punchy reaction, spoken at the start",\n'
        ' "captions": ["2 or 3 short GENERIC lines, <= 6 words each, that fit the whole clip and never describe '
        'a specific moment, e.g. Funny animal video 😂 / The ultimate brainrot / Wait for the end... / '
        'Cats are built different; may name one or two animals that appear"],\n'
        ' "hashtags": ["3 to 5 hashtags like #funnyanimals"]}'
    )


ANIMAL_WORDS = SPECIFIC_ANIMALS | set('''animal animals pet pets creature creatures wildlife critter critters
buffalo bison bull ox yak gecko iguana chameleon cheetah leopard jaguar wolf wolves hyena meerkat lemur orangutan
chimpanzee chimp ape baboon peacock pigeon swan ostrich emu flamingo toucan eagle hawk seagull gull octopus bee
cattle moose elk reindeer boar guinea chinchilla skunk possum opossum badger beaver walrus puppy kitten insect
spider bat cub cubs calf foal goat lamb'''.split())


def shows_animals(seen):
    """False only when the vision model looked and saw no animal at all (a game, a cartoon prop, a car crash)."""
    found = {w.rstrip('s') for w in re.findall(r"[a-z]+", str(seen or '').casefold())}
    return not seen or bool(found & {w.rstrip('s') for w in ANIMAL_WORDS})


def too_specific(title):
    """The species words in a title, which keep it from being a broad Shotsnip title."""
    return sorted({w for w in re.findall(r"[a-z]+", str(title).casefold()) if w in SPECIFIC_ANIMALS})


def clean_twist(raw, c):
    def short(text, words, chars):
        text = ' '.join(str(text or '').split()[:words])
        return text if len(text) <= chars else text[:chars].rsplit(' ', 1)[0].strip()
    twist = {
        'title': short(raw.get('title'), 14, 80),
        'hook': short(raw.get('hook'), 7, 48),
        'voice_line': short(raw.get('voice_line'), 12, 90),
        'captions': [short(x, 7, 48) for x in (raw.get('captions') or []) if str(x).strip()][:3],
        'hashtags': [('#' + re.sub(r'[^A-Za-z0-9]', '', str(t))) for t in (raw.get('hashtags') or [])][:5],
    }
    if not twist['title'] or twist['title'].casefold() == c['title'].casefold():
        raise ValueError('twist title missing or copies the source title')
    named = too_specific(twist['title'])
    if named:
        raise ValueError(f"twist title names a specific animal ({', '.join(named)}); titles stay broad")
    if not twist['hook'] or not twist['voice_line'] or len(twist['captions']) < 2:
        raise ValueError('twist is missing its hook, voice line or captions')
    twist['hashtags'] = [t for t in twist['hashtags'] if len(t) > 1] or ['#funnyanimals']
    return twist


def write_twist(c, ask=None, tries=4, avoid=(), check=None):
    """check(title) returns a problem or None; a title the upload rules would refuse is redrafted here, so the
    downloaded clip is not wasted on a vague or repeated title."""
    if ask is None:
        from commentary import _ask as ask
    last = None
    for _ in range(tries):
        try:
            twist = clean_twist(ask(twist_prompt(c, avoid)), c)
            problem = check(twist['title']) if check else None
            if problem:
                raise ValueError(problem)
            return twist
        except Exception as exc:  # a weak draft is redrafted, never published
            last = exc
    raise RuntimeError(f'Could not write a usable twist: {last}')


SPACER_LINES = 14  # a column of "." lines pushes the credit below the fold on YouTube and Facebook


def description(c, twist):
    """Hook, hashtags, then the credit at the very bottom. The credit names the original channel only, no link."""
    tags = ' '.join(dict.fromkeys(['#funnyanimals'] + twist['hashtags'] + ['#shorts']))
    spacer = '\n'.join(['.'] * SPACER_LINES)
    return (f"{twist['hook']}\n\n"
            f"{tags}\n"
            f"{spacer}\n\n"
            f"Credit: {c['channel']}\n"
            f"Original video by {c['channel']}. All rights belong to the original creator; "
            "Shotsnip adds its own commentary and edit. Creators: contact us for credit changes or removal.")


# ---------- render ----------

def _ass_escape(text):
    return str(text).replace('\\', '＼').replace('{', '｛').replace('}', '｝').replace('\n', ' ')


def _stamp(seconds):
    cs = max(0, round(seconds * 100))
    s, c = divmod(cs, 100)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f'{h}:{m:02}:{s:02}.{c:02}'


def overlay_ass(twist, duration, voice_seconds):
    """Hook on top for the whole clip; captions spread over the time after the spoken reaction."""
    head = ('[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n'
            'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, '
            'Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
            'Alignment, MarginL, MarginR, MarginV, Encoding\n'
            'Style: Hook,Arial Black,72,&H0000FFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,7,2,8,50,50,50,1\n'
            'Style: Cap,Arial Black,58,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,6,2,2,50,50,40,1\n\n'
            '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n')
    lines = [f"Dialogue: 1,{_stamp(0)},{_stamp(duration)},Hook,,0,0,0,,{_ass_escape(twist['hook'].upper())}"]
    lines.append(f"Dialogue: 1,{_stamp(0)},{_stamp(voice_seconds)},Cap,,0,0,0,,{_ass_escape(twist['voice_line'])}")
    caps = twist['captions']
    start = min(voice_seconds, max(0.0, duration - 1.5 * len(caps)))
    step = max(1.2, (duration - start) / len(caps))
    for i, text in enumerate(caps):
        a, b = start + i * step, min(duration, start + (i + 1) * step)
        if b - a >= 0.6:
            lines.append(f"Dialogue: 1,{_stamp(a)},{_stamp(b)},Cap,,0,0,0,,{_ass_escape(text)}")
    return head + '\n'.join(lines) + '\n'


def has_audio(path):
    import av
    with av.open(str(path)) as media:
        return bool(media.streams.audio)


def render(src, voice, voice_seconds, twist, folder):
    """1080x1920: blurred fill behind the fitted clip, overlays, voice over ducked original audio."""
    from commentary import media_seconds
    from live_clipper import ffmpeg
    clip_seconds = min(media_seconds(src), MAX_SECONDS)
    duration = min(MAX_SECONDS, max(clip_seconds, voice_seconds + 0.6))
    hold = max(0.0, duration - clip_seconds)
    (folder / 'overlay.ass').write_text(overlay_ass(twist, duration, voice_seconds), encoding='utf-8')
    fmt = 'aformat=sample_rates=48000:channel_layouts=stereo'
    graph = ('[0:v]split[a][b];'
             '[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:2,eq=brightness=-0.12[bg];'
             '[b]scale=1080:1420:force_original_aspect_ratio=decrease[fg];'
             f'[bg][fg]overlay=(W-w)/2:280+(1420-h)/2,tpad=stop_mode=clone:stop_duration={hold:.2f},'
             'fps=30,ass=overlay.ass,setsar=1[out];'
             f'[1:a]{fmt},apad[vo];')
    if has_audio(src):
        graph += (f"[0:a]{fmt},volume='if(lt(t,{voice_seconds + 0.2:.2f}),0.22,1)':eval=frame,apad[orig];"
                  '[orig][vo]amix=inputs=2:duration=longest:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[aout]')
    else:
        graph += '[vo]loudnorm=I=-14:TP=-1.5:LRA=11[aout]'
    out = folder / 'short.mp4'
    r = subprocess.run([ffmpeg(), '-y', '-v', 'error', '-i', str(src), '-i', str(voice),
                        '-filter_complex', graph, '-map', '[out]', '-map', '[aout]', '-t', f'{duration:.2f}',
                        '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p',
                        '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', out.name],
                       cwd=folder, capture_output=True, timeout=600)
    if r.returncode != 0 or not out.is_file():
        raise RuntimeError('Render failed: ' + r.stderr.decode(errors='replace')[-800:])
    return out, duration


def download(c, folder):
    import yt_dlp
    opts = {'format': 'bv*[height<=1920][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b',
            'outtmpl': str(folder / 'source.%(ext)s'), 'merge_output_format': 'mp4', 'quiet': True,
            'noprogress': True}
    local = Path(r'D:/YT-Automation-env/bin')  # yt-dlp needs a binary literally named ffmpeg for cutting
    if (local / 'ffmpeg.exe').is_file():
        import os
        opts['ffmpeg_location'] = str(local)
        if str(local) not in os.environ.get('PATH', ''):  # yt-dlp's section downloader only looks on PATH
            os.environ['PATH'] = str(local) + os.pathsep + os.environ.get('PATH', '')
    else:
        from live_clipper import ffmpeg
        opts['ffmpeg_location'] = ffmpeg()
    if (ROOT / 'cookies.txt').exists():
        opts['cookiefile'] = str(ROOT / 'cookies.txt')
    if c.get('start') is not None:  # long-form: only the chosen moment
        from yt_dlp.utils import download_range_func
        opts['download_ranges'] = download_range_func(None, [(c['start'], c['end'])])
        opts['force_keyframes_at_cuts'] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([c['url'].split('&t=')[0]])
    files = sorted(folder.glob('source.*'))
    if not files:
        raise RuntimeError('Download produced no file')
    return files[0]


BLOCK_SIGNS = ('confirm you', 'not a bot', '429', 'too many requests', 'rate-limit')


def youtube_blocked(exc):
    text = str(exc).casefold()
    return any(sign in text for sign in BLOCK_SIGNS)


# ---------- copyright gate ----------

def verdict(item):
    """'blocked', 'clean' or 'waiting' from a videos.list item (status + contentDetails)."""
    if not item:
        return 'blocked'  # removed by YouTube
    status = item.get('status', {})
    if status.get('uploadStatus') in ('rejected', 'failed', 'deleted') or status.get('rejectionReason'):
        return 'blocked'
    if (item.get('contentDetails', {}).get('regionRestriction') or {}).get('blocked'):
        return 'blocked'
    return 'clean' if status.get('uploadStatus') == 'processed' else 'waiting'


# ---------- cycle ----------

def footage_library(state):
    """Fingerprints of every posted clip; clips posted before fingerprints existed are added from disk once."""
    import clip_fingerprint as fp
    library = fp.Library(WORK / 'fingerprints.json')
    posted = {(r.get('source') or {}).get('video') for r in state.get('uploads', [])
              if r.get('status') == 'uploaded' and (r.get('source') or {}).get('game') == 'animals'}
    held = (state.get('animal') or {}).get('held') or {}
    posted |= {(held.get('source') or {}).get('video'), ((state.get('animal') or {}).get('ready') or {}).get('source', {}).get('video')}
    posted.discard(None)
    if posted - set(library.items):
        fp.backfill(library, WORK, only=posted)
    return library


def cycle(state, config, run_now=False, yt=None):
    import clip_fingerprint as fp
    import scheduled_clips as sc
    import youtube_rules
    cfg = settings(config)
    animal = state.setdefault('animal', {'used': [], 'hashes': [], 'cycles': 0})
    held = animal.get('held')
    if held and held.get('status') in ('pending', 'uncertain'):
        raise RuntimeError('Unresolved animal upload; reconcile the channel before posting more')
    yt = yt or sc.youtube()
    own_channel = sc.read(ROOT / 'channel.json', {}).get('channel_id')

    if animal.get('ready'):  # a finished clip waits for its posting slot (direct-to-public mode)
        return publish_ready(yt, state, config, animal)

    if held:  # 1. a private upload is waiting for YouTube's copyright verdict
        item = (yt.videos().list(part='status,contentDetails', id=held['video_id']).execute().get('items') or [None])[0]
        result = verdict(item)
        if result == 'blocked':
            held.update(status='quarantined', checked_at=sc.now())
            animal.setdefault('quarantined', []).append(held)
            animal.pop('held')
            sc.progress(state, 'queued', f"YouTube blocked the {held['source']['channel']} clip; it stays private")
            return {'status': 'quarantined', 'video_id': held['video_id']}
        if result == 'waiting' or time.time() - held['uploaded_at'] < cfg['check_minutes'] * 60:
            sc.progress(state, 'checking', 'Private upload is waiting for YouTube\'s copyright check')
            return {'status': 'checking', 'video_id': held['video_id']}
        try:
            youtube_rules.check(held['source'], held['title'], state, config, ROOT)
        except youtube_rules.RuleHold as hold:
            if hold.kind == 'spacing':
                sc.progress(state, 'queued', 'Checked clip is clean; ' + str(hold))
                return {'status': 'held_clip_waiting', 'until': hold.wait_until}
            held.update(status='dropped', reason=str(hold))
            animal.setdefault('quarantined', []).append(held)
            animal.pop('held')
            sc.progress(state, 'queued', f'Clean clip dropped: {hold}')
            return {'status': 'dropped', 'reason': str(hold)}
        privacy = sc.setting(config, 'privacy')
        yt.videos().update(part='status', body={'id': held['video_id'], 'status': {
            'privacyStatus': privacy, 'selfDeclaredMadeForKids': False}}).execute()
        record = {**held, 'status': 'uploaded', 'finished_at': sc.now(), 'requested_privacy': privacy}
        state.setdefault('uploads', []).append(record)
        state['last_upload_at'] = state['last_youtube_upload_at'] = time.time()
        animal.pop('held')
        sc.save(sc.WORK / 'state.json', state)
        if privacy == 'public':  # same Facebook Page Reel cross-post as GTA clips; never reverses the YouTube publish
            crosspost_facebook(record, state)
        sc.progress(state, 'published', f"Published \"{held['title']}\" (credit: {held['source']['channel']})")
        return {'status': 'published', 'video_id': held['video_id'],
                'facebook': (record.get('facebook') or {}).get('status')}

    # 2. make the next clip, when a cycle is due
    if not run_now and time.time() - animal.get('last_cycle_at', 0) < config.get('scan_minutes', 240) * 60:
        return {'status': 'cycle_not_due'}
    wait = youtube_rules.spacing_wait(state, config)
    lead = cfg['check_minutes'] + 60 if cfg['private_first'] else cfg['ready_lead_minutes']
    if wait and not run_now and wait - time.time() > lead * 60:
        return {'status': 'cycle_not_due', 'until': wait}
    animal['last_cycle_at'] = time.time()
    number = animal['cycles']
    animal['cycles'] = number + 1
    sc.progress(state, 'scanning', 'Looking for the best funny animal Shorts not posted yet')
    every = cfg['long_form_every']
    long = bool(every) and number % every == every - 1
    library = footage_library(state)
    query, ranked = discover(yt, cfg, number, set(animal['used']), own_channel, long=long,
                             avoid_channels=recent_channels(state, cfg['recent_channel_gap']))
    errors = []
    for c in ranked[:6]:
        source = {'channel': c['channel'], 'channel_id': c['channel_id'], 'game': 'animals', 'url': c['url'],
                  'video': c['id'], 'views': c['views'], 'query': query}
        problem = youtube_rules.streamer_problem(source, state, config)
        if problem:
            errors.append(problem)
            continue
        folder = WORK / f"{datetime.now():%Y%m%d-%H%M%S}-{c['id']}"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if c['long']:
                window = long_window(c, cfg)
                if not window:
                    raise RuntimeError('no most-replayed graph on this video')
                c['start'], c['end'] = window
                c['url'] = f"{c['url']}&t={c['start']}s"
                source.update(url=c['url'], start=c['start'])
            sc.progress(state, 'capturing', f"Downloading {c['views']:,}-view Short by {c['channel']}")
            src = download(c, folder)
            hashes = fp.fingerprint(src)
            twin = library.duplicate_of(hashes, skip=c['id'])
            if twin:  # same footage re-uploaded by another channel: never post it again
                raise RuntimeError(f'same footage as {twin}, which Shotsnip already used')
            sc.progress(state, 'finishing', 'Writing the twist and recording the voice reaction')
            c['seen'] = describe_clip(src, cfg['vision_model'])
            if not shows_animals(c['seen']):
                raise RuntimeError('no animals in the clip: ' + c['seen'][:120])
            twist = write_twist(c, avoid=youtube_rules.recent_titles(state, ROOT, 8)[-8:],
                                check=lambda t: youtube_rules.title_problem(t, source, state, config, ROOT))
            from commentary import speak
            voice = (config.get('commentary') or {}).get('voice') or 'en-US-AndrewMultilingualNeural'
            voice_seconds, _ = speak(twist['voice_line'], folder / 'voice.mp3', voice)
            video, duration = render(src, folder / 'voice.mp3', voice_seconds, twist, folder)
        except Exception as exc:  # any per-candidate failure: try the next Short
            if youtube_blocked(exc):  # a temporary bot check / rate limit says nothing about this Short
                animal['blocked_at'] = time.time()
                sc.save(sc.WORK / 'state.json', state)
                sc.progress(state, 'queued', 'YouTube is rate-limiting downloads; trying again next cycle')
                return {'status': 'youtube_blocked', 'error': str(exc)[:200]}
            animal['used'].append(c['id'])
            errors.append(f"{c['id']}: {exc}"[:300])
            continue
        library.add(c['id'], hashes, channel=c['channel'])
        digest = hashlib.sha256(video.read_bytes()).hexdigest()
        meta = {'seen': c.get('seen', ''), 'title': twist['title'], 'description': description(c, twist), 'twist': twist,
                'source': source, 'duration_seconds': duration}
        sc.save(folder / 'metadata.json', meta)
        if cfg['private_first']:
            return upload_private(yt, video, meta, digest, state, animal, c)
        animal['used'].append(c['id'])
        animal['ready'] = {'file': str(video), 'sha256': digest, 'source': source, 'title': meta['title'][:100]}
        sc.save(sc.WORK / 'state.json', state)
        return publish_ready(yt, state, config, animal)
    sc.save(sc.WORK / 'state.json', state)
    sc.progress(state, 'queued', f'No usable Short for "{query}" this cycle')
    return {'status': 'no_quality_clip', 'query': query, 'errors': errors[-5:]}


def crosspost_facebook(record, state):
    """Post the published Short as a Reel on the Shotsnip Facebook Page (best effort, result kept on the record)."""
    import facebook_shotsnip as fb
    import scheduled_clips as sc
    video = Path(record['file'])
    meta = sc.read(video.parent / 'metadata.json', {})
    text = meta.get('description', '')
    if meta.get('twist') and (meta.get('source') or {}).get('channel'):
        text = description(meta['source'], meta['twist'])  # clips made before the credit change get the new layout too
    fb.crosspost(record, video, record.get('title', ''), text, ROOT)
    sc.save(sc.WORK / 'state.json', state)
    if meta:
        meta.update(uploaded=True, uploaded_video_id=record.get('video_id'), facebook=record.get('facebook'))
        sc.save(video.parent / 'metadata.json', meta)
    return record.get('facebook')


def insert(yt, video, meta, privacy):
    from googleapiclient.http import MediaFileUpload
    return yt.videos().insert(part='snippet,status', body={
        'snippet': {'title': meta['title'][:100], 'description': meta['description'], 'categoryId': CATEGORY,
                    'tags': ['funny animals', 'funny animal video', 'animals', 'Shorts', 'Shotsnip']},
        'status': {'privacyStatus': privacy, 'selfDeclaredMadeForKids': False}},
        media_body=MediaFileUpload(str(video), mimetype='video/mp4', resumable=True)).execute(num_retries=0)['id']


def publish_ready(yt, state, config, animal):
    """Direct mode: upload the finished clip with the owner's privacy (public) once the posting rules allow."""
    import scheduled_clips as sc
    import youtube_rules
    ready = animal['ready']
    video = Path(ready['file'])
    if not video.is_file():
        animal.pop('ready')
        sc.save(sc.WORK / 'state.json', state)
        return {'status': 'dropped', 'reason': 'finished clip file is missing'}
    try:
        youtube_rules.check(ready['source'], ready['title'], state, config, ROOT)
    except youtube_rules.RuleHold as hold:
        if hold.kind == 'spacing':
            sc.progress(state, 'queued', 'Finished animal Short waits for its slot; ' + str(hold))
            return {'status': 'held_clip_waiting', 'until': hold.wait_until}
        animal.pop('ready')
        sc.progress(state, 'queued', f'Finished clip dropped: {hold}')
        return {'status': 'dropped', 'reason': str(hold)}
    meta = sc.read(video.parent / 'metadata.json', {})
    privacy = sc.setting(config, 'privacy')
    record = {'operation': uuid.uuid4().hex, 'status': 'pending', 'started_at': sc.now(), 'file': str(video),
              'sha256': ready['sha256'], 'source': ready['source'], 'title': ready['title']}
    state.setdefault('uploads', []).append(record)
    animal.pop('ready')
    sc.save(sc.WORK / 'state.json', state)
    sc.progress(state, 'uploading', f'Uploading "{ready["title"]}" ({privacy})')
    try:
        video_id = insert(yt, video, meta, privacy)
    except Exception as exc:
        from googleapiclient.errors import HttpError
        rejected = isinstance(exc, HttpError) and 400 <= exc.resp.status < 500 and exc.resp.status != 408
        record['status'] = 'rejected' if rejected else 'uncertain'
        sc.save(sc.WORK / 'state.json', state)
        if rejected:
            raise RuntimeError(f'YouTube rejected upload (HTTP {exc.resp.status})') from None
        raise RuntimeError('Upload response uncertain. Publishing held to prevent duplicates; reconcile channel uploads.') from None
    record.update(status='uploaded', video_id=video_id, finished_at=sc.now(), requested_privacy=privacy)
    state['last_upload_at'] = state['last_youtube_upload_at'] = time.time()
    sc.save(sc.WORK / 'state.json', state)
    if privacy == 'public':
        crosspost_facebook(record, state)
    sc.progress(state, 'published', f"Published \"{ready['title']}\" (credit: {ready['source']['channel']})")
    return {'status': 'published', 'video_id': video_id, 'facebook': (record.get('facebook') or {}).get('status')}


def upload_private(yt, video, meta, digest, state, animal, c):
    import scheduled_clips as sc
    from googleapiclient.http import MediaFileUpload
    held = {'operation': uuid.uuid4().hex, 'status': 'pending', 'started_at': sc.now(), 'file': str(video),
            'sha256': digest, 'source': meta['source'], 'title': meta['title'][:100]}
    animal['held'] = held
    animal['used'].append(c['id'])
    sc.save(sc.WORK / 'state.json', state)
    sc.progress(state, 'uploading', 'Uploading privately for YouTube\'s copyright check')
    try:
        result = yt.videos().insert(part='snippet,status', body={
            'snippet': {'title': meta['title'][:100], 'description': meta['description'], 'categoryId': CATEGORY,
                        'tags': ['funny animals', 'funny animal video', 'animals', 'Shorts', 'Shotsnip']},
            'status': {'privacyStatus': 'private', 'selfDeclaredMadeForKids': False}},
            media_body=MediaFileUpload(str(video), mimetype='video/mp4', resumable=True)).execute(num_retries=0)
    except Exception as exc:
        from googleapiclient.errors import HttpError
        if isinstance(exc, HttpError) and 400 <= exc.resp.status < 500 and exc.resp.status != 408:
            animal.pop('held')
            sc.save(sc.WORK / 'state.json', state)
            raise RuntimeError(f'YouTube rejected upload (HTTP {exc.resp.status})') from None
        held['status'] = 'uncertain'
        sc.save(sc.WORK / 'state.json', state)
        raise RuntimeError('Upload response uncertain; animal posting held to prevent duplicates') from None
    held.update(status='checking', video_id=result['id'], uploaded_at=time.time())
    sc.save(sc.WORK / 'state.json', state)
    sc.progress(state, 'checking', f"Uploaded privately; publishing after the copyright check (credit: {c['channel']})")
    return {'status': 'uploaded_private', 'video_id': result['id'], 'source': c['url']}
