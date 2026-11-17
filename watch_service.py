"""One-source-at-a-time local clipping worker, controlled by the Shotsnip dashboard."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from filelock import FileLock, Timeout
from game_schedule import active_game, title_matches
from live_clipper import monitor, resolve

ROOT = Path(__file__).resolve().parent
WORK = ROOT / 'work/dashboard'
STOP = WORK / 'stop-worker'


def read(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        return default


def status(state, **details):
    path = WORK/'worker.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps({'status':state,'checked_at':datetime.now(timezone.utc).isoformat(),
                               'publishing':False,**details},indent=2),encoding='utf-8')
    temp.replace(path)


def pick_sources():
    watchlist = read(ROOT/'watchlist.json', {'creators':[]})
    settings = read(WORK/'agent-policy.json', {'source_priorities':{}})
    candidates = []
    for creator in watchlist['creators']:
        if not creator.get('enabled',True):
            continue
        for source in creator['sources']:
            if source['platform']=='youtube':
                candidates.append((creator,source))
    def probe(pair):
        creator,source = pair
        if STOP.exists():
            return None
        try:
            info = resolve(source['url'].rstrip('/')+'/live')
            if not title_matches(info.get('title'),active_game()):
                return None
            return {'creator':creator['name'],'creator_id':creator['id'],
                    'url':info.get('webpage_url'), 'video_id':info['id'],
                    'watching':info.get('concurrent_view_count') or 0,
                    'priority':settings.get('source_priorities',{}).get(creator['name'],1)}
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        live = [x for x in pool.map(probe,candidates) if x]
    return sorted(live,key=lambda x:(x['priority'],x['watching']),reverse=True)


def main():
    WORK.mkdir(parents=True,exist_ok=True)
    try:
        with FileLock(str(WORK/'worker.lock'),timeout=0):
            failures = {}
            while not STOP.exists():
                status('scanning',game=active_game(),note='Checking enabled YouTube targets. Twitch capture is not connected.')
                sources = pick_sources()
                sources = [x for x in sources if time.time()-failures.get(x['video_id'],0)>300]
                if sources and not STOP.exists():
                    chosen=sources[0]
                    status('capturing',source=chosen,game=active_game())
                    try:
                        monitor(chosen['url'],seconds=300,finish_clips=True,
                                should_stop=STOP.exists)
                    except KeyboardInterrupt:
                        break
                    except Exception as exc:
                        failures[chosen['video_id']]=time.time()
                        status('retry_wait',error=str(exc)[:250],source=chosen,
                               note='Back off this source for 5 minutes; try other enabled targets.')
                else:
                    status('waiting',game=active_game(),note='No matching enabled YouTube target is currently live.')
                for _ in range(60):
                    if STOP.exists():
                        break
                    time.sleep(1)
            status('stopped')
    except Timeout:
        print('A clipping worker is already running.',flush=True)


if __name__=='__main__':
    main()
