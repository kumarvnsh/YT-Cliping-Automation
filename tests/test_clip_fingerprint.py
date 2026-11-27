import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import clip_fingerprint as fp

FFMPEG = shutil.which('ffmpeg')


def make(path, source, extra=()):
    subprocess.run([FFMPEG, '-v', 'error', '-y', *source, *extra, str(path)], check=True)


@unittest.skipUnless(FFMPEG, 'ffmpeg not installed')
class SameFootage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        zoom = ['-f', 'lavfi', '-i', 'mandelbrot=s=360x640:r=30:start_scale=3:end_scale=0.01,scale=720:1280', '-t', '14']
        make(cls.tmp / 'original.mp4', zoom)
        # Another channel's re-upload: trimmed, cropped, smaller, a caption bar on top.
        make(cls.tmp / 'reupload.mp4', ['-ss', '2.3', '-i', str(cls.tmp / 'original.mp4'), '-t', '9'],
             ['-vf', 'crop=iw*0.95:ih*0.95,scale=540:960,drawbox=0:0:540:150:black:fill', '-crf', '30'])
        make(cls.tmp / 'other.mp4', ['-f', 'lavfi', '-i', 'life=s=360x640:mold=10:r=30:ratio=0.5:death_color=#C83232:'
                                    'life_color=#00ff00,scale=720:1280', '-t', '10'])
        make(cls.tmp / 'other2.mp4', ['-f', 'lavfi', '-i', 'testsrc2=s=720x1280:r=30', '-t', '10'])
        # A finished Shotsnip render of the original (source in the 800x1420 box over a blurred fill).
        (cls.tmp / "run-1").mkdir()
        make(cls.tmp / "run-1" / "short.mp4",
             ['-i', str(cls.tmp / 'original.mp4')],
             ['-filter_complex', '[0:v]split[a][b];[a]scale=1080:1920,boxblur=24:2[bg];'
              '[b]scale=1080:1420:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:280'])

    def test_reupload_of_the_same_clip_matches(self):
        self.assertTrue(fp.same_footage(fp.fingerprint(self.tmp / 'reupload.mp4'),
                                        fp.fingerprint(self.tmp / 'original.mp4')))

    def test_different_clips_do_not_match(self):
        original = fp.fingerprint(self.tmp / 'original.mp4')
        for name in ('other.mp4', 'other2.mp4'):
            self.assertFalse(fp.same_footage(fp.fingerprint(self.tmp / name), original))
        self.assertFalse(fp.same_footage(fp.fingerprint(self.tmp / 'other.mp4'),
                                         fp.fingerprint(self.tmp / 'other2.mp4')))

    def test_old_renders_are_backfilled_and_catch_a_reupload(self):
        work = self.tmp / 'work'
        folder = work / '20261126-005438-Hik7FVeDbiY'
        folder.mkdir(parents=True)
        shutil.copy(self.tmp / 'run-1' / 'short.mp4', folder / 'short.mp4')
        (work / '20261126-010000-neverPosted').mkdir()
        library = fp.Library(work / 'fingerprints.json')
        self.assertEqual(fp.backfill(library, work, only={'Hik7FVeDbiY'}), 1)
        again = fp.Library(work / 'fingerprints.json')  # saved to disk
        self.assertEqual(again.duplicate_of(fp.fingerprint(self.tmp / 'reupload.mp4')), 'Hik7FVeDbiY')
        self.assertIsNone(again.duplicate_of(fp.fingerprint(self.tmp / 'other.mp4')))
        self.assertIsNone(again.duplicate_of(fp.fingerprint(self.tmp / 'reupload.mp4'), skip='Hik7FVeDbiY'))


if __name__ == '__main__':
    unittest.main()
