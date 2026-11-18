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
