"""Visual fingerprints, so the same footage is never posted twice even when another channel re-uploaded it.

Viral animal clips are re-uploaded by dozens of channels, each with its own video ID, trim, crop and text on top.
Checking the YouTube video ID alone let Shotsnip post the giant green cat statue clip twice on 26 and 27 Nov 2026
(once from "Shaka and Family Official", once from "Crazy Kozak"). So every clip also gets a fingerprint: a 64-bit
difference hash of the middle of each frame, two frames a second. Two clips are the same footage when enough of
the new clip's frames have a near-identical frame in an old one, which survives trims, re-encodes, rescaling and
captions at the top or bottom.

Fingerprints live in work/animals/fingerprints.json ({video_id: {"hashes": [...], "channel": ..., "at": ...}}).
"""
import json
import subprocess
import time
from pathlib import Path

FPS = 2
MAX_FRAMES = 120          # 60 s at 2 fps
NEAR = 10                 # max differing bits (of 64) for two frames to count as the same picture
MIN_MATCHES = 3           # at least this many matching frames...
MIN_SHARE = 0.25          # ...and at least this share of the new clip's frames
# The middle of the picture: skips the caption bars and logos re-uploaders put at the top and bottom.
CENTER = 'crop=iw*0.7:ih*0.6'
# A Shotsnip render puts a 9:16 source at 800x1420 from (140, 280); used to fingerprint old renders.
RENDER_BOX = 'crop=800:1420:140:280'


def _ffmpeg():
    from live_clipper import ffmpeg
    return ffmpeg()


def fingerprint(path, rendered=False, ffmpeg=None):
    """Hex dHashes of the clip's frames; blank and flat frames are left out because they match anything."""
    chain = ([RENDER_BOX] if rendered else []) + [f'fps={FPS}', CENTER, 'scale=9:8:flags=area', 'format=gray']
    r = subprocess.run([ffmpeg or _ffmpeg(), '-v', 'error', '-i', str(path), '-vf', ','.join(chain),
                        '-frames:v', str(MAX_FRAMES), '-f', 'rawvideo', '-'], capture_output=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError('Fingerprint failed: ' + r.stderr.decode(errors='replace')[-300:])
    data, out = r.stdout, []
    for n in range(0, len(data) - 71, 72):
        px = data[n:n + 72]
        if max(px) - min(px) < 12:  # near-uniform frame (fade, black, white)
            continue
        bits = 0
        for row in range(8):
            for col in range(8):
                bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
        if 4 <= bin(bits).count('1') <= 60:
            out.append(f'{bits:016x}')
    return out


def _ints(hashes):
    return [int(h, 16) for h in hashes]


def matches(new, old):
    """How many of `new`'s frames have a near-identical frame in `old`."""
    old = _ints(old)
    if not old:
        return 0
    return sum(1 for a in _ints(new) if min(bin(a ^ b).count('1') for b in old) <= NEAR)


def same_footage(new, old):
    if not new or not old:
        return False
    hits = matches(new, old)
    return hits >= MIN_MATCHES and hits >= MIN_SHARE * len(new)


class Library:
    """Fingerprints of every clip Shotsnip has used, stored next to the animal work folders."""

    def __init__(self, path):
        self.path = Path(path)
        try:
            self.items = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.items = {}

    def duplicate_of(self, hashes, skip=None):
        """The video ID of an earlier clip with the same footage, or None."""
        for vid, row in self.items.items():
            if vid != skip and same_footage(hashes, row.get('hashes') or []):
                return vid
        return None

    def add(self, video_id, hashes, **extra):
        if not hashes:
            return
        self.items[video_id] = {'hashes': hashes, 'at': time.time(), **extra}
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + '.tmp')
        temp.write_text(json.dumps(self.items), encoding='utf-8')
        temp.replace(self.path)


def backfill(library, work, only=None, ffmpeg=None):
    """Fingerprint clips made before fingerprints existed, from what is still on disk in work/animals.

    Folders are named <timestamp>-<video id>; the downloaded source is best, the finished render works too.
    `only` limits it to the video IDs that were actually posted."""
    added = 0
    for folder in sorted(Path(work).glob('*-*')):
        if not folder.is_dir():
            continue
        video_id = folder.name.split('-', 2)[-1]
        if video_id in library.items or (only is not None and video_id not in only):
            continue
        sources = sorted(folder.glob('source.*'))
        target, rendered = (sources[0], False) if sources else (folder / 'short.mp4', True)
        if not target.is_file():
            continue
        try:
            hashes = fingerprint(target, rendered=rendered, ffmpeg=ffmpeg)
        except Exception:
            continue
        if hashes:
            library.items[video_id] = {'hashes': hashes, 'at': target.stat().st_mtime, 'backfilled': True}
            added += 1
    if added:
        library.save()
    return added
