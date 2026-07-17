# yt-auto-animation — gaming shorts pipeline

Daily job: trending gaming video → most-replayed 35s → 9:16 short with
hook/credit text → upload. Spec + plan in `docs/superpowers/`.

## One-time setup

1. Google Cloud Console: create project, enable **YouTube Data API v3**,
   create an **API key** and an OAuth client (**Desktop app**).
2. `YT_CLIENT_ID=... YT_CLIENT_SECRET=... python get_token.py` → copy
   refresh token.
3. Add a [self-hosted macOS runner](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/adding-self-hosted-runners)
   to this repository with the custom label `yt-shorts`. Keep the Mac awake,
   online, and logged in to the Brave profile that is signed in to YouTube.
   Install ffmpeg once (`brew install ffmpeg`). Each run exports that local
   Brave session for the watch-page lookup and yt-dlp, then deletes the export.
4. Add GitHub secrets: `YT_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`,
   `YT_REFRESH_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`.
   Delete the obsolete `YT_COOKIES` secret after the first successful
   self-hosted run.

## Rollout

Week 1: trigger workflow manually (defaults to dry-run → uploads **private**),
check results in YouTube Studio. Then rely on the daily cron (uploads public).

## Local test

    pip install -r requirements.txt && pytest
    YT_API_KEY=... OPENAI_API_KEY=... python pipeline.py --dry-run

## Known risks

Copyright strikes possible (credit is not a license); reused-content
demonetization; cookie expiry. See spec for details.
