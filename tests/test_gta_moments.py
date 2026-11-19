import json
import gta_moments as g


def result(category='police_chase'):
    return dict(category=category,gta_gameplay_visible=True,routine_activity=False,happening_now=True,has_peak=True,has_action_beat=True,
        observations=[{'frame_index':0,'description':'Police vehicles pursue the player car'},
                      {'frame_index':2,'description':'Player collides while police vehicles close in'}],
        evidence_quotes=['police are chasing me'],hook='A Close Call During a Police Chase',summary='A close call during a police pursuit',reason='Visible pursuit and collision')


def test_ongoing_chase_does_not_require_escape_or_stars(monkeypatch):
    monkeypatch.setattr(g,'_frames',lambda *a,**kw:['a']*4)
    assert g.review(video='clip',ask=lambda *_:result())['accepted'] is True
    assert 'ANY wanted level' in g.FOCUS


def test_funny_gameplay_supported(monkeypatch):
    monkeypatch.setattr(g,'_frames',lambda *a,**kw:['a']*4)
    assert g.review(video='clip',ask=lambda *_:result('funny_gameplay'))['accepted'] is True


def test_visual_evidence_needs_distinct_valid_frames(monkeypatch):
    monkeypatch.setattr(g,'_frames',lambda *a,**kw:['a']*4)
    for indices in ([0,0],[0,4]):
        row=result()
        for o,i in zip(row['observations'],indices):o['frame_index']=i
        assert g.review(video='clip',ask=lambda *_:row)['accepted'] is False


def test_idle_or_uncertain_rejected(monkeypatch):
    monkeypatch.setattr(g,'_frames',lambda *a,**kw:['a']*4)
    row=result();row['has_action_beat']=False
    assert not g.review(video='clip',ask=lambda *_:row)['accepted']
    assert not g.review(video='clip',ask=lambda *_:[])['accepted']


def test_transcript_quotes_must_exist(tmp_path):
    transcript=tmp_path/'speech.json'
    transcript.write_text(json.dumps({'segments':[{'text':'police are chasing me right now'}]}))
    assert g.review(transcript,ask=lambda *_:result())['accepted']
    row=result();row['evidence_quotes']=['invented exciting event']
    assert not g.review(transcript,ask=lambda *_:row)['accepted']


def test_missing_footage_fails_closed(monkeypatch):
    def fail(_):raise OSError('missing')
    monkeypatch.setattr(g,'_frames',fail)
    assert not g.review(video='clip',ask=lambda *_:result())['accepted']


def test_facecam_without_gameplay_rejected(monkeypatch):
    monkeypatch.setattr(g,'_frames',lambda *a,**kw:['a']*4)
    row=result();row['gta_gameplay_visible']=False
    assert not g.review(video='clip',ask=lambda *_:row)['accepted']

def test_visual_request_uses_bounded_local_instruct_model(monkeypatch):
    seen={}
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'response':'{"category":"unknown"}'}
    def post(url,**kwargs):
        seen.update(url=url,**kwargs)
        return Response()
    monkeypatch.setattr(g.requests,'post',post)
    assert g._ask('review',['frame'])=={'category':'unknown'}
    assert seen['url']=='http://127.0.0.1:11434/api/generate'
    assert seen['json']['model']=='qwen3-vl:4b-instruct'
    assert seen['json']['options']['num_ctx']<=8192
    assert seen['json']['keep_alive']=='60s'
    assert seen['timeout']==180


def test_routine_movement_cannot_qualify_as_exploration(monkeypatch):
    monkeypatch.setattr(g,'_frames',lambda *a,**kw:['a']*4)
    row=result('exploration');row['routine_activity']=True
    assert not g.review(video='clip',ask=lambda *_:row)['accepted']
    row.pop('routine_activity')
    assert not g.review(video='clip',ask=lambda *_:row)['accepted']


def test_old_policy_acceptance_does_not_pass_new_policy():
    assert not g.approved({'accepted':True})
    assert not g.approved({'accepted':True,'policy_version':1})
    assert g.approved({'accepted':True,'policy_version':g.POLICY_VERSION})

def test_missing_or_verbose_hook_cannot_be_published(monkeypatch):
    monkeypatch.setattr(g,'_frames',lambda *a,**kw:['a']*4)
    for hook in ('','a'*61):
        row=result();row['hook']=hook
        assert not g.review(video='clip',ask=lambda *_:row)['accepted']

def test_preflight_detects_missing_model_before_recording(monkeypatch):
    import pytest
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'models':[{'name':'qwen3:8b'}]}
    monkeypatch.setattr(g.requests,'get',lambda *a,**kw:Response())
    with pytest.raises(RuntimeError,match='requires'):g.preflight()


def test_staggered_pass_can_find_a_brief_action_beat(monkeypatch):
    sampled=[]
    def frames(video,offsets=(2,10,18,26)):
        sampled.append(offsets)
        return ['a']*4
    monkeypatch.setattr(g,'_frames',frames)
    replies=[{**result(),'has_action_beat':False},result()]
    reviewed=g.review(video='clip',ask=lambda *_:replies.pop(0))
    assert reviewed['accepted']
    assert sampled==[(2,10,18,26),(4,12,20,28)]
    assert reviewed['first_pass']['accepted'] is False

def test_customization_can_qualify_with_an_entertaining_context(monkeypatch):
    monkeypatch.setattr(g,'_frames',lambda *a,**kw:['a']*4)
    row=result('exploration')
    row.update(summary='Player tries on a surprising outfit in a clothing store',interest_basis='humor',engagement_detail='The unexpected outfit gets a clear amused reaction from the streamer',routine_activity=True,has_peak=False,has_action_beat=False)
    assert g.review(video='clip',ask=lambda *_:row)['accepted']


def test_exploration_needs_an_interesting_context_not_a_rare_landmark(monkeypatch):
    monkeypatch.setattr(g,'_frames',lambda *a,**kw:['a']*4)
    row=result('exploration')
    assert not g.review(video='clip',ask=lambda *_:row)['accepted']
    row.update(interest_basis='discovery',engagement_detail='The streamer discovers an interactive feature in a new location')
    assert g.review(video='clip',ask=lambda *_:row)['accepted']

def test_in_game_social_phone_and_wildlife_are_valid_interactions(monkeypatch):
    monkeypatch.setattr(g,'_frames',lambda *a,**kw:['a']*4)
    for detail in ('Player scrolls the in-game social feed while sitting on a boat','Player approaches an alligator in the swamp and reacts to it'):
        row=result('game_interaction');row.update(interest_basis='interaction',engagement_detail=detail,routine_activity=True,has_peak=False,has_action_beat=False)
        assert g.review(video='clip',ask=lambda *_:row)['accepted']
