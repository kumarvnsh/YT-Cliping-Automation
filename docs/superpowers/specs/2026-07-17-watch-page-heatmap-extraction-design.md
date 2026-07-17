# Watch-Page Heatmap Extraction — Design

Date: 2026-07-17
Status: Approved and implemented

## Goal

Restore most-replayed clip selection now that the installed yt-dlp YouTube
extractor no longer returns `info["heatmap"]`.

## Scope

Use only the existing `YT_COOKIES` browser session and the public YouTube watch
page for each candidate. Do not use a PO-token provider, browser automation, or
an undocumented YouTube player API request.

## Data flow

```
trending candidate
  -> fetch watch-page HTML with cookies
  -> extract most-replayed markers
  -> no markers: skip without downloading
  -> download selected candidate
  -> pick 35-second window, edit, upload, commit state
```

`main()` checks the heatmap before calling `download()`. It uses the source
duration already returned by YouTube Data API `videos.list`, so heatmap picking
does not depend on yt-dlp metadata.

## Watch-page request

`fetch_heatmap(video_id)` will:

1. Load `cookies.txt` as a Netscape cookie jar when it exists.
2. Request `https://www.youtube.com/watch?v=<video_id>` with that cookie jar
   and a desktop-browser User-Agent.
3. Scan the returned HTML for embedded JSON values under
   `heatMarkerRenderer`.
4. Normalize each valid marker to:

   ```python
   {
       "start_time": timeRangeStartMillis / 1000,
       "end_time": (timeRangeStartMillis + markerDurationMillis) / 1000,
       "value": heatMarkerIntensityScoreNormalized,
   }
   ```

The parser uses `json.JSONDecoder.raw_decode` at every marker-object start
instead of depending on a fixed, private player-response hierarchy. Duplicate
marker ranges are removed and results are sorted by start time.

## Failure handling

- No markers in a valid page response returns an empty list; the candidate is
  skipped before any media download.
- HTTP, cookie, or JSON parsing errors raise a candidate-level error and the
  pipeline tries the next candidate.
- The existing ten-candidate limit remains. Since no-heatmap candidates are no
  longer downloaded, that limit does not waste media bandwidth.
- A failed run is still written to `log.jsonl` and committed by the workflow.

## Testing

Unit tests will cover:

- extraction and normalization of representative embedded marker JSON;
- empty output when no marker JSON is present;
- invalid marker records being ignored;
- `main()` skipping a no-heatmap candidate before `download()` and proceeding
  to a later candidate with a heatmap.

## Out of scope

- PO tokens or external token providers;
- browser automation;
- YouTube internal player API calls;
- fallback clip selection when a video lacks most-replayed data.
