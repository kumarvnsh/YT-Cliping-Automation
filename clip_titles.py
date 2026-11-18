"""Keep the streamer and evidenced game visible in every upload title."""
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
    prefix=f'{name} | {game}'
    if len(prefix)>100:
        name=name[:100-len(game)-3].rstrip()
        prefix=f'{name} | {game}'
    hook=' '.join(str(hook or '').split())
    # Repeated calls (reserve/manual publishing) must not stack prefixes.
    if hook.casefold()==prefix.casefold():return prefix
    if hook.casefold().startswith((prefix+' | ').casefold()):hook=hook[len(prefix)+3:]
    if not hook:return prefix
    room=100-len(prefix)-3
    if room<8:return prefix
    if len(hook)>room:
        hook=hook[:room-1].rsplit(' ',1)[0].rstrip(' ,;:|')+'…'
    return f'{prefix} | {hook}'
