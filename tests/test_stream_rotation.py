import time
from stream_sources import choose_varied


def test_rotation_skips_highest_recent_and_queued():
    rows=[{'channel':x,'viewers':n,'targeted':False} for x,n in [('A',1000),('B',500),('C',100)]]
    state={'capture_history':[{'at':time.time(),'source':rows[0]}],
           'ready_clips':[{'source':rows[1]}]}
    assert choose_varied(rows,state)['channel']=='C'
    assert choose_varied(rows[:2],state) is None


def test_rotation_releases_after_cooldown():
    row={'channel':'Creator','viewers':100,'targeted':True}
    assert choose_varied([row],{'capture_history':[{'at':time.time()-7*3600,'source':row}]})==row


def test_target_wins_over_larger_non_target():
    target={'channel':'Target','viewers':10,'targeted':True}
    other={'channel':'Other','viewers':1000,'targeted':False}
    assert choose_varied([other,target],{})==target
