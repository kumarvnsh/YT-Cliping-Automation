from pathlib import Path
import unittest

import animal_shorts as a
import youtube_rules


def video(vid, views, seconds='PT20S', channel='C1', kids=False):
    return {'id': vid, 'snippet': {'title': 't ' + vid, 'channelId': channel, 'channelTitle': 'Chan ' + vid},
            'contentDetails': {'duration': seconds}, 'statistics': {'viewCount': str(views)},
            'status': {'madeForKids': kids}}


SOURCE = {'title': 'funny animal video', 'description': '', 'tags': [], 'channel': 'The Living Planet',
          'url': 'https://www.youtube.com/shorts/dioC4sSuJUE'}


class Rank(unittest.TestCase):
    def test_most_views_first_and_filters(self):
        cfg = a.settings({})
        items = [video('a', 2_000_000), video('b', 55_000_000), video('c', 500), video('d', 9_000_000, 'PT3M'),
                 video('e', 9_000_000, channel='OWN'), video('f', 9_000_000, kids=True), video('g', 9_000_000)]
        ranked = a.rank(items, used={'g'}, own_channel='OWN', cfg=cfg)
        self.assertEqual([c['id'] for c in ranked], ['b', 'a'])
        self.assertEqual(ranked[0]['url'], 'https://www.youtube.com/shorts/b')

    def test_big_and_skipped_channels_are_left_out(self):
        cfg = {**a.settings({}), 'skip_channels': ['Chan b']}
        items = [video('a', 2_000_000, channel='BIG'), video('b', 3_000_000), video('c', 4_000_000)]
        ranked = a.rank(items, set(), 'OWN', cfg, subscribers={'BIG': 300_000_000})
        self.assertEqual([c['id'] for c in ranked], ['c'])

    def test_query_plan_rotates_queries_then_windows(self):
        cfg = {'queries': ['x', 'y'], 'windows_days': [0, 7]}
        self.assertEqual([a.query_plan(cfg, n) for n in range(4)], [('x', 0), ('y', 0), ('x', 7), ('y', 7)])


class LongForm(unittest.TestCase):
    def test_long_compilations_use_watch_urls_and_their_own_view_floor(self):
        cfg = a.settings({})
        items = [video('a', 600_000, 'PT12M'), video('b', 100_000, 'PT12M'), video('c', 9_000_000, 'PT30S')]
        ranked = a.rank(items, set(), 'OWN', cfg, long=True)
        self.assertEqual([c['id'] for c in ranked], ['a'])
        self.assertEqual(ranked[0]['url'], 'https://www.youtube.com/watch?v=a')

    def test_best_window_finds_the_most_replayed_moment(self):
        heat = [{'start_time': t, 'end_time': t + 10, 'value': 1.0 if 300 <= t < 330 else 0.1} for t in range(0, 600, 10)]
        self.assertEqual(a.best_window(heat, 600, 30), 300)
        self.assertIsNone(a.best_window([], 600, 30))


class Twist(unittest.TestCase):
    good = {'title': 'This little guy discovered gravity the hard way', 'hook': 'He did NOT see that coming',
            'voice_line': 'I meant to do that. Totally meant to.', 'captions': ['confidence: 100', 'skill: 0'],
            'hashtags': ['#funnycats', 'cats']}

    def test_clean_twist_and_credit_in_description(self):
        twist = a.write_twist(SOURCE, ask=lambda _p: dict(self.good))
        self.assertEqual(twist['hashtags'], ['#funnycats', '#cats'])
        text = a.description(SOURCE, twist)
        self.assertNotIn('http', text)
        self.assertNotIn(SOURCE['url'], text)
        lines = text.split('\n')
        self.assertEqual(lines[0], self.good['hook'])
        self.assertEqual(lines[2], '#funnyanimals #funnycats #cats #shorts')
        self.assertEqual(lines[3:3 + a.SPACER_LINES], ['.'] * a.SPACER_LINES)
        self.assertEqual(lines[-2], 'Credit: The Living Planet')
        self.assertTrue(lines[-1].startswith('Original video by The Living Planet.'))
        from public_metadata import description as clean  # the Facebook cleaner keeps the spacer column
        self.assertEqual(clean(text), text)

    def test_copied_title_or_missing_parts_are_redrafted(self):
        drafts = iter([{**self.good, 'title': 'Funny Animal Video'}, {**self.good, 'captions': ['one']}, self.good])
        self.assertEqual(a.write_twist(SOURCE, ask=lambda _p: next(drafts))['title'], self.good['title'])
        with self.assertRaises(RuntimeError):
            a.write_twist(SOURCE, ask=lambda _p: {'title': 'funny animal video'})

    def test_titles_stay_broad_and_never_name_the_species(self):
        drafts = iter([{**self.good, 'title': 'Goat headbutts the farmer 😭'},
                       {**self.good, 'title': 'Two puppies vs one sock'},
                       {**self.good, 'title': 'Ragebaiting pets be like 😭'}])
        self.assertEqual(a.write_twist(SOURCE, ask=lambda _p: next(drafts))['title'], 'Ragebaiting pets be like 😭')
        self.assertEqual(a.too_specific('My CATS and the dog'), ['cats', 'dog'])
        self.assertEqual(a.too_specific('Animals that chose chaos today'), [])
        self.assertIn('NEVER the species', a.twist_prompt(SOURCE))

    def test_recent_titles_are_shown_and_rule_refusals_are_redrafted(self):
        prompts, drafts = [], iter([{**self.good, 'title': 'Funny animal clips 😂'}, dict(self.good)])
        def ask(prompt):
            prompts.append(prompt)
            return next(drafts)
        def check(title):  # the real upload rule: broad words alone do not say anything
            return youtube_rules.title_problem(title, SOURCE, {}, {}, Path('/nonexistent'))
        twist = a.write_twist(SOURCE, ask=ask, avoid=['Pets were NOT having it today'], check=check)
        self.assertEqual(twist['title'], self.good['title'])
        self.assertIn('- Pets were NOT having it today', prompts[0])

    def test_overlay_has_hook_voice_line_and_captions(self):
        ass = a.overlay_ass(a.clean_twist(self.good, SOURCE), duration=8.0, voice_seconds=2.5)
        self.assertIn('HE DID NOT SEE THAT COMING', ass)
        self.assertEqual(ass.count('Dialogue:'), 4)


class Facebook(unittest.TestCase):
    def test_published_short_is_cross_posted_with_its_description(self):
        import json, tempfile
        from unittest.mock import patch
        import facebook_shotsnip, scheduled_clips
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'run'; folder.mkdir()
            (folder / 'short.mp4').write_bytes(b'0')
            (folder / 'metadata.json').write_text(json.dumps({'description': 'Credit: X - url'}))
            record, calls = {'file': str(folder / 'short.mp4'), 'title': 'Cat chaos', 'video_id': 'yt1'}, []
            def fake(rec, video, title, description, root):
                calls.append((title, description)); rec['facebook'] = {'status': 'published', 'reel_id': 'r1'}
            with patch.object(facebook_shotsnip, 'crosspost', fake), patch.object(scheduled_clips, 'WORK', Path(tmp)):
                result = a.crosspost_facebook(record, {'uploads': [record]})
            self.assertEqual(calls, [('Cat chaos', 'Credit: X - url')])
            self.assertEqual(result['reel_id'], 'r1')
            self.assertEqual(json.loads((folder / 'metadata.json').read_text())['facebook']['reel_id'], 'r1')


class DirectPublish(unittest.TestCase):
    def run_ready(self, hold=None):
        import json, tempfile
        from unittest.mock import patch
        import scheduled_clips, youtube_rules
        tmp = tempfile.mkdtemp()
        folder = Path(tmp) / 'run'; folder.mkdir()
        (folder / 'short.mp4').write_bytes(b'0')
        (folder / 'metadata.json').write_text(json.dumps({'title': 'Cat chaos', 'description': 'Credit: X - url'}))
        animal = {'ready': {'file': str(folder / 'short.mp4'), 'sha256': 'h', 'source': {'channel': 'X'},
                            'title': 'Cat chaos'}}
        state, posted, privacies = {'uploads': [], 'animal': animal}, [], []
        def check(*_a, **_k):
            if hold: raise hold
        def fake_insert(_yt, _video, _meta, privacy):
            privacies.append(privacy); return 'yt9'
        with patch.object(scheduled_clips, 'WORK', Path(tmp)), patch.object(scheduled_clips, 'progress', lambda *a: None),                 patch.object(youtube_rules, 'check', check), patch.object(a, 'insert', fake_insert),                 patch.object(a, 'crosspost_facebook', lambda rec, st: posted.append(rec['video_id'])):
            result = a.publish_ready(None, state, {'privacy': 'public'}, animal)
        return result, state, posted, privacies

    def test_finished_clip_goes_straight_to_public_and_facebook(self):
        result, state, posted, privacies = self.run_ready()
        self.assertEqual((result['status'], privacies, posted), ('published', ['public'], ['yt9']))
        self.assertEqual(state['uploads'][0]['status'], 'uploaded')
        self.assertNotIn('ready', state['animal'])

    def test_spacing_keeps_the_clip_waiting_without_uploading(self):
        import youtube_rules
        result, state, posted, privacies = self.run_ready(youtube_rules.RuleHold('wait', 'spacing', wait_until=5))
        self.assertEqual((result['status'], privacies, state['uploads']), ('held_clip_waiting', [], []))
        self.assertIn('ready', state['animal'])


class Gate(unittest.TestCase):
    def test_bot_checks_and_rate_limits_are_recognised(self):
        self.assertTrue(a.youtube_blocked(RuntimeError("Sign in to confirm you’re not a bot")))
        self.assertTrue(a.youtube_blocked(RuntimeError('HTTP Error 429: Too Many Requests')))
        self.assertFalse(a.youtube_blocked(RuntimeError('Render failed: bad filter')))

    def test_verdicts(self):
        self.assertEqual(a.verdict(None), 'blocked')
        self.assertEqual(a.verdict({'status': {'uploadStatus': 'rejected', 'rejectionReason': 'claim'}}), 'blocked')
        self.assertEqual(a.verdict({'status': {'uploadStatus': 'processed'},
                                    'contentDetails': {'regionRestriction': {'blocked': ['US']}}}), 'blocked')
        self.assertEqual(a.verdict({'status': {'uploadStatus': 'uploaded'}}), 'waiting')
        self.assertEqual(a.verdict({'status': {'uploadStatus': 'processed'}, 'contentDetails': {}}), 'clean')


if __name__ == '__main__':
    unittest.main()
