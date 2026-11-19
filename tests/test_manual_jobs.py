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
