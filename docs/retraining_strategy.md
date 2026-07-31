# Model Retraining Strategy

**Status:** working strategy (for the team). Explains *why* the current models
recognize inconsistently, and the procedure to retrain them so recognition is
stable and "not a known model" (NA) is reliable.

No code changes here — this is how to (re)build the model set correctly.

---

## 1. Why retrain — the problem we measured

The app recognizes a part by running the image through every model and picking
the one it fits best. "Fits best" is measured by **confidence** — each model's
raw anomaly score normalized into 0–1 by *that model's own* normal range:

```
confidence = 1 − (score − image_min) / (image_max − image_min)   # clamped 0..1
```

For this to work across models, every model's normal range must mean the same
thing. **Right now it doesn't.** Measured calibration:

| Model | normal range (min → max) | width |
| --- | --- | --- |
| model1 | 19.79 → 20.21 | **0.42** |
| model3 | 18.13 → 18.23 | **0.10** |
| model5 | 18.02 → 18.15 | **0.13** |
| model_6 (app-trained) | 26.0 → 36.8 | **10.8** |
| Model_7 (app-trained) | 24.6 → 30.0 | **5.4** |

- **model1–5** have a near-zero range (0.1–0.4). A range that tight means *any*
  real image scores "off the end" → confidence ≈ 0 → they can barely recognize
  even their own parts, yet they still add noise to the comparison.
- **model_6 / Model_7** have a wide range (5–11), so they behave very
  differently.

Two sets of models calibrated on totally different scales → the "which model
fits best" comparison is unreliable → recognition looks random, and NA can't be
trusted. **The cure is to (re)build every model through one consistent
pipeline** — the app's own Train — so all ranges are comparable.

---

## 2. The goal

After retraining, every model should satisfy:

1. **Same pipeline** — trained via the app (Patchcore, resnet50 backbone,
   layers layer2+layer3, synthetic threshold calibration). This makes the
   normal ranges comparable, so confidence means the same thing everywhere.
2. **A "healthy" normal range** — wide enough to cover the genuine variation of
   good parts (lighting flicker, small position/rotation changes) but nothing
   more. Then:
   - a genuine part scores near `image_min` → **high confidence** → recognized;
   - a foreign part / other wheel type scores past `image_max` → **low
     confidence** → NA.

> Range too tight (like model3's 0.10) → genuine parts wrongly rejected as NA.
> Range too wide (like model_6's 10.8) → foreign parts wrongly accepted.
> The range is set by your training images, so **the data is the lever.**

---

## 3. Core principles

1. **One model per distinct wheel type.** Never mix two wheel types into one
   model's training folder — that widens the normal range and blurs recognition.
2. **Train from the production camera.** Collect the good-sample images from the
   *same* HTTP camera and framing used at inspection time (`http_streamer` in
   settings.json). Patchcore compares appearance; training on a different source
   than inference creates a domain gap that wrecks scores.
3. **Good samples only.** Patchcore learns "what normal looks like" from good
   parts. Every training image must be a **known-good** part of that type.
4. **Consistent capture conditions.** Same lighting, distance, background, and
   camera settings as production. Variation in the images should come only from
   the part itself (position, rotation, unit-to-unit differences), not the rig.
5. **Enough images.** Minimum is 10 (the `min_training_images` setting), and up
   to 25 uploads are used — `MAX_UPLOAD_IMAGES` in `app/inference/trainer.py`;
   extras beyond 25 (name-sorted) are ignored. Rotated copies (90/180/270°) top
   the set up to `MAX_TRAIN_IMAGES` (60) total, and none of them is held out of
   the memory bank (the validation set is a separate copy of the uploads).
   Diversity beats volume: the uploads should span every position and rotation
   the wheel shows on the live camera, not 25 near-identical frames.

---

## 4. Step-by-step procedure (uses the app you already have)

### A. Collect a dataset per wheel type (using the camera + Capture)
1. **Options → nothing needed;** on the main screen press **Start Stream** to
   bring up the live camera.
2. Place a **known-good** wheel of type *X* under the camera.
3. Press **Capture** — the frame is saved to `Desktop/WheelCaptures/`.
4. Repeat 20–25 times, each time introducing the *natural* variation you expect
   in production: rotate the wheel a little, nudge its position, let normal
   lighting flicker happen, swap in different good units of the same type.
5. Move that batch of PNGs into a clearly named folder, e.g.
   `Desktop/train/typeX/`. One folder per type.

> This is exactly why Capture saves to a Desktop folder — those captures *are*
> your training set, taken through the real camera.

### B. Train the model
6. **Train button → Train New Model window.**
7. Enter the **name** (e.g. `typeX`), the **diameter** and **height** (both must
   be > 0), then **Upload Folder…** and pick `Desktop/train/typeX/`.
8. Press **Train**. When it finishes, choose **restart** (or continue — the
   model is live immediately).
9. Repeat A–B for every wheel type.

### C. Retire the old, inconsistent models
10. Once the retrained models cover the types you need, remove the mis-calibrated
    originals (`model1–5`) so they stop polluting the comparison. *(There's no
    Delete button in the UI yet — ask and I'll add one; until then delete the
    `models/<name>/` folder.)*

---

## 5. Diameter measurement (disambiguating same-pattern models) — PLAN

**Problem.** Patchcore compares *appearance*. Two wheel types with the same
spoke pattern but different diameters look nearly identical to it, so both
recognise as one model. We add a geometric check — measure the wheel's actual
diameter — and use it to pick between look-alike candidates.

**The classification pipeline is unchanged.** Diameter measurement runs
alongside it and only *refines* the winner.

### 5.1 The measurement pipeline

Per inspected frame:

1. **Background subtraction** — `cv2.absdiff(frame, background)` against a
   stored reference photo of the **empty conveyor**. Unchanged pixels (rollers,
   rails, fixed shadows) fall to ~0; the wheel stands out.
2. **Threshold** — grayscale, then Otsu threshold → binary mask
   (white = component).
3. **Morphology** — `MORPH_OPEN` (kill speckle) then `MORPH_CLOSE` (seal the
   spoke gaps so the wheel becomes one solid blob).
4. **Largest contour** — `findContours`, keep the biggest by area. This also
   discards the burned-in timestamp text, which *does* appear in the diff
   because it changes every second.
5. **Fit a circle** — `cv2.minEnclosingCircle` → centre + **radius in pixels**;
   pixel diameter = 2 × radius.

### 5.2 Pixels → millimetres

Two independent sources of scale; we use both.

**a) The calibrated factor.** The previous rig was calibrated to
`pixel_to_mm = 0.885` (from the reference project's `config.yaml`). It goes in
`settings.json` so it can be re-tuned without a code change:

```
mm = diameter_px * pixel_to_mm
```

> **Validity:** 0.885 is only correct while the camera keeps the same mounting
> height, lens, and stream resolution as when it was calibrated. Re-derive it
> after ANY camera change — photograph a wheel of known diameter and compute
> `known_mm / measured_px`.

**b) Per-model pixel diameter (the primary comparator).** At training we also
measure the median pixel diameter of the uploads and store it as
`pixel_diameter` in `meta.json` next to the operator-entered `diameter`.

Matching is done **pixel-to-pixel** against these stored values, not in mm.
That way the tie-break stays correct even if the absolute mm factor has drifted
— the same camera measures both the training images and the live frame, so the
error cancels. The mm value is used for display and the PLC frame.

### 5.2b Files touched (IMPLEMENTED)

| File | Change |
|---|---|
| `app/inference/measure.py` | **new** — masking, hull, circle fit |
| `app/ui/dialogs/background.py` | **new** — capture/upload reference dialog |
| `app/ui/dialogs/mask_view.py` | **new** — binary-mask debug window |
| `app/core/paths.py` | `CAPTURES_DIR`, `BACKGROUND_DIR`, `BACKGROUND_PATH` |
| `app/core/models.py` | `ModelData.pixel_diameter`; 3 new `AppSettings` fields |
| `app/core/verdict.py` | `pick_by_diameter()` — the tie-break |
| `app/core/registry.py` | persists `pixel_diameter` in `meta.json` |
| `app/core/state.py` | `save()` for in-place settings tweaks |
| `app/ui/main_window.py` | threshold slider/box, mask checkbox, Options entry, Diameter row, `_measure_diameter()` |
| `app/inference/trainer.py` | measures uploads → `pixel_diameter` |
| `app/ui/dialogs/train.py` | passes settings in, stores the measurement |

**Verified** against synthetic circles of known size: 300/400/500/550 px all
measured exactly; empty conveyor and off-frame parts correctly rejected; the
tie-break picks the right model and abstains when nothing is in tolerance.

### 5.2c Flow A — background setup (once per rig)

```
Options ─► "Background reference"          [main_window._open_background]
             │                              developer login required
             ▼
     ╱───────────────╲
   ╱  capture or       ╲── upload ──► file picker ──┐  [BackgroundDialog._upload]
     ╲   upload?      ╱                             │
       ╲───────────╱   [BackgroundDialog]           │
             │ capture                              │
             ▼                                      │
   ┌────────────────────┐                           │
   │ grab live frame    │  [_capture; reads         │
   │ (stream must be on)│   main_window._latest_qimage]
   └─────────┬──────────┘                           │
             ▼                                      ▼
   ┌──────────────────────────────────────────────────┐
   │ preview + "Is the conveyor EMPTY?" confirm       │  [_save]
   └─────────┬────────────────────────────────────────┘
             │ yes
             ▼
   save  Desktop/WheelCaptures/background/background.png
         [paths.BACKGROUND_PATH — a SUB-folder, so bulk-deleting
          captures for a new training set cannot destroy it, and
          it never gets swept into a training upload]
             │
             ▼
   main_window reloads it into self._background  [measure.load_background]
```

### 5.2d Flow B — training gains one step

```
Train dialog ─► build_training_set() ─► Patchcore fit ─► save weights
[TrainDialog]   [trainer.build_          [TrainThread.run]
                 training_set]
                        │
                        ▼  (new step, inside TrainThread.run)
        ┌──────────────────────────────────────────┐
        │ measure each upload, take the MEDIAN      │
        │ [measure.measure_folder(...)]            │
        │ median so one bad mask can't skew it     │
        │ never fatal: on error → 0.0              │
        └──────────────────┬───────────────────────┘
                           ▼
              TrainThread.pixel_diameter
                           │
                           ▼  [TrainDialog._on_done → registry.add_model]
              meta.json: { diameter: <mm>,
                           pixel_diameter: <px> }
                           │
                           ▼
        pixel_diameter == 0  ⇒  this model simply skips the
        diameter check (older models keep working untouched)
```

### 5.2e Flow C — inspection

```
        PLC trigger / Run Inspection      [main_window._do_classify]
                    │                      ONE button — no separate
                    │                      "measure" action exists
                    ▼
        ┌───────────────────────┐
        │ engine.classify(      │  ◄── UNCHANGED recognition path
        │   want_heatmap=<box>) │      overlay rendered in memory only
        │ scores, confidences,  │      when "Show heatmap overlay"
        │ winner, matched       │      is ticked  [engine.classify]
        └───────────┬───────────┘
                    ▼
              ╱──────────╲
            ╱  matched?    ╲── No ──► "Not a known model"
              ╲──────────╱            + zero frame to PLC ──► END
                    │ Yes
                    ▼
           ╱─────────────────╲          [main_window._measure_diameter]
         ╱  background set?    ╲── No ──► classifier winner
           ╲─────────────────╱           log "no background reference" ──► END
                    │ Yes
                    ▼
        ┌──────────────────────────────────┐  [measure.measure()]
        │ absdiff(frame, background)       │   removes rollers/rails
        │ → gray + blur                    │
        │ → threshold  (0 = Otsu, else     │   ◄── slider/spin box in
        │    the fixed slider value)       │       Camera Controls
        │ → OPEN 5px  (despeckle)          │       [settings.mask_threshold]
        │ → CLOSE 25px x3 (bridge spokes   │
        │    and roller-overlap bands)     │
        │ → merge fragments → convexHull   │   [_component_hull] arcs would
        │ → minEnclosingCircle → px        │   otherwise measure as the part
        │ → x pixel_to_mm (0.885) → mm     │
        └───────────┬──────────────────────┘
                    │
                    ├──► if "Show binary mask" ticked:
                    │      MaskViewDialog.update_mask(m)
                    │      [dialogs/mask_view.py — separate window]
                    ▼
          ╱──────────────────╲
        ╱  measurement ok?     ╲── No ──► classifier winner
          ╲──────────────────╱          reasons: no contour / empty /
                    │ Yes                "component touches frame edge" ──► END
                    ▼
        ┌────────────────────────────────┐  [verdict.pick_by_diameter]
        │ candidates = models with       │
        │ conf ≥ gate AND                │
        │ pixel_diameter > 0             │
        └───────────┬────────────────────┘
                    ▼
          ╱──────────────────╲
        ╱  ≥ 2 candidates?     ╲── No ──► classifier winner
          ╲──────────────────╱          "nothing to disambiguate" ──► END
                    │ Yes
                    ▼
        ┌────────────────────────────────┐
        │ pick candidate whose stored    │   pixel-to-pixel, so a drifted
        │ pixel_diameter is closest to   │   mm factor cannot mislead it
        │ the measured value             │
        └───────────┬────────────────────┘
                    ▼
          ╱──────────────────╲
        ╱  within tolerance?   ╲── No ──► classifier winner + log
          ╲──────────────────╱          [settings.diameter_tolerance_pct] ──► END
                    │ Yes
                    ▼
        ┌────────────────────────────────┐
        │ FINAL = diameter-picked model  │
        │ log: "classifier said X,       │   [main_window._log]
        │       diameter chose Y"        │
        │ UI: Diameter row under Status  │   [_fill_diameter]
        │ PLC: 13-byte frame             │   [signals.send_measurement]
        └────────────────────────────────┘
```

**Every "No" branch falls back to today's classification result.** Measurement
can only refine the verdict, never make it worse — the current accuracy cannot
regress.

### 5.3 How the verdict combines both signals

1. Patchcore ranks the models as today (unchanged).
2. Measure the frame's pixel diameter.
3. Among models whose confidence clears the gate, pick the one whose stored
   `pixel_diameter` is closest to the measured value, within a tolerance
   (`diameter_tolerance_pct`, default ~5%).
4. If the measurement fails (no background set, no contour found, nothing
   within tolerance) → **fall back to the pure-classification result** and log
   it. Measurement must never make recognition worse than it is today.

### 5.4 Background reference: capture or upload

The reference frame lives at `models/_background.png` (one per rig, not per
model) and is set from **Options → Background reference**:

- **Capture from camera** — clear the conveyor, press capture, confirm the
  preview. Preferred: it is by definition the real lighting and framing.
- **Upload image** — pick a saved empty-conveyor photo, for offline setup.

Re-capture the background whenever the camera is moved, refocused, or the
lighting/rig changes. A stale background is the #1 cause of bad masks.

### 5.4b Is the measurement precise enough? (do this FIRST)

Diameter can only separate two models if their diameters differ by more than
the measurement noise. Establish both numbers before relying on the tie-break:

1. **Required separation.** List the new wheel types and their true diameters.
   The closest pair sets the bar, in pixels:
   `separation_px = (mm difference) / pixel_to_mm`.
2. **Actual noise.** Capture ~30 frames of the *same* wheel and measure all of
   them. The spread (max − min) is the noise floor.
3. **Verdict.** Noise well below the required separation → the tie-break is
   reliable. Noise comparable or larger → diameter can only separate coarse
   families, not near-twins, and those pairs need a different signal.

> Historical caution: on the *previous* wheel set, the look-alike pairs differed
> by only 4–5 mm (≈5 px at 0.885) — below realistic contour noise. Check the new
> types' numbers rather than assuming.

**Estimator choice matters.** `minEnclosingCircle` is fixed by the single
furthest contour point, so one shadow spike shifts it several pixels. The
equivalent-area diameter `2*sqrt(area/pi)` averages over the whole blob and is
substantially steadier. Measure both; prefer area-based unless the circle fit
proves tighter on real data.

### 5.5 Practical notes

- **Conveyor must be empty** for the reference shot — any wheel in it becomes
  part of "background" and gets subtracted away later.
- **A shadow moving with the part** can join the blob and inflate the diameter;
  `MORPH_OPEN` plus the largest-contour rule handles most of it.
- **If the part is ever cut off** at the frame edge, the circle fit
  under-measures — treat "contour touches border" as a failed measurement.
- **Fallback without a background:** these wheels are light on a dark rig, so
  plain Otsu thresholding often works alone. Background subtraction is the more
  robust path and the one we implement; simple thresholding is the backup if a
  site cannot maintain a clean reference.

### 5.6 Rollout

1. Set the background reference (§5.4).
2. Retrain — or re-measure — each model so it gains a `pixel_diameter`.
   *(Existing models keep working; without `pixel_diameter` they simply skip the
   diameter check.)*
3. Verify with **Batch Run → Export CSV**: the CSV gains measured pixel/mm
   diameter columns so a whole folder can be checked at once.

---

## 5. Validate & tune (do this after retraining)

For each retrained model, run parts through **Run Inspection** and check:

- **Known-good part of type X** → recognized as `X` with **confidence well above
  0.3** (ideally 0.6+). If a genuine part shows NA, your normal range is too
  tight → add more/representative good images, or lower
  `recognition_min_confidence`.
- **A different wheel type / foreign object** → should be **NA** or recognized
  as the *other* correct model. If a foreign part matches, raise
  `recognition_min_confidence`.

`recognition_min_confidence` lives in `settings.json` under `"app"` (default
0.3). It's the single knob that trades off false-accepts vs false-rejects:

| Symptom | Adjust |
| --- | --- |
| Foreign parts wrongly matched | **raise** it (e.g. 0.4–0.6) |
| Genuine parts wrongly NA | **lower** it, or add more good training images |

Tune it *after* retraining, using the confidence numbers you actually observe on
real parts.

---

## 6. What to do about model1–5

They came from a different pipeline and are mis-calibrated (§1). Options:

- **If those wheel types are still in use** → retrain them via §4 (same as any
  other type) and delete the old versions.
- **If they're demo/obsolete** → delete their folders; they only add noise.

Either way, the end state should be: **every model in `models/` was trained by
this app**, so they all share one calibration.

---

## 7. When to retrain again (triggers)

Retrain a model whenever the thing it learned "normal" from changes:

- camera moved, refocused, or replaced;
- lighting or background changed;
- the good part's appearance changed (new supplier, finish, tolerance);
- you see confidence drifting down on known-good parts over time.

---

## 8. Quick checklist

- [ ] One folder of **known-good** images **per wheel type**, captured from the
      **production camera** (via Start Stream → Capture).
- [ ] **10–25** images per type (25 is the cap — extras are ignored), covering
      only natural good-part variation. Rotations expand these to 60 samples
      automatically. **More is NOT better here — diverse is better.**
- [ ] Train each type via **Train New Model** (name + diameter + height + folder).
- [ ] Remove/retrain the old `model1–5`.
- [ ] Validate: good parts → correct model at high confidence; foreign → NA.
- [ ] Tune `recognition_min_confidence` in settings.json to taste.

---

## Appendix — why consistent training gives comparable confidence

All app-trained models use the **same** frozen resnet50 feature extractor and
the same coreset + synthetic-threshold calibration. So each model's `image_min`
/`image_max` are produced the same way and describe the same kind of "spread of
normal scores." Normalizing by that range (the confidence formula in §1) then
yields a number that means the same thing for every model — which is what lets
the app compare them fairly and pick a winner (and reject NA) reliably. Models
built by a *different* pipeline (model1–5) break that assumption, which is the
whole reason recognition was unstable.
