# YT Gaming Shorts Auto-Pipeline — Design

Date: 2026-07-17
Status: Approved

## Goal

Fully automated daily pipeline: find trending gaming videos on YouTube, cut the
most-replayed moment into a 9:16 short with captions/edits (no voiceover), and
upload it publicly to the user's channel. Runs on GitHub Actions cron, zero
manual touch.

## Decisions (from brainstorm)

- Content source: clip from others' trending videos (user accepts copyright risk).
- Transformation level: captions/hook text + vertical crop + source credit. No AI voiceover.
- Cadence: 1 short/day, full auto, public upload.
- Runtime: GitHub Actions scheduled workflow.
- Approach: single Python script, official APIs, heatmap-based clip picking,
  Claude API for metadata only.

## Known risks (accepted)

- Copyright strikes possible even with credit — credit is not a license.
  Repeated strikes can terminate the channel.
- "Reused content" policy may block monetization.
- yt-dlp from GitHub Actions datacenter IPs is intermittently blocked;
  mitigated with browser cookies in a secret, refreshed manually when it breaks.
  Fallback with zero code change: run the same script on the Mac via launchd.
- Some videos lack most-replayed heatmap data; those are skipped.

## Pipeline flow (daily)

```
trending fetch → dedupe vs history → pick clip window → Claude metadata
→ download → ffmpeg edit → upload → commit state
```

Steps:

1. **Trending fetch** — YouTube Data API `videos.list`,
   `chart=mostPopular`, `videoCategoryId=20` (Gaming), `regionCode=US`, top 25.
   Filter out: videos < 2 min, videos already in `used_videos.json`.
2. **Download** — yt-dlp, 720p max, `--write-info-json` (contains `heatmap`),
   cookies loaded from `YT_COOKIES` secret.
3. **Clip pick** — parse `heatmap` from info JSON (YouTube most-replayed data).
   Choose the 35-second window with the highest summed heatmap value.
   No heatmap → skip video, try the next trending candidate.
4. **Metadata** — one Claude API call (claude-haiku-4-5), input: source title,
   channel name, game. Output JSON: short title (<100 chars), description
   (includes source credit + link), hashtags (#shorts #gaming + game tag),
   hook text. Called before ffmpeg because the hook text is burned in.
5. **Edit** — single ffmpeg command: cut 35s → crop/scale to 1080×1920 center
   → drawtext hook at top → drawtext "Credit: @channel" at bottom.
6. **Upload** — YouTube Data API `videos.insert`, `privacyStatus=public`
   (`private` in dry-run), `madeForKids=false`, category 20. OAuth refresh
   token minted once locally by `get_token.py`, stored as GH secret.
   Upload = 1600 quota units; 1/day fits the 10k daily cap (~6/day max).
7. **State + errors** — append to `used_videos.json` and `log.jsonl`, commit
   back to the repo from the action (repo is the database). Any step failure →
   move to next trending candidate, max 3 attempts, then exit nonzero so
   GitHub emails the user. No queues, no monitoring stack.

## Files

- `pipeline.py` — the whole pipeline (~200 lines), `--dry-run` flag.
- `get_token.py` — one-time local OAuth flow to mint the refresh token.
- `.github/workflows/daily.yml` — cron schedule + secrets wiring + state commit.
- `requirements.txt` — yt-dlp, google-api-python-client, google-auth, anthropic.
- `used_videos.json` — dedupe state, committed.
- `log.jsonl` — run log, committed.

## Secrets (GitHub Actions)

`YT_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`,
`ANTHROPIC_API_KEY`, `YT_COOKIES` (Netscape cookie file contents).

## Testing

- `pipeline.py --dry-run`: full pipeline but uploads as `private`, plus
  assert-based self-check (output duration ≈ 35s, resolution 1080×1920).
- Rollout: week 1 run workflow manually with dry-run, eyeball results in
  YouTube Studio, then enable the cron.

## Out of scope (YAGNI)

- Voiceover/commentary, speech-to-text captions, thumbnails (shorts pick a
  frame), analytics dashboards, multi-region trending, multiple uploads/day,
  retry queues, databases.
