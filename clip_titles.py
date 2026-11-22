"""Upload titles: what happens first, then the streamer and evidenced game ("Plane lands in a field | Kai Cenat | GTA 6").

Titles used to start with the streamer's name ("Kai Cenat Live | GTA 6 | ..."), which made a run of uploads look
alike and as if the channel were that streamer. youtube_rules.py refuses titles that do not describe the moment.
"""
import re


def game_label(source):
    known={'gta5':'GTA 5','gta6':'GTA 6'}
    if source.get('game') in known:return known[source['game']]
    text=source.get('title','')
    tokens=re.findall(r'\b(?:gta|grand\s+theft\s+auto)\s*[-:]?\s*(vi|v|6|5)\b',text,re.I)
    games={'GTA 6' if t.lower() in ('vi','6') else 'GTA 5' for t in tokens}
    if len(games)==1:return games.pop()
    return 'GTA' if games or source.get('game')=='gta' or re.search(r'\bgta\b',text,re.I) else 'Gaming'


def upload_title(source, hook='', creator=None):
    name=' '.join(str(source.get('channel') or creator or 'Streamer').split())
    game=game_label(source)
    hook=' '.join(str(hook or '').split())
    suffix=f'{name} | {game}'
    if len(suffix)>60:
        name=name[:60-len(game)-3].rstrip()
        suffix=f'{name} | {game}'
    folded=hook.casefold()
    # Repeated calls (reserve/manual publishing) must not stack; old-style "Name | Game | hook" titles are turned round.
    if folded==suffix.casefold() or folded.endswith((' | '+suffix).casefold()):
        hook=hook[:len(hook)-len(suffix)].rstrip(' |')
    old=f'{name} | {game} | '
    if hook.casefold().startswith(old.casefold()):hook=hook[len(old):]
    # A hook that opens with the streamer as a label ("Kai Cenat Live: ...", "Kai Cenat Live | ...") drops the label.
    lead=re.match(r'^'+re.escape(name)+r'\s*(?:[:|\-–—]\s*)+',hook,re.I)
    if lead:hook=hook[lead.end():]
    if not hook:return suffix
    room=100-len(suffix)-3
    if len(hook)>room:
        hook=hook[:room-1].rsplit(' ',1)[0].rstrip(' ,;:|')+'…'
    return f'{hook} | {suffix}'
