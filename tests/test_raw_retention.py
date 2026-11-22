from contextlib import closing
import json
import os
import sqlite3
import time

from filelock import FileLock
import pytest

from raw_retention import raw_retention, scheduler_lock
from clip_efficiency import cleanup


@pytest.fixture
def library(tmp_path):
    for directory in ('live', 'scheduler', 'dashboard', 'manual'):
        (tmp_path/'work'/directory).mkdir(parents=True)
    (tmp_path/'work/scheduler/state.json').write_text('{"uploads":[]}', encoding='utf-8')
    queue = tmp_path/'work/manual/jobs.sqlite'
    with closing(sqlite3.connect(queue)) as db, db:
        db.execute('CREATE TABLE jobs (status TEXT, options TEXT, result TEXT)')
    return tmp_path, queue


def segment(root, run, age_hours=25, closed=True):
    folder = root/'work/live'/run
    folder.mkdir()
    path = folder/'segment-000001.ts'
    path.write_bytes(b'raw capture bytes')
    when = time.time() - age_hours * 3600
    os.utime(path, (when, when))
    if closed:
        summary = folder/'summary.json'
        summary.write_text('{"elapsed_seconds":300}', encoding='utf-8')
        os.utime(summary, (when, when))
    return path


def test_guard_keeps_queued_references_pinned_recent_and_open_runs(library):
    root, queue = library
    removable = segment(root, 'expired')
    queued = segment(root, 'queued')
    pinned = segment(root, 'pinned')
    recent = segment(root, 'recent', age_hours=2)
    unclosed = segment(root, 'unclosed', closed=False)
    (pinned.parent/'.studio-hq-keep').touch()
    with closing(sqlite3.connect(queue)) as db, db:
        db.execute('INSERT INTO jobs VALUES (?,?,?)',
                   ('queued', '{}', json.dumps({'original_file': str(queued.parent/'final.mp4')})))
    assert raw_retention(root)['eligible'] == 1
    assert removable.exists()
    assert raw_retention(root, delete=True)['removed'] == 1
    assert not removable.exists()
    assert all(path.exists() for path in (queued, pinned, recent, unclosed))


def test_both_worker_locks_block_cleanup(library):
    root, _ = library
    path = segment(root, 'expired')
    with scheduler_lock(root/'work/scheduler/worker.lock'):
        assert raw_retention(root, delete=True)['status'] == 'worker_busy'
    with FileLock(str(root/'work/dashboard/worker.lock')):
        assert raw_retention(root, delete=True)['status'] == 'worker_busy'
    assert path.exists()


def test_queue_writer_and_corrupt_ledger_block_cleanup(library):
    root, queue = library
    path = segment(root, 'expired')
    with closing(sqlite3.connect(queue)) as db:
        db.execute('BEGIN IMMEDIATE')
        assert raw_retention(root, delete=True)['status'] == 'state_unavailable_or_unsafe'
    (root/'work/scheduler/state.json').write_text('{}', encoding='utf-8')
    assert raw_retention(root, delete=True)['status'] == 'state_unavailable_or_unsafe'
    assert path.exists()


def test_existing_cleanup_leaves_segments_to_guard_and_preserves_three_days(library):
    root, _ = library
    recent = segment(root, 'two_days', age_hours=48)
    old = segment(root, 'four_days', age_hours=96)
    assert cleanup(root, days=3)['files_removed'] == 0
    assert old.exists()
    with scheduler_lock(root/'work/scheduler/worker.lock') as lock:
        result = raw_retention(root, days=3, delete=True, held_scheduler_lock=lock)
    assert result['removed'] == 1
    assert recent.exists() and not old.exists()
