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

## Animal & brainrot mode

Set `"niche": "animals"` in `scheduler.json` (Studio HQ > Posting rules > Channel content) to switch Shotsnip from
GTA stream clips to funny animal and brainrot Shorts. `animal_shorts.py` runs inside the normal scheduler tick:

1. **Find**: YouTube search ordered by views, rotating queries (`animal_shorts.queries`) and time windows
   (all-time / 30 days / 7 days). Every 3rd cycle (`long_form_every`) searches long compilations instead and cuts
   their most-replayed 30 s (intro skipped). Channels over `max_channel_subscribers` (default 5M) and
   `skip_channels` are left out: big media channels Content-ID everything.
2. **Check and edit**: sources whose title, description or tags say AI, animation, cartoon, Pixar and so on are
   skipped before download. The local vision model (`qwen3-vl:4b-instruct`) then looks at four frames, says what
   happens and whether it is real footage; cartoons, AI clips and games are refused even when they show an animal.
   `qwen3:8b` writes the title and a 2 to 6 word hook. The clip fills the whole 9:16 frame with its own sound (no
   narration, no blurred border). Vertical sources are just scaled; wider ones are cropped to 9:16 around where the
   motion is, falling back to the center when the clip is calm. The hook shows at the bottom center, above the
   Shorts/Reels caption band, for the first `caption_seconds` (5, 0 turns it off).
3. **Credit**: the description always names the original channel and links the original video (with `&t=` for
   long-form clips). Nothing is credited on screen.
4. **Copyright gate**: uploads go up **private**. After `check_minutes` (30) the next tick reads YouTube's verdict:
   rejected or region-blocked videos stay private (`state.animal.quarantined`); clean ones are switched to the
   owner's privacy setting when the posting rules (spacing, variety, titles) allow.

Limit: the YouTube Data API reports rejections and blocks, not Content ID claims that only take the ad money.
Those show in YouTube Studio > Content > Restrictions.
