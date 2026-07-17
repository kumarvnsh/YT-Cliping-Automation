# Scene/Motion Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select a deterministic 35-second visual scene/motion window whenever YouTube heatmap sources are empty.

**Architecture:** Keep heatmaps as the preferred selector. After downloading a heatmap-free candidate, use local ffmpeg at one frame per second to emit `lavfi.scene_score` metadata, parse it into timestamped scores, and choose the highest-scoring valid 35-second window; use a centered window when all scores are absent or zero.

**Tech Stack:** Python 3.14, installed ffmpeg, pytest.

---

### Task 1: Pure motion-score parsing and selection

**Files:**

- Create: `motion.py`
- Create: `tests/test_motion.py`

- [ ] **Step 1: Write the failing parser and selector tests**

```python
def test_parse_scene_scores_reads_pts_and_scene_values():
    text = "pts_time:1.0\nlavfi.scene_score=0.2\npts_time:4.0\nlavfi.scene_score=0.9\n"
    assert motion.parse_scene_scores(text) == [(1.0, 0.2), (4.0, 0.9)]

def test_pick_motion_window_prefers_highest_scoring_35_second_window():
    start, method = motion.pick_motion_window([(10.0, 0.1), (50.0, 0.9)], 120, 35)
    assert method == "motion"
    assert 45 <= start <= 50

def test_pick_motion_window_centers_when_scores_are_empty_or_zero():
    assert motion.pick_motion_window([], 120, 35) == (42.5, "center")
    assert motion.pick_motion_window([(10.0, 0.0)], 120, 35) == (42.5, "center")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_motion.py`
Expected: import failure because `motion.py` does not exist.

- [ ] **Step 3: Implement minimal pure helpers**

`parse_scene_scores` pairs each `pts_time:` record with its following
`lavfi.scene_score=` value and ignores malformed, negative, and non-finite
values. `pick_motion_window` clamps sample-aligned starts to the source
duration, sums each 35-second window, and returns `(start, "motion")`; when no
positive scores exist it returns `(max(0, (duration - clip) / 2), "center")`.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/python -m pytest -q tests/test_motion.py`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add motion.py tests/test_motion.py && git commit -m "feat: select clips from scene scores"`

### Task 2: ffmpeg analysis and pipeline integration

**Files:**

- Modify: `pipeline.py`
- Modify: `tests/test_pipeline.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing pipeline tests**

```python
def test_analyze_motion_runs_ffmpeg_and_parses_stdout(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.subprocess, "run", fake_completed_process)
    assert pipeline.analyze_motion(tmp_path / "source.mp4") == [(1.0, 0.7)]

def test_main_uses_motion_after_empty_heatmap(monkeypatch, tmp_path):
    # Mock empty heatmap, downloaded source, and positive score; assert edit start
    # and successful log use selection_method == "motion".

def test_main_uses_center_after_empty_heatmap_and_zero_scores(monkeypatch, tmp_path):
    # Mock empty scene score result and assert selection_method == "center".
```

- [ ] **Step 2: Run focused tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_pipeline.py -k 'motion or center'`
Expected: FAIL because `analyze_motion` and fallback integration do not exist.

- [ ] **Step 3: Implement ffmpeg analysis and selection flow**

`analyze_motion(src)` runs ffmpeg with this exact filter chain:

```text
fps=1,select='gte(scene,0)',metadata=print:file=-
```

It captures stdout, raises `RuntimeError` on nonzero exit, and passes stdout to
`motion.parse_scene_scores`. In `main`, download the source once after fetching
the heatmap. Keep `pick_window` for heatmaps; otherwise call
`motion.pick_motion_window(analyze_motion(src), duration, CLIP_SECONDS)`. Add
the returned method to the successful log record as `selection_method`. A
download or analysis error continues to the next candidate.

- [ ] **Step 4: Document the fallback**

State in `README.md` that heatmaps are preferred and local ffmpeg scene/motion
scoring selects a segment when no heatmap is available; it consumes no LLM
tokens and centered selection is used if no motion signal exists.

- [ ] **Step 5: Run focused and full verification**

Run: `.venv/bin/python -m pytest -q tests/test_motion.py tests/test_pipeline.py`
Expected: PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

Run: `git add pipeline.py README.md tests/test_pipeline.py && git commit -m "feat: fall back to scene motion clip selection"`

### Task 3: Self-hosted runner validation

**Files:**

- No source changes expected.

- [ ] **Step 1: Dispatch the workflow with `dry_run=true`**

Run: `gh workflow run daily.yml --ref master -f dry_run=true`

- [ ] **Step 2: Verify a run log records `selection_method`**

Confirm the pipeline either uploads a private Short with `heatmap`, `motion`, or
`center` selection, or reports a candidate download/analysis error with the
existing candidate-level retry behavior.

- [ ] **Step 3: Keep public cron disabled until the private Short is reviewed**

Do not change the scheduled workflow input or upload privacy policy as part of
this feature.
