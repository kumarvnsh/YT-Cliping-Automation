import json
import unittest
from pathlib import Path

import owner_settings
import youtube_rules

ROOT = Path(__file__).resolve().parents[1]


class OwnerSettings(unittest.TestCase):
    def test_validate_accepts_values_in_range_and_rejects_others(self):
        self.assertEqual(owner_settings.validate({'youtube_rules.max_per_day': 4, 'privacy': 'unlisted'}),
                         {'youtube_rules.max_per_day': 4, 'privacy': 'unlisted'})
        for bad in ({'youtube_rules.max_per_day': 0}, {'youtube_rules.max_per_day': 2.5}, {'privacy': 'secret'},
                    {'require_gta_moment_review': 1}, {'capture_max_height': '1080'}, {'unknown': 1}, {}):
            with self.assertRaises(ValueError):
                owner_settings.validate(bad)

    def test_apply_writes_nested_keys_and_keeps_others(self):
        config = {'enabled': True, 'youtube_rules': {'max_per_day': 3, 'recent_titles': 60}}
        out = owner_settings.apply(config, {'youtube_rules.max_per_day': 5, 'growth_experiment.enabled': False})
        self.assertEqual(out['youtube_rules'], {'max_per_day': 5, 'recent_titles': 60})
        self.assertIs(out['growth_experiment']['enabled'], False)
        self.assertEqual(config['youtube_rules']['max_per_day'], 3)  # input untouched

    def test_fill_writes_every_setting_without_changing_existing_values(self):
        filled = owner_settings.fill({'capture_seconds': 240})
        self.assertEqual(filled['capture_seconds'], 240)
        for key in owner_settings.DEFAULTS:
            self.assertEqual(owner_settings.get(filled, key), owner_settings.value(filled, key))
        self.assertEqual(filled['record_lead_minutes'], 20)

    def test_youtube_rules_use_the_owner_values(self):
        self.assertEqual(youtube_rules.settings({'youtube_rules': {'max_per_day': 5}})['max_per_day'], 5)
        self.assertEqual(youtube_rules.DEFAULTS['min_gap_minutes'], owner_settings.DEFAULTS['youtube_rules.min_gap_minutes'])

    def test_live_scheduler_json_has_every_owner_setting(self):
        config = json.loads((ROOT / 'scheduler.json').read_text(encoding='utf-8'))
        for key in owner_settings.DEFAULTS:
            node = config
            for part in key.split('.'):
                self.assertIn(part, node, key)
                node = node[part]

    def test_describe_lists_every_setting_with_limits(self):
        rows = owner_settings.describe({})
        self.assertEqual(len(rows), len(owner_settings.SETTINGS))
        gap = next(r for r in rows if r['key'] == 'youtube_rules.min_gap_minutes')
        self.assertEqual((gap['min'], gap['max'], gap['value']), (30, 1440, 120))


if __name__ == '__main__':
    unittest.main()
