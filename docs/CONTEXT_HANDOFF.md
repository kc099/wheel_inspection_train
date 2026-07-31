# Context Handoff — Wheel Inspection System

A knowledge-transfer document for whoever (person or AI) continues this project.
It captures the **non-obvious** decisions, the current state, the traps we hit,
and what's still open — the things that aren't visible from reading the code
alone. For the polished, senior-facing overview see
`docs/PROJECT_DOCUMENTATION.md`; for the dated change history see
`docs/work_log.md`.

---

## 0. Read these three files together

| File | Purpose |
|---|---|
| `docs/PROJECT_DOCUMENTATION.md` (+ .html) | Polished report — what the system is, for a manager/senior |
| `docs/work_log.md` | Dated log of every change (newest first) |
| `docs/CONTEXT_HANDOFF.md` (this) | Engineering brain-dump — decisions, traps, open items |

The `.html` report is generated from the `.md` by `python docs/build_docs.py`.
**Edit the `.md`, never the `.html`.**

---

## 1. What it is + current status

A PySide6 desktop app: an overhead IP camera watches wheels on a conveyor; a PLC
triggers over serial; the app **recognises which wheel model** it is (PatchCore
anomaly model) and optionally **measures its diameter**, then replies to the PLC
with a 13-byte frame (model id + entered height + entered diameter).

**Status:** recognition works well (~98% on curated tests). Diameter measurement
works and is accurate (±0.3–1.3% vs spec on validated models) but its
**disambiguation feature is not yet proven on production data** — see §5, §7.
Deployment path (conda launcher + PyInstaller) is set up.

---

## 2. The stack

Python 3.10, conda env **`depth`** (prod) / **`dl`** (dev). PySide6 6.11,
torch 2.12 CPU, anomalib 2.5, timm 1.0, kornia 0.8, lightning 2.6, opencv 4.13,
numpy 2.2, pyserial, pillow. See `docs/PROJECT_DOCUMENTATION.md` §3–4 for the
full dependency table and file-structure map.

---

## 3. Key decisions and WHY (not obvious from the code)

- **PatchCore, recognition-only.** No labelled defect data, models added often →
  anomaly-detection used for recognition (lowest anomaly / highest confidence
  wins). No OK/NG grading.
- **ResNet-50, layers `layer2 + layer4`.** Mid + high-level features tolerate
  pose/rotation. Was `layer2+layer3`; switching to `layer2+layer4` (the proven
  `Assets/train.py` recipe) improved robustness. **The engine auto-detects a
  checkpoint's layers from its memory-bank width** (1536→layer2+3, 2560→layer2+4)
  so old and new checkpoints both load — see `engine.py:_load_module`.
- **`coreset_sampling_ratio = 0.1`.** Keeps 10% of patch features. Briefly ran
  0.5 → 500 MB checkpoints, 5× slower, zero accuracy gain (score margins are
  huge). `trainer.py:CORESET_RATIO`.
- **Validation = a COPY of the uploads (`SAME_AS_TEST`).** The old
  `test_split_mode="synthetic"` secretly held ~20% of uploads OUT of the memory
  bank, and exactly those images then failed. Now nothing is held out.
- **Calibration widened: `image_max = 2 × image_min` at save time.** With an
  all-good validation set, anomalib's raw min/max were nearly equal → every real
  part read ~0% confidence. Widening gives confidence a usable range.
  `trainer.py` (post-fit, before `torch.save`).
- **Rotation augmentation.** 10–25 uploads + 90/180/270° copies up to 60. Real
  pose diversity still matters more; augmentation just fills gaps.
- **Backbone loaded with `pre_trained=False` at inference.** The checkpoint has
  every weight; downloading ImageNet weights from HF Hub was waste AND crashes
  on an offline machine.

---

## 4. The recognition journey (≈60% → ≈98%)

The jump came from stacking fixes, all on 16 Jul: layer2+4, clearing the stale
model cache on retrain, stop holding uploads out of the bank, calibration
widening, and **curated diverse training images** (the single biggest lever —
20–25 images spanning real poses beat 100 near-duplicates). Full story in
`docs/recognition_tuning_journey.md` and `work_log.md`.

---

## 5. Diameter measurement + the tie-break saga (READ THIS)

**Pipeline** (`measure.py`, on the full 1280×720 frame): background subtraction →
grayscale → blur → threshold (fixed 75) → morphology open/close → find contours →
drop fragments <2% → convex hull → edge-reject → `minEnclosingCircle` → ×0.885 mm.
Both `fit_method` values (`min-enclosing`, `hull`) end in the same
convexHull+minEnclosingCircle; today they're equivalent (both drop <2% specks).

**The tie-break logic changed hands three times — current version is the one to keep:**

1. **Original (mine):** refine a tie — among models past the 0.30 gate with a
   stored diameter, pick nearest. Failed in practice ONLY because all models had
   `pixel_diameter = 0` (trained before the feature) → no candidates → never ran.
2. **Codex's rewrite:** ignored appearance confidence, could OVERRIDE a confident
   match on one noisy reading, and left a dead "reject" branch. Riskier. Saved as
   `verdict_old.py` / `main_window_old.py`.
3. **Current (refine-only + `tie_confidence`):** `verdict.py:pick_by_diameter`
   runs the size check **only when 2+ models score ≥ `tie_confidence` (default
   0.5) AND have a stored `pixel_diameter`**, then picks nearest within
   tolerance. **Never rejects, never overrides a lone confident match.** Rationale:
   recognition resizes to 256×256 which ERASES size, so two same-pattern wheels
   both score high — that "both high" is the trigger to use size.

**Why this is safe:** it can only break a tie you already have; it cannot turn a
correct confident classification into a wrong one.

---

## 6. Magic numbers and where they live

| Number | Meaning | Location |
|---|---|---|
| `0.885` | mm per pixel (rig calibration) | settings.json `pixel_to_mm` |
| `75` | fixed mask threshold (from a sweep; 75–80 is the stable plateau) | measure_config.json `threshold_value` |
| `3.0` | diameter tolerance % | measure_config.json `diameter_tolerance_pct` |
| `0.5` | `tie_confidence` — 2+ models above this ⇒ use size | measure_config.json `tie_confidence` |
| `0.30` | recognition gate (below ⇒ "not a known model") | settings.json `recognition_min_confidence` |
| `0.1` | coreset ratio | trainer.py `CORESET_RATIO` |
| `10 / 25 / 60` | min uploads / max uploads / max after rotation | trainer.py |

---

## 7. Known limits & honest caveats (don't oversell to the client)

- **Diameter can only separate models >~30 mm apart.** Measurement noise is
  ~±4 mm typical, ±10–15 mm worst. Two models 5 mm apart **cannot** be separated
  by diameter on this camera — recognition or a hardware gauge must. Quote the
  client **±15 mm** measurement accuracy, not tighter.
- **The client said their two look-alike models differ by only ±5 mm.** That is
  below the noise floor → diameter won't disambiguate them. The real test is
  whether PatchCore's *appearance* separates them (it did for the earlier 9-vs-2
  pair). Run that test first.
- **Model 2's entered spec (496 mm) is almost certainly wrong** (measures
  ~478–482). Verify with calipers and fix in Options → Model data — that number
  goes to the PLC.
- **All current models have `pixel_diameter = 0`** → the tie-break is inert until
  they're retrained on the production machine with a background reference set.

---

## 8. Config files

- **`settings.json`** → `app`: `pixel_to_mm`, `recognition_min_confidence`,
  `mask_threshold` (fallback), `min_training_images`, plus modbus + camera URL.
  `measure_diameter` also lingers here but is now **unused** (moved to
  measure_config) — safe to remove.
- **`measure_config.json`** (the measurement control panel, read live each
  inspection, no restart): `measure_diameter` (master on/off), `tie_confidence`,
  `diameter_tolerance_pct`, `fit_method` (`min-enclosing`|`hull`),
  `threshold_method` (`otsu`|`fixed`) + `threshold_value`, `show_binary_mask`,
  `show_heatmap_overlay`. **The UI checkboxes were removed — this file is the
  only way to toggle these now.**

---

## 9. Deployment

- **`WheelInspection.bat`** — double-click launcher; activates conda `depth`
  (falls back to `dl`), runs `main.py`. Auto-start on power-on = Windows
  auto-login + Startup-folder shortcut. See `docs/build_windows_exe.md`.
- **PyInstaller:** build with `pyinstaller WheelInspection.spec --noconfirm`
  (NOT a bare command — the spec has the `--collect-all` for torch/anomalib/etc.
  a naive build misses). Then copy `Assets`, `models`, `settings.json`,
  `measure_config.json` beside the exe.
- **`console=False`** in the spec (no terminal window). `main.py` then redirects
  all `print()` to **`WheelInspection.log`** next to the exe (UTF-8, ~5 MB
  rollover). Dev runs still print to screen.
- **Data lives beside the exe** via the frozen-aware `ROOT` in `app/core/paths.py`
  (`history.db`, `users.json`, `settings.json`, `measure_config.json`, `models/`,
  `WheelInspection.log`). Upgrades: replace exe + `_internal` only.

---

## 10. Traps that bit us (so you don't repeat them)

- **Stale model cache:** retraining under the same name kept serving old weights
  until the cache was cleared. `engine.invalidate_cache()` on `models_changed`.
- **PyInstaller misses dynamic imports** (torch/anomalib/timm/kornia) → use the
  `.spec` with `collect_all`, never a bare `pyinstaller main.py`.
- **`console=False` + `print()`** → windowed exe has `sys.stdout = None`; naive
  print can crash. Handled by `main.py` redirect (`_init_logging`).
- **Non-ASCII in logged strings** (`≥`, `—`) crash a cp1252 Windows console via
  `print()`. Keep log/reason strings ASCII (verdict.py uses `>=` not `≥`).
- **`min-enclosing` fit encloses ALL mask pixels** → a distant speck (timestamp
  text) inflated a wheel to 569 mm. Fixed by the 2% fragment filter now applied
  to both fit methods.
- **build_docs.py** originally split multi-line bullets into loose paragraphs;
  fixed to join continuation lines.
- **Heatmaps / training scratch used to write to disk unbounded** → would fill
  the production disk. Now heatmap is in-memory only; training scratch is in a
  temp dir wiped after fit.

---

## 11. Open items / TODO

1. **Retrain the production models with a background set** so they get a real
   `pixel_diameter` — the tie-break is inert until then.
2. **Verify Model 2's diameter with calipers** and correct its spec.
3. **Test whether PatchCore alone separates the client's two same-pattern models**
   (the ±5 mm pair) — the diameter route can't.
4. Decide whether to **delete `verdict_old.py` / `main_window_old.py`** backups.
5. Remove the now-unused `measure_diameter` key from `settings.json`.
6. Optional: file-drift check so the doc's file-map can't silently go stale.

---

## 12. Test data situation

No production wheel images are checked in (capture folders were deleted).
`sample_dataset/` (metal nuts) and `vial_sample/` are **generic anomalib demo
data**, not the real wheels. To test the tie-break end-to-end you need real
images of two same-pattern-different-size wheels from the actual camera, OR a
synthetic two-size set (offered but not built). A trained `weights.pt` is a
feature memory bank — **it does NOT contain the training images.**

---

## 13. Maintaining the docs

Edit `docs/PROJECT_DOCUMENTATION.md` (source of truth) → run
`python docs/build_docs.py` → regenerates the self-contained `.html` (embeds
screenshots as base64 and the SVG flowchart inline; no external files needed to
share it). Add a dated entry to `docs/work_log.md` for any pipeline change.
The flowchart source is `docs/pipeline_flowchart.svg`.

---

## 14. Backups currently present

`app/core/verdict_old.py` and `app/ui/main_window_old.py` are the pre-Codex
tie-break versions, kept for reference. `dist/` may hold an older PyInstaller
build. Remove when confident.
