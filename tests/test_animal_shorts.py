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

    def test_query_plan_uses_every_query_each_round_then_changes_window_and_order(self):
        cfg = {'queries': ['x', 'y', 'z'], 'windows_days': [0, 7], 'search_orders': ['viewCount', 'relevance']}
        plans = [a.query_plan(cfg, n) for n in range(6)]
        self.assertEqual(sorted(q for q, _, _ in plans[:3]), ['x', 'y', 'z'])
        self.assertEqual(sorted(q for q, _, _ in plans[3:]), ['x', 'y', 'z'])
        self.assertEqual({d for _, d, _ in plans[:3]}, {0})
        self.assertEqual({d for _, d, _ in plans[3:]}, {7})
        self.assertEqual({o for _, _, o in plans}, {'viewCount', 'relevance'})

    def test_well_liked_clip_beats_a_bigger_one_nobody_liked(self):
        cfg = a.settings({})
        liked, farm = video('liked', 3_000_000), video('farm', 9_000_000)
        liked['statistics']['likeCount'] = '150000'   # 5 %
        farm['statistics']['likeCount'] = '120000'    # 1.3 %
        self.assertEqual([c['id'] for c in a.rank([farm, liked], set(), 'OWN', cfg)], ['liked', 'farm'])

    def test_low_like_share_and_recycled_formats_are_skipped(self):
        cfg = a.settings({})
        dead = video('dead', 9_000_000); dead['statistics']['likeCount'] = '20000'   # 0.2 %
        tier = video('tier', 9_000_000); tier['snippet']['title'] = 'Ranking Funniest Cat Prank Moments'
        hidden = video('hidden', 2_000_000)  # likes hidden: not judged on them
        self.assertEqual([c['id'] for c in a.rank([dead, tier, hidden], set(), 'OWN', cfg)], ['hidden'])

    def test_recent_creators_are_rested(self):
        cfg = a.settings({})
        state = {'uploads': [{'status': 'uploaded', 'source': {'game': 'animals', 'channel': 'Chan a',
                                                               'channel_id': 'CA'}}]}
        avoid = a.recent_channels(state, cfg['recent_channel_gap'])
        items = [video('a', 9_000_000, channel='CA'), video('b', 2_000_000, channel='CB')]
        self.assertEqual([c['id'] for c in a.rank(items, set(), 'OWN', cfg, avoid_channels=avoid)], ['b'])


class Animals(unittest.TestCase):
    def test_clip_must_show_an_animal_when_the_vision_model_saw_it(self):
        self.assertTrue(a.shows_animals('Two puppies tumble off a couch.'))
        self.assertTrue(a.shows_animals('A buffalo charges and men run.'))
        self.assertTrue(a.shows_animals(''))  # vision model unavailable: do not block
        self.assertFalse(a.shows_animals('A stick figure falls through a rainbow maze in a block game.'))

    def test_cartoon_and_ai_clips_are_refused_even_with_an_animal_in_them(self):
        # The 27 Nov Facebook post: an AI cartoon cat spilling gems passed because "cat" is an animal word.
        cartoon = {'seen': 'An orange cat crashes a bike and gems spill on a colourful street.', 'footage': 'cartoon'}
        self.assertIn('cartoon', a.real_footage(cartoon))
        self.assertIn('ai', a.real_footage({**cartoon, 'footage': 'ai'}))
        self.assertIn('animated', a.real_footage({'seen': 'An animated orange cat falls off a bike.', 'footage': ''}))
        self.assertIsNone(a.real_footage({'seen': 'Two puppies tumble off a couch.', 'footage': 'real'}))
        self.assertIsNone(a.real_footage({'seen': '', 'footage': ''}))  # vision model unavailable: do not block
        self.assertIn('no animals', a.real_footage({'seen': 'A car slides on ice.', 'footage': 'real'}))
        # 27 Nov reference: a man rings the bell on a giant grass cat; no living animal at all.
        giant = {'seen': 'A man rings a bell on the collar of a giant topiary cat statue.', 'footage': ''}
        self.assertIn('statue', a.real_footage(giant))
        self.assertIn('object', a.real_footage({**giant, 'seen': 'A man taps a huge green cat.', 'footage': 'object'}))

    def test_sources_that_call_themselves_ai_or_cartoon_are_skipped_before_download(self):
        cfg = a.settings({})
        ai = video('ai', 9_000_000); ai['snippet']['tags'] = ['funny cat', 'AI video']
        toon = video('toon', 9_000_000); toon['snippet']['description'] = 'Cute cat animation #pixar #cartoon'
        real = video('real', 2_000_000); real['snippet']['title'] = 'My cat said no (rain day)'
        self.assertEqual([c['id'] for c in a.rank([ai, toon, real], set(), 'OWN', cfg)], ['real'])
        self.assertEqual(a.synthetic_words('Cat zoomies', 'filmed on my phone', ['cats']), [])
        self.assertEqual(a.synthetic_words('He touched the bell', '', ['giant cat statue']), ['statue'])

    def test_clips_with_a_watermark_or_another_channels_name_are_refused(self):
        # Owner, 28 Nov: skip videos with a watermark or a different channel's name on them.
        clean = {'seen': 'Two puppies tumble off a couch.', 'footage': 'real', 'watermark': False, 'marks': ''}
        self.assertIsNone(a.clean_frame(clean))
        self.assertIsNone(a.clean_frame({**clean, 'marks': 'none'}))
        self.assertIsNone(a.clean_frame({'seen': '', 'footage': ''}))  # vision model unavailable: do not block
        self.assertIn('watermark', a.clean_frame({**clean, 'watermark': True, 'marks': 'TikTok'}))
        self.assertIsNotNone(a.clean_frame({**clean, 'marks': '@funnypaws.daily'}))
        self.assertIsNotNone(a.clean_frame({**clean, 'marks': 'petclips.com'}))
        self.assertIsNotNone(a.clean_frame({**clean, 'seen': 'A cat falls off a sofa while a TikTok logo floats.'}))
        # 28 Nov Facebook post: "Sorry Mom, I didn't mean to hurt Julie" was burned in by the first uploader.
        self.assertIn('caption', a.clean_frame({**clean, 'caption': True, 'marks': 'Sorry Mom, I didnt mean to'}))
        self.assertTrue(a._yes('true') and a._yes(True) and not a._yes('false') and not a._yes(None))

    def test_sources_reposted_from_tiktok_or_another_creator_are_skipped_before_download(self):
        cfg = a.settings({})
        tok = video('tok', 9_000_000); tok['snippet']['tags'] = ['funny cat', 'tiktok']
        cr = video('cr', 8_000_000); cr['snippet']['description'] = 'Credit to @kittyfails, DM for removal'
        real = video('real', 2_000_000); real['snippet']['description'] = 'My cat Milo, filmed at home'
        self.assertEqual([c['id'] for c in a.rank([tok, cr, real], set(), 'OWN', cfg)], ['real'])
        self.assertEqual(a.repost_words('Cat zoomies', 'filmed on my phone', ['cats']), [])

    def test_searches_lean_on_kids_and_pets(self):
        queries = a.settings({})['queries']
        self.assertGreaterEqual(sum(any(k in q for k in ('kid', 'baby', 'toddler')) for q in queries), 10)
        self.assertFalse(any(w in q for q in queries for w in ('wild', 'goat', 'cow', 'monkey', 'raccoon')))


class Framing(unittest.TestCase):
    def test_vertical_source_fills_the_screen_without_a_crop(self):
        self.assertEqual(a.fill_filter(1080, 1920), 'scale=1080:1920:flags=lanczos')

    def test_landscape_source_is_cropped_to_9_16_around_the_action(self):
        self.assertEqual(a.fill_filter(1920, 1080, 0.5), 'crop=606:1080:656:0,scale=1080:1920:flags=lanczos')
        self.assertEqual(a.fill_filter(1920, 1080, 0.0), 'crop=606:1080:0:0,scale=1080:1920:flags=lanczos')
        self.assertEqual(a.fill_filter(1920, 1080, 1.0), 'crop=606:1080:1314:0,scale=1080:1920:flags=lanczos')

    def test_subject_center_follows_the_motion_and_defaults_to_the_middle(self):
        keep = 0.3
        right = [0] * 70 + [10] * 20 + [0] * 6
        self.assertGreater(a.subject_center(right, keep), 0.75)
        self.assertEqual(a.subject_center([0] * 96, keep), 0.5)
        self.assertEqual(a.subject_center([], keep), 0.5)
        self.assertAlmostEqual(a.subject_center([1] * 96, keep), 0.5, delta=0.02)  # evenly busy: stay centered


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
            'hashtags': ['#funnycats', 'cats']}

    def test_clean_twist_and_no_credit_in_description(self):
        twist = a.write_twist(SOURCE, ask=lambda _p: dict(self.good))
        self.assertEqual(twist['hashtags'], ['#funnycats', '#cats'])
        text = a.description(SOURCE, twist)
        self.assertEqual(text, self.good['hook'] + '\n\n#funnyanimals #funnycats #cats #shorts')
        self.assertNotIn('The Living Planet', text)
        self.assertNotIn('Credit', text)
        from public_metadata import description as clean
        self.assertEqual(clean(text), text)

    def test_queued_clip_cut_with_the_old_credit_goes_out_without_it(self):
        old = {'description': 'hook\n\n#shorts\n.\n\nCredit: The Living Planet', 'source': SOURCE,
               'twist': {'hook': 'hook', 'hashtags': ['#cats']}}
        self.assertEqual(a.current_description(old), 'hook\n\n#funnyanimals #cats #shorts')
        self.assertEqual(a.current_description({'description': 'plain'}), 'plain')

    def test_copied_title_or_missing_parts_are_redrafted(self):
        drafts = iter([{**self.good, 'title': 'Funny Animal Video'}, {**self.good, 'hook': ''}, self.good])
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

    def test_refused_titles_are_fed_back_so_the_writer_changes_its_words(self):
        # 27 Nov run: every draft was "This little guy chose chaos" + a new emoji, so all five clips failed.
        recent = 'This little guy chose chaos 🐾'
        prompts = []
        def ask(prompt):
            prompts.append(prompt)
            return {**self.good, 'title': 'This little guy chose chaos 😂' if len(prompts) < 3 else self.good['title']}
        def check(title):  # what the duplicate-title rule said on the laptop
            return f'title "{title}" repeats the recent title "{recent}"' if 'chose chaos' in title else None
        self.assertEqual(a.write_twist(SOURCE, ask=ask, check=check)['title'], self.good['title'])
        self.assertNotIn('REFUSED', prompts[0])
        self.assertIn('- This little guy chose chaos 😂', prompts[1])
        self.assertNotIn('chose violence', ''.join(prompts))

    def test_writer_failure_is_its_own_error_so_the_clip_is_not_burned(self):
        with self.assertRaises(a.TwistFailed):
            a.write_twist(SOURCE, ask=lambda _p: {'title': 'funny animal video'})

    def test_overlay_is_one_bottom_center_line_for_the_first_seconds(self):
        twist = a.clean_twist(self.good, SOURCE)
        ass = a.overlay_ass(twist, duration=8.0, caption_seconds=5)
        self.assertEqual(ass.count('Dialogue:'), 1)
        self.assertIn('0:00:00.00,0:00:05.00,Hook', ass)
        self.assertIn('He did NOT see that coming', ass)
        style = next(line for line in ass.splitlines() if line.startswith('Style: Hook'))
        self.assertEqual(style.split(',')[18], '2')  # bottom center
        self.assertIsNone(a.overlay_ass(twist, duration=8.0, caption_seconds=0))
        self.assertNotIn('voice_line', a.twist_prompt(SOURCE))


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
