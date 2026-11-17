"""User's GTA V to GTA VI transition, evaluated in Indian Standard Time."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

IST = timezone(timedelta(hours=5, minutes=30), name='IST')
CONFIG = Path(__file__).resolve().parent / 'game_schedule.json'


def active_game(now=None):
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        raise ValueError('Schedule requires a timezone-aware timestamp')
    cutoff = datetime.fromisoformat(config['switch_at'])
    return config['before_switch'] if now < cutoff else config['from_switch']


def title_matches(title, game):
    tokens = re.findall(r'\b(?:gta|grand\s+theft\s+auto)\s*[-:]?\s*(vi|v|6|5)\b',
                        title or '', flags=re.IGNORECASE)
    found = {'gta6' if token.lower() in ('vi', '6') else 'gta5' for token in tokens}
    return found == {game}


def search_query():
    return 'GTA 6 live' if active_game() == 'gta6' else 'GTA 5 live'
