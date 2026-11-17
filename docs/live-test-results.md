# GTA V live test — 17 November 2026

Test machine: Windows laptop, Intel i7-12650H, 16 GB RAM, approximately 95 GB
free disk at the start. No account secrets are included in this report.

## Passed

- Live discovery returned currently live GTA V broadcasts.
- Sentry's `M9d7qlFtU1s` was independently resolved as `is_live` and captured
  for 150 seconds with audio and video.
- The manual buffer test produced a 30.00-second H.264/AAC MP4 at 720x1280.
  Full-file decoding succeeded. A preview was visually inspected to confirm
  GTA V gameplay and preserved framing.
- A simulated spike (12 distinct users against a baseline of 1) passed through
  the detector and rendered another 30-second clip from the real capture.
  This is a simulation, not evidence of a natural live chat spike.
- Four focused tests cover baseline/warmup, repeated-user filtering, cooldown,
  buffer offsets/gaps, live-page configuration parsing, and message parsing.

Artifacts are under `work/live/20261117-111332-M9d7qlFtU1s/`:
`summary.json`, `simulation-test.json`, `preview.jpg`, and both MP4 clips.

## Not proven / failures

- The old pytchat adapter returned no messages. It was replaced with a public
  web-endpoint adapter using the live page's client configuration. That adapter
  connected and polled successfully, but returned no messages in the observed
  interval. Natural chat spike detection still needs an active-chat test.
- The first two sources returned HTTP 500/404 media errors. LadyBot's source
  stalled after a short initial segment. These attempts did not produce clips;
  failed runs retain their own diagnostic summaries.
- The broader repository test run had 67 passing, 3 skipped, and 2 failing
  tests before the fourth new test was added. Both failures are in the older
  watch-page heatmap code/tests, which assume Unix `SIGALRM`/`setitimer` and
  are unavailable on Windows. The new live command does not import that code.
- No uploads were performed. The destination clipper channel is unconfirmed.
- No always-on service, Twitch monitoring, or automatic
  watchlist polling has been installed. See `live-clipping.md` for scope.

## Finishing pass

Added `finish_video.py` and optional `monitor --finish`: CPU speech recognition,
word-timed highlighted captions, on-screen hook, blurred background, preserved
gameplay, creator credit, SRT/transcript files, and source metadata.

Rendered the Sentry test clip with the hook "This parking job needed a photo".
The finished video is 1080x1920, 30 fps, 30 seconds, with H.264 video and AAC
audio. Full decoding/duration checks pass. Caption text and timing are automatic;
proper names and uncertain phrases still need human review before publishing.
Seven focused live/finishing tests pass, including ASS injection escaping,
silence separation, timing, and preserving zero-duration recognized words.
