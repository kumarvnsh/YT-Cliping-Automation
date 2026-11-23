import unittest

import animal_shorts as a


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
    good = {'title': 'Cat discovers gravity the hard way', 'hook': 'He did NOT see that coming',
            'voice_line': 'I meant to do that. Totally meant to.', 'captions': ['confidence: 100', 'skill: 0'],
            'hashtags': ['#funnycats', 'cats']}

    def test_clean_twist_and_credit_in_description(self):
        twist = a.write_twist(SOURCE, ask=lambda _p: dict(self.good))
        self.assertEqual(twist['hashtags'], ['#funnycats', '#cats'])
        text = a.description(SOURCE, twist)
        self.assertIn('Credit: The Living Planet - https://www.youtube.com/shorts/dioC4sSuJUE', text)

    def test_copied_title_or_missing_parts_are_redrafted(self):
        drafts = iter([{**self.good, 'title': 'Funny Animal Video'}, {**self.good, 'captions': ['one']}, self.good])
        self.assertEqual(a.write_twist(SOURCE, ask=lambda _p: next(drafts))['title'], self.good['title'])
        with self.assertRaises(RuntimeError):
            a.write_twist(SOURCE, ask=lambda _p: {'title': 'funny animal video'})

    def test_overlay_has_hook_voice_line_and_captions(self):
        ass = a.overlay_ass(a.clean_twist(self.good, SOURCE), duration=8.0, voice_seconds=2.5)
        self.assertIn('HE DID NOT SEE THAT COMING', ass)
        self.assertEqual(ass.count('Dialogue:'), 4)


class Gate(unittest.TestCase):
    def test_verdicts(self):
        self.assertEqual(a.verdict(None), 'blocked')
        self.assertEqual(a.verdict({'status': {'uploadStatus': 'rejected', 'rejectionReason': 'claim'}}), 'blocked')
        self.assertEqual(a.verdict({'status': {'uploadStatus': 'processed'},
                                    'contentDetails': {'regionRestriction': {'blocked': ['US']}}}), 'blocked')
        self.assertEqual(a.verdict({'status': {'uploadStatus': 'uploaded'}}), 'waiting')
        self.assertEqual(a.verdict({'status': {'uploadStatus': 'processed'}, 'contentDetails': {}}), 'clean')


if __name__ == '__main__':
    unittest.main()
