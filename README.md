# yt-auto-animation — gaming shorts pipeline

Daily job: trending gaming video → most-replayed 35s → 9:16 short with
hook/credit text → upload. Spec + plan in `docs/superpowers/`.

## One-time setup

1. Google Cloud Console: create project, enable **YouTube Data API v3**,
   create an **API key** and an OAuth client (**Desktop app**).
2. `YT_CLIENT_ID=... YT_CLIENT_SECRET=... python get_token.py` → copy
   refresh token.
3. Export YouTube cookies (browser extension "Get cookies.txt", Netscape
   format) — needed so yt-dlp works from GitHub's IPs. Re-export when
   downloads start failing.
4. Add GitHub secrets: `YT_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`,
   `YT_REFRESH_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `YT_COOKIES`.

## Rollout

Week 1: trigger workflow manually (defaults to dry-run → uploads **private**),
check results in YouTube Studio. Then rely on the daily cron (uploads public).

## Local test

    pip install -r requirements.txt && pytest
    YT_API_KEY=... OPENAI_API_KEY=... python pipeline.py --dry-run

## Known risks

Copyright strikes possible (credit is not a license); reused-content
demonetization; cookie expiry. See spec for details.
