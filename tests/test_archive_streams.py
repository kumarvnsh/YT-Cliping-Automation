from datetime import date
import json
from archive_streams import replay_candidate,chat_peak


def test_only_yesterday_completed_streams_qualify():
    info={'id':'abc','title':'GTA V stream','live_status':'was_live','duration':4000,
          'view_count':10000,'upload_date':'20261117','channel':'Creator'}
    assert replay_candidate(info,today=date(2026,11,18))['archive']
    assert replay_candidate({**info,'live_status':'not_live'},today=date(2026,11,18)) is None
    assert replay_candidate({**info,'upload_date':'20261116'},today=date(2026,11,18)) is None


def test_chat_peak_uses_replay_offset_and_unique_chatters():
    def message(at,author):
        return json.dumps({'replayChatItemAction':{'videoOffsetTimeMsec':str(at*1000),
            'actions':[{'addChatItemAction':{'item':{'liveChatTextMessageRenderer':{'authorExternalChannelId':author}}}}]}})
    p=chat_peak([message(10,'a'),message(10,'a'),message(40,'b'),message(50,'c')],100)
    assert p=={'end':60,'unique_chatters':2}
