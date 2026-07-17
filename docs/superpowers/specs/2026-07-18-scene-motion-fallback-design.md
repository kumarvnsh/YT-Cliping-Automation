# Scene/Motion Fallback Design

**Date:** 2026-07-18  
**Status:** Approved

## Goal

Produce one daily Short when YouTube provides no usable most-replayed heatmap,
without adding any AI-token or external-service cost.

## Selection order

For each eligible trending candidate:

1. Try the existing static and rendered YouTube heatmap sources.
2. Download the candidate if it has no heatmap.
3. Run local ffmpeg scene/motion analysis at one frame per second.
4. Sum those scores for every valid 35-second window and use the highest
   scoring window.
5. If every sampled score is zero or unavailable, use the centered valid
   35-second window instead.

The first downloadable eligible candidate therefore produces a clip even when
YouTube has no replay data. Download, edit, metadata generation, source credit,
upload privacy, and candidate-level error handling remain unchanged.

## Architecture

`motion.py` will contain pure parsing and window-selection helpers. `pipeline.py`
will invoke ffmpeg only after a candidate has been downloaded and both heatmap
sources are empty. ffmpeg emits scene scores to a temporary local file; the
pipeline parses those scores, chooses the 35-second start time, and records the
selection method as either `heatmap`, `motion`, or `center` in the run log.

The analysis uses the installed local ffmpeg binary. It sends no video,
metadata, or prompt to any new service and consumes no OpenAI or Claude tokens.

## Failure behavior

- An ffmpeg analysis failure makes that candidate fail and advances to the next
  eligible candidate.
- Empty or all-zero scene scores are valid and select the centered window.
- The selected window is always clamped to the source duration, preserving the
  existing output-duration guarantees.

## Testing

Unit tests will cover ffmpeg score parsing, highest-window selection, centered
fallback, and duration clamping. Pipeline tests will verify heatmaps remain
preferred, while a heatmap-free candidate downloads once and selects the motion
or centered fallback without any browser or LLM mock being newly required.
