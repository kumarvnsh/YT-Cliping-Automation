import json
import re
import subprocess
import wave
from pathlib import Path
import pytest
import adaptive_framing as framing
import finish_video
from music_bed import original_bed


def test_default_has_only_caption_events():
    transcript={'duration':5,'segments':[{'start':1,'end':2,'text':'Hello there'}]}
    ass,_,_=finish_video.build_subtitles(transcript,'A hook','Creator')
    events=[r for r in ass.splitlines() if r.startswith('Dialogue:')]
    assert events and all(r.split(',',4)[3]=='Caption' for r in events)
    assert 'SOURCE / CREATOR' not in ass and 'Watch what happens' not in ass


@pytest.mark.parametrize('size,content',[
    ((1280,720),(1280,718,0,0)),((1920,1080),(1920,1080,0,0)),
    ((1080,1920),(1080,1920,0,0))])
def test_both_layouts_keep_source_crops_in_bounds(size,content):
    template=finish_video.load_template()
    for camera in ([0,.42,.2,.22],None):
        graph,layout=framing.video_graph(*size,content,camera,template)
        for w,h,x,y in re.findall(r'crop=(\d+):(\d+):(\d+):(\d+)',graph.split('[framed]')[0]):
            w,h,x,y=map(int,(w,h,x,y))
            assert w>0 and h>0 and x>=0 and y>=0
            assert w+x<=size[0] and h+y<=size[1]
        assert layout['mode']==('camera_top_gameplay_bottom' if camera else 'gameplay_only')
        assert 'drawbox' not in graph


def test_invalid_camera_cannot_create_blank_top_panel():
    for box in ([0,0,1,1],[.9,0,.2,.2],[float('nan'),0,.2,.2],None):
        assert framing.valid_camera(box) is None
        _,layout=framing.video_graph(1280,720,(1280,720,0,0),box,finish_video.load_template())
        assert layout['mode']=='gameplay_only'


def test_probe_timeout_is_footage_only(tmp_path,monkeypatch):
    install=tmp_path/'kitty';(install/'.venv/Scripts').mkdir(parents=True)
    (install/'.venv/Scripts/python.exe').touch();(install/'yolov8n-pose.pt').touch()
    monkeypatch.setattr(framing,'KITTY',install)
    def timeout(*args,**kwargs):raise subprocess.TimeoutExpired('probe',90)
    monkeypatch.setattr(framing.subprocess,'run',timeout)
    assert framing.detect_camera(tmp_path/'input.mp4','Creator',tmp_path)['camera'] is None


def test_original_music_is_a_reusable_valid_audio_file(tmp_path):
    path=original_bed(tmp_path)
    with wave.open(str(path)) as sound:
        assert sound.getnchannels()==1 and sound.getsampwidth()==2
        assert sound.getnframes()/sound.getframerate()==30
    assert original_bed(tmp_path)==path

def test_camera_foreground_has_a_bounded_scale_and_preserves_full_panel():
    template=finish_video.load_template()
    graph,layout=framing.video_graph(1280,720,(1280,720,0,0),[0,.4,.205,.207],template)
    assert layout['camera_upscale']<=4
    assert layout['camera_fit']=='contain'
    assert layout['camera_height']==480
    assert '[cback][cfront]overlay' in graph


def test_frozen_camera_becomes_small_corner_and_keeps_the_face():
    graph,layout=framing.video_graph(1280,720,(1280,720,0,0),[0,.4,.205,.207],finish_video.load_template(),presentation='small_corner')
    assert layout['mode']=='gameplay_with_small_camera'
    assert layout['camera_position']==[24,24]
    assert layout['camera_foreground_size'][0]<=1080*.26
    assert layout['camera_upscale']<=4
    assert 'overlay=x=24:y=24' in graph


def test_static_camera_with_moving_game_is_reported_as_frozen():
    import numpy as np
    camera=[np.zeros((24,32),dtype=np.uint8) for _ in range(8)]
    game=[np.full((24,32),i*5,dtype=np.uint8) for i in range(8)]
    report=framing.camera_sample_health(camera,game,[100]*8,(260,148))
    assert not report['usable']
    assert report['longest_frozen_seconds']==7


def test_quiet_scene_does_not_by_itself_prove_a_frozen_camera():
    import numpy as np
    frames=[np.zeros((24,32),dtype=np.uint8) for _ in range(8)]
    assert framing.camera_sample_health(frames,frames,[100]*8,(260,148))['usable']
