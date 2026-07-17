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
4. Repeat 25–100 times, each time introducing the *natural* variation you expect
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
- [ ] **40–100** images per type (≥25 minimum), covering only natural good-part
      variation.
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
