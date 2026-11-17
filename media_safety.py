"""Reject clip windows outside the source's verified audio coverage."""
def audio_bounds(path):
    import av
    with av.open(str(path)) as container:
        if not container.streams.audio:
            return None
        stream=container.streams.audio[0]
        if stream.duration is None:
            return None
        start=float((stream.start_time or 0)*stream.time_base)
        return start,start+float(stream.duration*stream.time_base)


def safe_window(bounds,ends,duration=30):
    if not bounds: return None
    low,high=bounds
    for end in ends:
        start=end-duration
        if start>=max(0,low)-.05 and end<=high+.05:
            return start,end
    return None
