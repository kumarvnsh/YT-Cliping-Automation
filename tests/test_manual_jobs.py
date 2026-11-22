import pytest
import manual_jobs as jobs


def test_urls_and_timestamp_validation():
    assert jobs.source_url('https://youtu.be/abcdefghijk?t=30')=='https://www.youtube.com/watch?v=abcdefghijk'
    assert jobs.seconds('01:02:03')==3723
    for url in ['http://localhost','https://youtube.com.evil.test/watch?v=abcdefghijk','https://www.youtube.com:8443/watch?v=abcdefghijk','file:///private']:
        with pytest.raises(ValueError): jobs.source_url(url)
    with pytest.raises(ValueError): jobs.seconds('01:70')


def test_batch_creates_separate_previews_and_claims_once(tmp_path):
    ids=jobs.enqueue({'mode':'exact','url':'https://youtu.be/abcdefghijk','ranges':'00:10 - 00:40\n01:00 - 01:30'},tmp_path)
    assert len(ids)==2
    assert not jobs.get(ids[0],tmp_path)['options']['publish']
    first=jobs.claim(tmp_path); second=jobs.claim(tmp_path)
    assert first['id']!=second['id']
    assert jobs.claim(tmp_path) is None


def test_publish_and_retry_controls_are_state_guarded(tmp_path):
    identity=jobs.enqueue({'mode':'auto'},tmp_path)[0]
    with pytest.raises(ValueError): jobs.control(identity,'publish',tmp_path)
    jobs.update(identity,tmp_path,status='ready')
    jobs.control(identity,'publish',tmp_path)
    assert jobs.claim(tmp_path)['publishing_only']
    jobs.update(identity,tmp_path,status='attention')
    with pytest.raises(ValueError): jobs.control(identity,'retry',tmp_path)


def test_private_youtube_never_silently_publishes_to_facebook():
    with pytest.raises(ValueError,match='Facebook Reels are public'):
        jobs.validate({'mode':'auto','privacy':'private','platforms':['youtube','facebook']})


def test_cancel_and_restart_recovery(tmp_path):
    identity=jobs.enqueue({'mode':'auto'},tmp_path)[0]
    jobs.control(identity,'cancel',tmp_path)
    assert jobs.get(identity,tmp_path)['status']=='cancelled'
    other=jobs.enqueue({'mode':'auto'},tmp_path)[0]
    jobs.claim(tmp_path); jobs.recover_interrupted(tmp_path)
    assert jobs.get(other,tmp_path)['status']=='failed'
    jobs.control(other,'retry',tmp_path)
    assert jobs.get(other,tmp_path)['status']=='queued'


def test_prioritize_moves_a_waiting_job_to_the_front(tmp_path):
    first=jobs.enqueue({'mode':'auto'},tmp_path)[0]
    second=jobs.enqueue({'mode':'auto'},tmp_path)[0]
    third=jobs.enqueue({'mode':'auto'},tmp_path)[0]
    jobs.control(third,'prioritize',tmp_path)
    assert jobs.claim(tmp_path)['id']==third
    assert jobs.claim(tmp_path)['id']==first
    with pytest.raises(ValueError,match='waiting job'):
        jobs.control(third,'prioritize',tmp_path)  # already running
    jobs.control(second,'prioritize',tmp_path)  # already first; no change
    assert jobs.claim(tmp_path)['id']==second


def queued_publish(tmp_path, count):
    ids = [jobs.enqueue({'mode': 'auto'}, tmp_path)[0] for _ in range(count)]
    for identity in ids:
        jobs.update(identity, tmp_path, status='queued_publish')
    return ids


def test_scheduled_clips_wait_without_blocking_the_rest(tmp_path):
    first, second = queued_publish(tmp_path, 2)
    with jobs.connection(tmp_path) as db:
        jobs.control_job(db, first, 'schedule', value=jobs.time.time() + 3600)
    assert jobs.claim(tmp_path)['id'] == second
    assert jobs.claim(tmp_path) is None
    with jobs.connection(tmp_path) as db:
        jobs.control_job(db, first, 'schedule', value=None)
    assert jobs.claim(tmp_path)['id'] == first


def test_schedule_limits_and_reordering(tmp_path):
    first, second, third = queued_publish(tmp_path, 3)
    with jobs.connection(tmp_path) as db:
        with pytest.raises(ValueError, match='within the next'):
            jobs.control_job(db, first, 'schedule', value=jobs.time.time() + 40 * 86400)
        with pytest.raises(ValueError, match='already first'):
            jobs.control_job(db, first, 'move_earlier')
        jobs.control_job(db, third, 'move_earlier')
        with pytest.raises(ValueError, match='already last'):
            jobs.control_job(db, second, 'move_later')
    order = [jobs.claim(tmp_path)['id'] for _ in range(3)]
    assert order == [first, third, second]


def test_gap_and_estimate_follow_the_worker_rules():
    batch = {'options': {'local_library_batch': 'x'}}
    plain = {'options': {}}
    config = {'post_minutes': 180, 'early_publish_minutes': 30}
    assert (jobs.publish_gap_minutes(batch, config), jobs.publish_gap_minutes(plain, config)) == (120, 150)
    assert jobs.publish_gap_minutes(plain, dict(config, manual_post_minutes=60)) == 60
    now = 1_000_000.0
    rows = [{'id': 'a', 'status': 'queued_publish', 'created': 1, 'options': {}},
            {'id': 'b', 'status': 'queued_publish', 'created': 2, 'options': {'not_before': now + 10 * 3600}},
            {'id': 'c', 'status': 'queued_publish', 'created': 3, 'options': {}},
            {'id': 'd', 'status': 'ready', 'created': 0, 'options': {}}]
    times = jobs.estimate(rows, now - 600, {'manual_post_minutes': 60}, now)
    assert times == {'a': now + 3000, 'c': now + 3000 + 3600, 'b': now + 10 * 3600}


def test_paused_manual_publishing_keeps_clips_and_frees_live_operations(tmp_path):
    import json
    (first,) = queued_publish(tmp_path, 1)
    assert jobs.pending(tmp_path)
    (tmp_path / 'scheduler.json').write_text(json.dumps({'manual_publishing_paused': True}))
    assert not jobs.pending(tmp_path)          # live operations no longer wait for the paused clip
    assert jobs.claim(tmp_path) is None        # nothing is published
    assert jobs.get(first, tmp_path)['status'] == 'queued_publish'   # the clip stays queued
    (tmp_path / 'scheduler.json').write_text(json.dumps({'manual_publishing_paused': False}))
    assert jobs.claim(tmp_path)['id'] == first
