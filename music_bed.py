"""Generate the original instrumental used in the approved local preview."""
from pathlib import Path
import wave


def original_bed(folder):
    path=Path(folder)/'original-gaming-beat-v1.wav'
    if path.is_file():return path
    import numpy as np
    path.parent.mkdir(parents=True,exist_ok=True)
    sr=44100;duration=30;audio=np.zeros(sr*duration);rng=np.random.default_rng(42)
    def add(at,signal,gain=1):
        pos=int(at*sr);n=min(len(signal),len(audio)-pos)
        if n>0:audio[pos:pos+n]+=signal[:n]*gain
    step=60/108
    for beat,at in enumerate(np.arange(0,duration,step)):
        t=np.arange(int(sr*.32))/sr
        phase=2*np.pi*(48*t+65*.025*(1-np.exp(-t/.025)))
        add(at,np.sin(phase)*np.exp(-t*17),.55 if beat%4 in (0,2) else .3)
        if beat%4 in (1,3):
            t=np.arange(int(sr*.16))/sr;n=rng.normal(0,1,len(t));n=np.r_[0,np.diff(n)]
            add(at,(n*.12+np.sin(2*np.pi*180*t)*.15)*np.exp(-t*35),.55)
        t=np.arange(int(sr*.06))/sr;n=rng.normal(0,1,len(t));n=np.r_[0,np.diff(n)]
        add(at,n*np.exp(-t*95),.05);add(at+step/2,n*np.exp(-t*95),.025)
        if beat%2==0:
            freq=[55,65.406,49,41.203][(beat//8)%4];t=np.arange(int(sr*step*1.8))/sr
            add(at,(np.sin(2*np.pi*freq*t)+.15*np.sin(2*np.pi*2*freq*t))*np.minimum(t/.02,1)*np.exp(-t*2.5),.28)
        if beat%4==0:
            t=np.arange(int(sr*1.2))/sr
            for freq in [220,261.626,329.628]:add(at,np.sin(2*np.pi*freq*t)*np.minimum(t/.04,1)*np.exp(-t*3),.035)
    audio=np.tanh(audio);audio*=.16/max(np.sqrt(np.mean(audio**2)),1e-8)
    with wave.open(str(path),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(sr)
        wav.writeframes((np.clip(audio,-.9,.9)*32767).astype('<i2').tobytes())
    return path
