from clip_titles import upload_title,game_label


def test_target_game_comes_from_evidence_not_the_calendar():
    source={'channel':'IShowSpeed','game':'gta','title':'PLAYING GTA 6 EARLY ACCESS GAMEPLAY'}
    assert upload_title(source,'A surprising jump')=='A surprising jump | IShowSpeed | GTA 6'
    assert game_label({**source,'title':'Playing GTA V'})=='GTA 5'
    assert game_label({**source,'title':'Every GTA before GTA 6: GTA 5'})=='GTA'


def test_long_hooks_preserve_streamer_and_game_under_youtube_limit():
    source={'channel':'Mythpat','game':'gta6'}
    title=upload_title(source,'An unexpected moment during the stream '*20)
    assert title.endswith(' | Mythpat | GTA 6')
    assert len(title)<=100
    assert upload_title(source,title)==title


def test_manual_non_gta_does_not_get_a_fabricated_game():
    assert upload_title({'channel':'Creator','game':'custom'},'A live moment')=='A live moment | Creator | Gaming'
    assert upload_title({'channel':'Creator','game':'gta5'},'')=='Creator | GTA 5'
