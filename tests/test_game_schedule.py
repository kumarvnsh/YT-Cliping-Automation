from datetime import datetime
from game_schedule import active_game, title_matches


def test_switch_at_midnight_ist_and_equivalent_utc_boundary():
    assert active_game(datetime.fromisoformat('2026-11-18T23:59:59+05:30')) == 'gta5'
    assert active_game(datetime.fromisoformat('2026-11-19T00:00:00+05:30')) == 'gta6'
    assert active_game(datetime.fromisoformat('2026-11-18T18:30:00+00:00')) == 'gta6'


def test_reject_wrong_unknown_and_mixed_game_titles():
    assert title_matches('GTA V roleplay live', 'gta5')
    assert title_matches('Grand Theft Auto VI LIVE', 'gta6')
    assert title_matches('GTA6 live', 'gta6')
    assert not title_matches('GTA V live', 'gta6')
    assert not title_matches('GTA 6 countdown while playing GTA 5', 'gta6')
    assert not title_matches('Gaming live', 'gta6')
