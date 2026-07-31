# Work Log

Reverse-chronological log of notable changes. Newest first.

> **Maintenance rule:** update this file whenever the inspection or measurement
> pipeline changes — add a dated entry below AND update the "Current pipeline"
> reference if the flow or preprocessing changed. Keep the reference in sync
> with `app/inference/measure.py` and `app/ui/main_window.py`.

---

## Current pipeline (living reference — keep in sync with the code)

### End-to-end flow (one inspection)

```
  PLC trigger / Run Inspection            [main_window._do_classify]
              │
              ▼
  ┌────────────────────────────┐
  │ engine.classify()          │   recognition — UNCHANGED core
  │ per-model score/confidence │   overlay rendered only if "heatmap" ticked
  │ winner, matched?           │
  └─────────────┬──────────────┘
                ▼
          ╱──────────╲
        ╱  matched?    ╲── No ──► "Not a known model" → zero frame to PLC → END
          ╲──────────╱
                │ Yes
                ▼
        ╱────────────────╲
      ╱ "Measure diameter" ╲── unchecked ──► classifier winner only → END
        ╲   checkbox on?   ╱
          ╲──────────────╱
                │ checked
                ▼
  ┌─────────────────────────────────────────┐
  │ measure.measure()  (see preprocessing)  │  config: measure_config.json
  │  → diameter in px and mm, roundness     │  (fit_method + threshold_method)
  └─────────────┬───────────────────────────┘
                ├─► "Show binary mask" ticked → MaskViewDialog (overlay + numbers)
                ├─► terminal log: background YES/NO | threshold N (method) | fit
                ▼
          ╱──────────────╲
        ╱ measurement ok? ╲── No ──► classifier winner (reason logged) → END
          ╲──────────────╱
                │ Yes
                ▼
  ┌─────────────────────────────────────────┐
  │ verdict.pick_by_diameter()              │  only if ≥2 models pass the
  │  nearest stored pixel_diameter within   │  confidence gate AND carry a
  │  diameter_tolerance_pct                  │  measured pixel_diameter
  └─────────────┬───────────────────────────┘
                ▼
        FINAL model → UI (Diameter row) → PLC 13-byte frame
```

### Measurement preprocessing (inside `measure.measure`)

Exact order, from `build_mask()` then the fit in `app/inference/measure.py`:

```
 1. Background subtraction   cv2.absdiff(frame, background)
                             — ONLY if a reference exists and matches the frame
                               size; otherwise the raw frame is used (fallback,
                               logged as "background subtraction: NO").
 2. Grayscale               cv2.cvtColor(..., BGR2GRAY)
 3. Blur                    cv2.GaussianBlur(5x5)      — calm sensor noise
 4. Threshold               Otsu (threshold_method=otsu) OR fixed cut
                            (threshold_method=fixed, threshold_value) → binary mask
 5. Morphology OPEN         5px ellipse                — remove speckle
 6. Morphology CLOSE        9px ellipse ×2             — seal small gaps
 7. Contours                findContours(RETR_EXTERNAL)
 8. Fragment filter         drop fragments < 2% of the largest   (_FRAGMENT_MIN_RATIO)
 9. Convex hull             convexHull of the kept fragments
10. Edge reject             if the hull touches the frame border → fail
                            ("component touches frame edge")
11. Fit circle             cv2.minEnclosingCircle(hull) → diameter_px
12. To mm                  diameter_px × pixel_to_mm  (0.885)
    roundness = hull_area / circle_area   (< 0.90 ⇒ "NOT round" warning)
```

Overlay drawn for the debug window: **green** = the hull measured (step 9),
**red** = the fitted min enclosing circle (step 11), red cross = its centre.

Both `fit_method` values (`min-enclosing`, `hull`) run steps 8–11 identically
today; see the 2026-07-28 entry for why.

---

## 2026-07-29 — Report: DB schema + build/install steps; dashboard width

- PROJECT_DOCUMENTATION: new §13 "Data storage — the history database"
  (inspections + audit_log schema tables, how the DB auto-generates, how it's
  placed/backed-up on the client); expanded §12 with the full double-click .exe
  build (PyInstaller spec) and client-install steps. Renumbered progress→§14,
  glossary→§15 and fixed the cross-reference.
- dashboard.py: capped the "Wheel Count by Model" block width (max 430px,
  stretch 2:5) so it no longer stretches across a wide screen.

## 2026-07-29 — Report images/flowchart + context handoff + report prompt

- PROJECT_DOCUMENTATION: reworded description (diameter used to separate
  same-pattern models, not "optional"); added Screenshots (home + 2 working
  images) and an SVG flowchart (inspection + training, each block labelled with
  its source file); replaced "Connecting the camera" with a stream-URL caution +
  troubleshooting table.
- build_docs.py: embeds images as base64 and inlines SVG (single portable HTML);
  fixed multi-line bullets that were spilling into loose paragraphs.
- Added `docs/CONTEXT_HANDOFF.md` (engineering brain-dump: decisions, traps,
  magic numbers, limits, open TODOs) and `prompts/generate_project_report.md`
  (reusable prompt to produce this report system for other projects).

## 2026-07-28 — Console-less build + file logging + data beside exe

- WheelInspection.spec: console=False (no terminal window on launch).
- main.py: when there is no console, redirect stdout/stderr to
  WheelInspection.log next to the exe (UTF-8, per-run timestamp header,
  ~5 MB rollover). Dev runs (console present) still print on screen.
- Confirmed history.db, users.json, settings.json, measure_config.json and
  models/ all resolve next to the exe via the frozen-aware ROOT (paths.py),
  so they live in dist/WheelInspection/ and survive app upgrades.
- Build: `pyinstaller WheelInspection.spec --noconfirm`, then copy Assets,
  models, settings.json, measure_config.json beside the exe.

## 2026-07-28 — Diameter tie-break reworked + controls moved to config

**Problem.** Two same-pattern wheels of different size both recognised as the
smaller one. Root cause: the models had `pixel_diameter = 0` (trained before the
measurement feature), so the old tie-break had no candidates and never ran.
Codex had then changed the logic to override/reject on diameter, which ignored
appearance confidence, could flip a confident correct match on a noisy reading,
and left a dead "reject" branch in `_do_classify` (main_window never returned
None). Backups kept as `verdict_old.py` / `main_window_old.py`.

**New behaviour (refine-only, confidence-gated).** `pick_by_diameter` now takes
`tie_confidence`. It runs the diameter step ONLY when 2+ models score at least
`tie_confidence` AND carry a stored `pixel_diameter`; among those it picks the
nearest within `diameter_tolerance_pct`. It never rejects a part and never
overrides a lone confident match — so it can only refine recognition. Rationale:
recognition resizes to 256×256, which erases size, so same-pattern rivals both
score high; that "both high" is the trigger to use size. Default
`tie_confidence = 0.5` (rivals ~0.85–0.95, unrelated models ~0–0.25).

**Controls moved to `measure_config.json`** (UI checkboxes removed):
`measure_diameter`, `tie_confidence`, `diameter_tolerance_pct`,
`show_binary_mask`, `show_heatmap_overlay` (plus existing `fit_method`,
`threshold_method`, `threshold_value`). Removed the 3 checkboxes + threshold
slider and their handlers from main_window; deleted the dead rejection branch;
made verdict reason strings ASCII (a `≥` would crash the cp1252 Windows console).
Verified: BIG-wheel→BIG, SMALL→SMALL, lone-confident→appearance, size-matches-
nothing→appearance, pixel_diameter=0→appearance.

## 2026-07-28 — Project documentation expanded

Added dependency table (exact tested versions), file-structure tree with a
"who-calls-whom" link diagram, PySide6 threading architecture (main thread +
4 QThreads and their signals), full data-flow diagram, PatchCore training-vs-
inference pipeline flowchart, camera-connection guide + troubleshooting, and a
machine-environment precautions section. Regenerated the HTML (14 sections,
6 tables, 8 flowcharts). Verified.

## 2026-07-28 — Project documentation (md + auto-generated html)

Added `docs/PROJECT_DOCUMENTATION.md` (single source of truth) and
`docs/build_docs.py`, a stdlib-only Markdown→HTML converter. Edit the `.md`,
run `python docs/build_docs.py`, and `PROJECT_DOCUMENTATION.html` regenerates —
self-contained (inlined CSS, no external fonts/scripts), so it opens offline on
the production machine. The doc covers the recognition and measurement
pipelines, preprocessing steps, image resolutions, why each method was chosen,
and a day-1-to-now progress table. Also added friendly `fit_method` aliases
("convex hull" etc.) so natural spellings in measure_config.json resolve.

## 2026-07-28 — Diameter measurement: speck filtering + config polish

**Context.** The `min-enclosing` fit method was enclosing *every* white pixel of
the binary mask. On real captures a small speck (e.g. timestamp-text remnants,
sensor noise) far from the wheel pulled the enclosing circle outward and
inflated the diameter — one Model 2 frame read **569 mm against a 496 mm spec**,
roundness 0.731, with the overlay showing the green hull jutting up to a
top-right blob.

**Change — min-enclosing now drops fragments < 2% of the largest.**
`_mask_hull()` in `app/inference/measure.py` previously did
`convexHull(all points)`; it now delegates to `_component_hull()`, applying the
same `_FRAGMENT_MIN_RATIO = 0.02` filter the `hull` method already used. Distant
specks are removed before the enclosing circle is fitted.

- Verified on 6 Model 2 frames: `min-enclosing` went from ~569 mm to **~478 mm**,
  and now matches `hull` to < 0.1 mm.
- Consequence: with the filter applied, `min-enclosing` and `hull` are currently
  **equivalent** (both = min enclosing circle over the convex hull of the
  significant fragments). The two config values are kept as distinct code paths
  so behaviour can diverge again without a config schema change, but they
  produce the same number today. Either value is safe; `min-enclosing` is the
  default.

**Files touched today.**
- `app/inference/measure.py` — `_mask_hull()` now filters via `_component_hull()`;
  updated the method-selection comment.
- `measure_config.json` — operator sets `threshold_value: 80` (fixed) and
  `fit_method: min-enclosing`.

### Earlier the same day — measurement config file + diagnostics
- **`measure_config.json`** (new, project root): single place to choose
  `fit_method` (`min-enclosing` | `hull`) and `threshold_method`
  (`otsu` | `fixed` + `threshold_value`). Re-read every inspection — no restart.
  Old value `mask` still accepted as an alias for `min-enclosing`.
- **Terminal diagnostics** per inspection: background subtraction ON/OFF, the
  threshold actually applied (and whether Otsu-auto or fixed), and the fit
  method. Mirrored in the Binary Mask debug window, which also shows the
  **roundness** (flagged `<-- NOT round!` below 0.90) and a colour overlay:
  green = the measured hull, red = the fitted min enclosing circle, red cross =
  its centre.
- **"Measure diameter" master checkbox**: unchecked ⇒ pure recognition, no
  masking/measurement at all. Persisted as `measure_diameter` in settings.json.
- **Batch run**: added Detected/Spec/Diff (mm), Threshold, roundness and
  area-equiv columns; a **Stop** button; and `QApplication.processEvents()` so
  the window no longer shows "not responding" mid-run.

### Findings recorded today (not code)
- `pixel_to_mm = 0.885` validated: Model 9 measures 492.7 mm vs 491 spec
  (+0.3%); Model33 measures 336 mm vs 332 spec (+1.3%).
- **Model 2's spec (496 mm) is wrong** — three independent checks (bbox,
  threshold sweep, clean-mask overlay) put it at ~478–482 mm. Verify with
  calipers and correct in Options → Model data (that number goes to the PLC).
- **Threshold sweep**: 75–80 is the stable plateau; below 60 the mask leaks and
  inflates, above 90 it clips. Keep the fixed value in this range.
- **Diameter separation limit**: measurement repeatability is ±3.9 mm (big
  wheels) / ±1.7 mm (small). Two same-pattern models can only be told apart by
  diameter if they differ by **> ~30 mm**. Tell the client "±15 mm" as the
  guaranteed accuracy envelope, and that diameter disambiguation needs ≥30 mm
  separation.
