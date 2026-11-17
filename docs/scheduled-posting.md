# Shotsnip scheduled posting

Capture and publishing are separate: each eligible run may prepare one finished clip before the posting window. Up to five ready clips stay in reserve for the current IST day; recording pauses when full. Publishing frees a slot. Unused reserves expire at midnight IST. Ready clips may publish in the last 30 minutes of the two-hour timer, which restarts after successful upload. Queue entries from the previous game or missing files are discarded. The dashboard shows the ready queue.

Run `D:/YT-Cliping-Automation/.venv/Scripts/python.exe D:/YT-Cliping-Automation/scheduled_clips.py --tick` every 30 minutes on this PC. Codex's recurring automation invokes this command. Keep Codex running and the PC awake with an Internet connection. Windows currently has sleep disabled on AC and battery; no power settings were changed.

`scheduler.json` controls enable/pause, 30-minute scanning, 120-minute minimum spacing between successful uploads, and a bounded 180-second capture. No suitable stream or quality clip means that posting interval is skipped. Cadence is a minimum spacing, not a promise of twelve uploads daily.

Discovery checks enabled YouTube targets plus public YouTube live search and the public Twitch game directory. It ranks the measured live viewers of streams returned by those sources; it cannot guarantee discovery of every live stream on either platform. Target creators win equal-count ties. Missing viewer counts are excluded. YouTube game evidence is an unambiguous live title; Twitch uses its actual category and rejects opposite-game mentions. This is metadata filtering, not visual verification of the game being played.

The game switches from GTA V to GTA VI at **2026-11-19 00:00 IST**, per the owner's requested schedule. Discovery, capture selection, and the final upload guard apply it. Old-game footage is refused if processing crosses the boundary.

Public Twitch playback uses anonymous access through yt-dlp; no Twitch credentials are needed for supported public streams. Subscriber-only, restricted or unavailable playback is skipped. Public chat is best effort: Twitch's anonymous IRC connection may be refused. Chat counts/spikes and errors are saved without usernames or message text. If no usable chat peak exists, Clips Kitty selects the moment using transcript/audio/visual signals. A detected usable chat peak selects the preceding 30 seconds with a five-second reaction offset.

Clips Kitty is a separate AGPL-3.0 checkout at `D:/Clips-Kitty`, pinned initially to `56a571faadd244926d1abdae5843d413af8665f9`. Its local API binds only to `127.0.0.1:8765`. Configuration is `config/shotsnip.yaml`: local qwen3:8b, CPU Whisper small, one clip, minimum score 55, own uploads disabled, feedback relay blank, no outro. Required tools and transcription weights are installed. One compatibility patch in `video/cutter.py` replaces the removed FFmpeg `-vsync` option with `-fps_mode`. Preserve this patch when updating upstream.

The scheduler uses Kitty's timing on the original full-frame recording, then applies Gaming Short v1 with local speech captions, hook, bottom message and small source credit. Captions and generated text remain automatic and can contain recognition errors. Background music is not added until a cleared Audio Library track is available locally.

Before every upload, `channels.list(mine=True)` must match Shotsnip's exact channel ID in `channel.json`. Uploads request public privacy; YouTube may return a different effective privacy, which is recorded. `work/scheduler/state.json` is the upload ledger and status export. A unique description marker is written before upload. Pending/uncertain upload results block further posting until the marker is reconciled against Shotsnip's uploads; never erase the entry to force a retry. A Windows byte-range lock prevents overlapping ticks. Dashboard pause is checked again before upload.

Tokens, signed capture URLs, private logs, source media and state stay under ignored paths. Do not commit `token.json`, `analytics_token.json`, `client_secret.json`, or `work/`. Preserve the ledger across restarts. Raw media is retained locally; monitor disk space (capture stops below 3 GB free).

The updated authenticated dashboard preview runs on port 8799. The older dashboard on 8787 belongs to another Windows process context and could not be restarted with this session's permissions. Histold's existing scheduled task remains unchanged.
