# Local live clipping on Windows

## Game schedule (IST)

`game_schedule.json` selects GTA V through November 18, 2026, then GTA VI only
starting November 19 at 00:00 IST. Discovery defaults to that game's query.
Discovery and monitoring reject titles naming the wrong game, no identifiable
game, or both games. This conservative title filter does not verify gameplay
visually. A running monitor stops when the schedule changes; restart discovery
to choose a GTA VI source. This setting does not install a background service
or enable uploads, and the older recorded-video pipeline is independent.

Run from `D:\YT-Cliping-Automation` in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-live.txt
.\.venv\Scripts\python.exe live_clipper.py discover --query "GTA V roleplay live"
.\.venv\Scripts\python.exe live_clipper.py monitor --url "YOUTUBE_LIVE_URL" --seconds 300 --test-clip-after 70
```

Remove `--test-clip-after` for real chat spike triggers only. The manual test
trigger is always marked `manual-test`, never presented as a natural chat spike.
The command checks that the source is actually live before recording.

## Implemented

- Public YouTube live discovery, separate live audio/video capture, and local
  FFmpeg (automatically uses the bundled Windows executable if needed).
- A rolling 180-second completed-segment buffer, 2 GB startup disk check,
  1 GB low-space stop, bounded run time, and child-process cleanup.
- Unique chatters per five-second bin, median baseline over twelve preceding
  bins, minimum eight unique chatters, three-times-baseline spike threshold,
  and a 90-second cooldown. Initial/stale chat history is excluded.
- Thirty-second vertical 720x1280 clips preserving the entire source frame.
  Each result is fully decoded and checked for duration before being accepted.
- Local `work/live/<run>/summary.json`, aggregate chat bins in `events.jsonl`,
  capture diagnostics, and clip files. Chat text and author identities are not
  persisted. Capture logs can contain expiring media URLs; keep them local.
- No paid AI APIs, uploads, or changes to laptop startup/sleep settings.

## Current limits

This is a bounded, single-source YouTube prototype, not an installed always-on
service. `watchlist.json` is not yet consumed automatically. Twitch capture/chat,
content scoring, public publishing, and startup/recovery still
need implementation. The old recorded-video pipeline is independent.

The chat adapter reads YouTube's public live chat web endpoint using its current
page configuration. This is unofficial and may fail as YouTube changes;
failures are recorded and do not fabricate spikes.
Moving to the official live chat API requires additional API access and scopes;
the existing token grants only upload access. No chat messages means no natural
spike validation, even when video capture succeeds.

The default reaction delay of five seconds is relative to the latest completed
video segment. It is an estimate, not calibrated synchronization. HLS latency,
batch delivery, and per-stream delay must be measured before public automation.
Search can include other GTA games: verify each candidate title manually during
testing. This command never auto-adds search results to the creator watchlist.

Recording is bounded per run; completed old run folders remain for inspection.
For unattended operation, add cross-run retention before scheduling it.

## Finished Shorts: hooks, captions, and credit

```powershell
.\.venv\Scripts\python.exe finish_video.py --input "PATH_TO_CLIP.mp4" --hook "A hook supported by this clip" --creator "Source creator" --source-url "SOURCE_URL"
```

Outputs a separate finished folder containing `finished-short.mp4`, editable
`captions.srt`, word timings and text in `transcript.json`, `overlays.ass`,
source/upload metadata, and preview frames. The source clip is preserved.
Rendering uses 1080x1920 at 30 fps, a blurred background, full gameplay frame,
bold hook, highlighted current caption word, and source credit. It checks the
whole finished video decodes and matches source duration.

Speech transcription uses faster-whisper on the CPU without a paid API.
The first run downloads a model to `work/models`; later runs reuse it. Use
`--language hi` or `--language en` if automatic language detection is wrong.
Captions follow the spoken language; translation is not enabled. Automatic
transcriptions, especially names and mixed-language speech, need review.
Correct the word text in the transcript and rerun with `--transcript FILE`.
Word timings are preserved. No captions are invented for silence.

To finish new clips as they are captured, add `--finish` to `monitor`, optionally
with a reviewed `--hook`. Without one, a neutral creator label is used. No
context-sensitive AI hook generation or posting is performed.

### Default video template

The accepted layout is saved in `templates/gaming-short-v1.json` and
`templates/gaming-short-v1.ass`. Both standalone finishing and `monitor --finish`
use it by default. The JSON controls positions, gameplay framing, background,
accent, and the bottom message; the ASS file controls fonts, colors, sizes, and
outlines. It retains the small source credit at y=1760 and the bottom message
at y=1570. Only the hook, captions, category, and source identity vary per clip.
This is a repository video template, not a Codex artifact-template skill.

Each finished output includes `timings.json` with measured transcription,
layout, rendering, validation/previews, and total wall time. Cached transcripts
are marked explicitly; capture waiting and uploads are excluded. Model download
time occurs on first use. Benchmark figures describe this laptop and workload,
not a guaranteed processing rate.

Run unit checks:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_live_clipper.py -q
```
