"""Guarded retention for closed Shotsnip capture segments."""
from contextlib import closing, contextmanager, nullcontext
import json
import msvcrt
from pathlib import Path
import re
import sqlite3
from stat import FILE_ATTRIBUTE_REPARSE_POINT, S_ISREG
import time

from filelock import FileLock, Timeout


class WorkerBusy(Exception):
    pass


@contextmanager
def scheduler_lock(path):
    # Use the same byte range as scheduled_clips and manual_worker.
    with path.open('a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise WorkerBusy from exc
        try:
            yield lock
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def plain_stat(path):
    info = path.lstat()
    if getattr(info, 'st_file_attributes', 0) & FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError('Reparse paths are excluded from raw retention')
    return info


def referenced_runs(value, live, root):
    if isinstance(value, dict):
        for item in value.values():
            yield from referenced_runs(item, live, root)
    elif isinstance(value, list):
        for item in value:
            yield from referenced_runs(item, live, root)
    elif isinstance(value, str):
        path = Path(value)
        if not path.is_absolute():
            if not value.replace('\\', '/').lower().startswith('work/live/'):
                return
            path = root / path
        if path.is_relative_to(live) and path != live:
            yield live / path.relative_to(live).parts[0]


def protections(root, live, db):
    ledger = json.loads((root/'work/scheduler/state.json').read_text(encoding='utf-8-sig'))
    references = list(ledger.get('ready_clips', []))
    for upload in ledger['uploads']:
        facebook = (upload.get('facebook') or {}).get('status')
        if upload.get('status') not in ('uploaded', 'facebook_published') or facebook not in (None, 'published', 'disabled', 'skipped'):
            references.append(upload)
    for options, result in db.execute("SELECT options,result FROM jobs WHERE status IS NULL OR status NOT IN ('published','cancelled')"):
        references.extend(json.loads(value or '{}') for value in (options, result))
    return set(referenced_runs(references, live, root))


def keep_flag(folder, root):
    for parent in (folder, *folder.parents):
        if (parent/'.studio-hq-keep').exists():
            return True
        if parent == root:
            break
    return False


def old_closed_run(folder, cutoff):
    summary = folder/'summary.json'
    try:
        plain_stat(folder)
        info = plain_stat(summary)
        if not S_ISREG(info.st_mode) or info.st_mtime > cutoff:
            return False
        elapsed = json.loads(summary.read_text(encoding='utf-8-sig')).get('elapsed_seconds')
        return type(elapsed) in (int, float) and elapsed >= 0
    except (OSError, ValueError, AttributeError):
        return False


def raw_retention(root, *, delete=False, limit=250, now=None, days=1, held_scheduler_lock=None):
    """Process one batch, reusing the scheduler's open lock when called from its tick."""
    if not 1 <= limit <= 250:
        raise ValueError('Batch limit must be between 1 and 250')
    report = dict(status='complete', eligible=0, bytes_eligible=0, removed=0, bytes_removed=0,
                  protected_runs=0, skipped_files=0, delete_enabled=delete)
    root = Path(root).absolute()
    live = root/'work/live'
    if type(days) not in (int, float) or not 1 <= days < float('inf'):
        report['status'] = 'invalid_retention'
        return report
    cutoff = (time.time() if now is None else now) - days * 86400
    try:
        for path in (live, *live.parents):
            plain_stat(path)
        dashboard = root/'work/dashboard'
        scheduled = root/'work/scheduler'
        queue = root/'work/manual/jobs.sqlite'
        for path in (dashboard, scheduled, queue.parent, queue):
            plain_stat(path)
        for lock_path in (scheduled/'worker.lock', dashboard/'worker.lock'):
            try:
                plain_stat(lock_path)
            except FileNotFoundError:
                pass
        if held_scheduler_lock is None:
            scheduler_guard = scheduler_lock(scheduled/'worker.lock')
        elif held_scheduler_lock.closed or Path(held_scheduler_lock.name).absolute() != scheduled/'worker.lock':
            raise ValueError('Expected the open scheduler worker lock')
        else:
            scheduler_guard = nullcontext()
        with scheduler_guard, FileLock(str(dashboard/'worker.lock'), timeout=0):
            with closing(sqlite3.connect(queue.as_uri()+'?mode=rw', uri=True, timeout=0)) as db, db:
                db.execute('BEGIN IMMEDIATE')
                protected = protections(root, live, db)
                for folder in live.iterdir():
                    if not folder.is_dir():
                        continue
                    if folder in protected or keep_flag(folder, root):
                        report['protected_runs'] += 1
                        continue
                    if not old_closed_run(folder, cutoff):
                        continue
                    for path in folder.glob('segment-*.ts'):
                        if not re.fullmatch(r'segment-[0-9]+\.ts', path.name):
                            continue
                        try:
                            info = plain_stat(path)
                            if not S_ISREG(info.st_mode) or info.st_mtime > cutoff:
                                continue
                            if keep_flag(folder, root):
                                break
                            if delete:
                                path.unlink()
                                report['removed'] += 1
                                report['bytes_removed'] += info.st_size
                            report['eligible'] += 1
                            report['bytes_eligible'] += info.st_size
                        except (OSError, ValueError):
                            report['skipped_files'] += 1
                        if report['eligible'] + report['skipped_files'] >= limit:
                            report['status'] = 'batch_limit_reached'
                            return report
    except (WorkerBusy, Timeout):
        report['status'] = 'worker_busy'
    except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error):
        report['status'] = 'state_unavailable_or_unsafe'
    return report
