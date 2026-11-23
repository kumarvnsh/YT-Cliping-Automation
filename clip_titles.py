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


NAMED=re.compile(r'\b(?:gta|grand\s+theft\s+auto)\s*[-:]?\s*(?:vi|v|6|5)\b',re.I)


def with_game(title, source):
    """Put the evidenced game in front of a commentary title ("GTA 6: Ludwig walks into a fire").

    Commentary titles are written as our own angle without the "| streamer | game" template, so they carried
    no game at all and lost the GTA 6 search and suggestion traffic. Only a game proven by the live category
    or the stream title is added, and never twice.
    """
    title=' '.join(str(title or '').split())
    label=game_label(source)
    if label not in ('GTA 6','GTA 5') or not title or NAMED.search(title):
        return title
    room=100-len(label)-2
    if len(title)>room:
        title=title[:room-1].rsplit(' ',1)[0].rstrip(' ,;:|')+'…'
    return f'{label}: {title}'


def game_hashtag(source):
    return {'GTA 6':'#GTA6','GTA 5':'#GTA5'}.get(game_label(source),'')
