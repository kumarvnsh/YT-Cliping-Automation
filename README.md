# yt-auto-animation — gaming shorts pipeline

## Live creator watchlist

The first local YouTube live capture/spike prototype is available in
`live_clipper.py`. See [Windows setup and test commands](docs/live-clipping.md).

`watchlist.json` records the requested YouTube sources and matched Twitch
profiles, including Pokimane and Valkyrae. Unconfirmed Twitch matches are
explicitly left absent. Platform IDs still need resolution. This configuration
does not start monitoring: the current pipeline below processes recorded videos
and does not yet consume the watchlist. The destination channel is unset until
the user supplies their own clipper channel URL.

Daily job: trending gaming video → most-replayed 35s → 9:16 short with
hook/credit text → upload. Spec + plan in `docs/superpowers/`.

## One-time setup

1. Google Cloud Console: create project, enable **YouTube Data API v3**,
   create an **API key** and an OAuth client (**Desktop app**).
2. `YT_CLIENT_ID=... YT_CLIENT_SECRET=... python get_token.py` → copy
   refresh token.
3. Add a [self-hosted macOS runner](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/adding-self-hosted-runners)
   to this repository with the custom label `yt-shorts`. Keep the Mac awake,
   unlocked, online, and logged in to the local Brave session that is signed in
   to YouTube; a visible browser window is required for the rendered fallback.
   Install ffmpeg once (`brew install ffmpeg`). Each job exports cookies from
   that signed-in local session, then—only if the normal watch-page parser finds
   no markers—uses an isolated, temporary, headed Brave profile seeded with
   those cookies. It never uses the regular Brave profile, and deletes both the
   temporary profile and cookie export after every job.
4. Add GitHub secrets: `YT_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`,
   `YT_REFRESH_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`.
   Delete the obsolete `YT_COOKIES` secret after the first successful
   self-hosted run.

## Rollout

By default, local pipeline runs use only the normal static watch-page parser;
set `USE_RENDERED_HEATMAP=1` to enable the rendered Brave fallback when static
markers are absent. The self-hosted daily workflow exports `cookies.txt` before
running the pipeline and enables that fallback automatically. Week 1: trigger
the workflow manually (defaults to dry-run → uploads **private**), check
results in YouTube Studio. Then rely on the daily cron (uploads public).

## Local test

    pip install -r requirements.txt && pytest
    YT_API_KEY=... OPENAI_API_KEY=... python pipeline.py --dry-run

## Known risks

Copyright strikes possible (credit is not a license); reused-content
demonetization; cookie expiry. See spec for details.

## Shotsnip local scheduler

The Windows worker uses `scheduler.json`: automatic discovery, capture, editing
and publication run together every 180 minutes. The six-hour streamer cooldown
is separate and keeps creator variety. Existing upload and uncertainty ledgers
must be preserved; changing cadence must never repost a finished publication.

Automatic clips require a GTA content review (`gta_moments.py`) in addition to
Clips Kitty ranking. Police pursuits at any wanted level, police-car chases,
combat, missions, discoveries, stunts/crashes and funny in-game events qualify
when there is a concrete action beat. An ongoing chase does not need a final
escape. Idle footage, menus and unrelated streamer banter do not qualify.

Install the local visual model with `ollama pull qwen3-vl:4b-instruct` on the
worker. The review samples four frames, then four staggered frames if needed, and uses the
cached transcript when available. It uses one local model, an 8192-token visual
context, a bounded request and a 60-second idle expiry. Models stay on D: under
the existing Ollama configuration. This is sampled visual assessment, not a
continuous-video guarantee; ambiguous or unavailable reviews reject automatic
publication and the scheduler tries another source within its normal retry cap.
The review is saved beside the raw clip and in final metadata for inspection.
Explicit manual timestamp choices remain available as user overrides.

For an explicitly requested immediate test, `python scheduled_clips.py --run-now`
bypasses the cadence for that invocation only. Channel, content, pause and
duplicate/uncertain-upload checks still apply; a successful upload restarts the
normal timer. Do not use this override in scheduled invocations.

GTA jobs request Clips Kitty gaming scoring (including software recovery),
so in-game character movement is not mistaken for the streamer reacting.
Routine footage needs a concrete entertaining reaction or interesting discovery;
customization and first-time exploration are allowed alongside preferred action peaks. accepted automatic reviews also provide a concise factual
hook before the game and creator names are added to the upload title.

Selection uses a balanced standard: prefer action, but also accept concrete humor,
streamer reactions, discoveries and mission dialogue. Rare landmarks and a dramatic
ending are not required. Loading, menu-only footage, idle stretches and unrelated
conversation remain excluded.

The v4 framing preset preserves the full webcam panel, limits its enlargement to
4x and requests source video up to 1080p when available. Camera motion and source
sharpness are sampled before rendering. A frozen/soft camera stays visible in a
small top-left box; a healthy camera uses the top split. No camera means footage
only. This preserves actual source frames and does not invent missing motion.
In-world phone/social apps, wildlife encounters and NPC interactions qualify as
interesting game interactions; pause/settings screens remain excluded.
