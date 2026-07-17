# Rendered Browser Heatmap Extraction — Design

Date: 2026-07-18
Status: Approved for planning

## Goal

Recover YouTube most-replayed markers when they are absent from the static
watch-page HTML, while preserving the existing daily GitHub Actions workflow
on the user's self-hosted macOS runner.

## Scope

Use Playwright to launch the locally installed Brave browser in headed mode
with a new temporary profile for each candidate. Seed that profile only with
the run's temporary Netscape `cookies.txt` export, wait for the normal YouTube
player to render, and read heatmap data already held by the rendered page.

The browser must be closed and the temporary profile removed after every
candidate attempt. The user's normal Brave profile must not be automated,
modified, or closed.

## Data flow

```
trending candidate
  -> static watch-page parser
  -> markers found: select clip
  -> otherwise launch temporary headed Brave profile
       -> import temporary YouTube cookies
       -> load rendered watch page
       -> read rendered player heatmap state
       -> close browser and delete profile
  -> markers found: download, edit, upload
  -> no markers: try next candidate
```

The static parser remains first because it is faster and avoids opening a
browser when YouTube does expose markers in HTML.

## Browser interaction

- Add the Python `playwright` package only; the workflow uses the existing
  Brave executable and does not run `playwright install`.
- Default executable path is the standard macOS Brave path. An environment
  variable may override it for a non-standard installation.
- Launch a persistent browser context with a unique temporary user-data
  directory and `headless=False`.
- Convert the locally exported Netscape cookie jar into Playwright cookie
  records and add them to that temporary context before navigation.
- Navigate to the normal `youtube.com/watch?v=<id>` page, wait for rendered
  player readiness, and poll for heatmap markers for a bounded period.
- Query only the page's already-rendered player state. Do not call an
  undocumented YouTube player endpoint or add a PO-token provider.

## Extraction contract

The browser extraction returns the existing heatmap shape:

```python
{"start_time": float, "end_time": float, "value": float}
```

Its marker values are passed through the same finite-number validation,
deduplication, sort order, and `pick_window()` selection already used for
static markers. Browser data cannot bypass validation.

## Failure handling

- Browser launch, cookie import, navigation, page evaluation, or cleanup
  failures are candidate-level errors; the pipeline tries the next candidate.
- A missing heatmap after the bounded rendered-page wait is treated as no
  heatmap, not as a successful fallback clip.
- Browser contexts and temporary profile directories are cleaned up with
  `finally` blocks even if the page or parser fails.
- The existing ten-candidate limit, dry-run behavior, upload semantics, and
  state commits do not change.

## Runner requirements

- The self-hosted runner runs as the logged-in macOS user and remains online.
- Brave is installed at the default path unless the override variable is set.
- The existing `export_cookies.py` script continues to create a temporary
  `cookies.txt`; the workflow removes it after the job.
- A visible temporary Brave window is expected briefly during a run.

## Testing

Unit tests will mock Playwright at the browser boundary and cover:

- Netscape-to-Playwright cookie conversion without leaking cookie values;
- extraction and normalization of rendered marker data;
- browser fallback only after static parsing returns no markers;
- cleanup after both success and browser errors;
- workflow installation of the Python Playwright dependency.

The rollout check is a manual GitHub Actions dry run on the Mac runner,
confirming that a temporary Brave window opens, heatmap markers are found, and
the resulting upload is private.

## Out of scope

- Controlling the user's normal Brave profile;
- headless-only operation;
- direct undocumented YouTube player API calls;
- PO tokens, proxies, or third-party scraping services;
- a non-heatmap clip-selection fallback.
