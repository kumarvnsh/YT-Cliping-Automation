# Shotsnip manual studio

The existing hourly scan/capture/finish/publish flow remains enabled. Manual jobs
take the next free worker slot; the same byte lock prevents overlapping capture
and publication. A preview does not reset the hourly timer. Successful manual
publication does.

## Dashboard controls

- **Run now & publish:** queue automatic discovery immediately, publishing to both platforms.
- **Create clip:** choose automatic discovery, a live link, a replay link, or exact timestamps.
- **Exact timestamps:** enter `00:12:30 - 00:13:00`, one interval per line, at most five.
  Each interval must last 5–60 seconds. Live offsets are from broadcast start and require DVR.
- **Preview:** the default outcome; uploads nothing. Review and publish from Work queue.
- **Publish automatically:** applies only to that job, with the selected platforms and YouTube visibility.
- **Cancel / Stop after this stage:** cancel queued work or stop running processing at its next stage boundary.
- **Retry processing:** available for failed processing, never an uncertain publication.
- **Retry Facebook only:** available after a confirmed Facebook rejection following YouTube success.

Replays use a bounded highlight-search window; they do not promise exhaustive
analysis of a multi-hour broadcast. Chat replay and live DVR depend on source
availability. Exact timestamps bypass automatic highlight selection.

## Storage and recovery

Jobs live in ignored `work/manual/jobs.sqlite`; media and private download logs
live under `work/manual/<job id>`. The dashboard media endpoint serves only files
within the worker's work directory, behind the existing dashboard authentication.
Interrupted processing is marked failed after the next worker acquires the lock.
Interrupted publishing needs reconciliation before another upload is attempted.

The worker can be invoked with `.venv/Scripts/python.exe manual_worker.py`.
It claims one queued job; the dashboard dispatcher keeps claiming remaining jobs.
No secrets are accepted through the job form or exported in its records.

## Caption language

Automatic captions retain Hindi for detected Hindi speech and use English for
all other detected languages. English speech is transcribed directly; other
languages are translated locally with Whisper. The manual speech-language
selector corrects source-language detection; it does not change this output rule.
English and Hindi cached transcripts can be reused. Other cached transcripts
are replaced with an English translation of the selected clip. Metadata records
both source and caption language and whether translation was applied.
