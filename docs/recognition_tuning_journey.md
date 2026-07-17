# How We Got Wheel Recognition From ~60% to ~98%

*A debugging journey — written for the team, 2026-07-17.*

This doc explains why the recognition system used to reject genuine wheels,
what each root cause was, how we found it, and the final training recipe.
Read this before training new models or changing anything in
`app/inference/trainer.py` / `app/inference/engine.py`.

---

## 1. How the system works (60-second version)

We use **Patchcore** (anomalib) — it is *not* a classifier and has no gradient
training. "Training" = pass good images through a frozen resnet50, store their
patch features in a **memory bank**. At inspection, every patch of the captured
frame is compared against the bank; the distance is the **anomaly score**
(lower = more familiar).

Recognition then works in two steps:

1. **Winner** — the model whose bank fits best (highest per-model confidence).
2. **Novelty gate** — the winner's confidence must reach
   `recognition_min_confidence` (0.30). Below it → "Not a known model".

Confidence is the score normalised by the model's own calibration:
`conf = 1 − (score − image_min) / (image_max − image_min)`, clamped to 0..1.
`image_min/image_max` are stored inside each checkpoint at training time.
**Keep this formula in mind — half of our problems lived in it.**

Because there is no gradient descent, two intuitions from normal ML do *not*
apply here:

- **Epochs do nothing.** One pass builds the bank; anomalib force-overrides
  `max_epochs` to 1 for Patchcore. We verified this in the library source.
- **It cannot overfit.** Extra good samples only *widen* what is accepted;
  they never narrow it. When results got worse, the cause was always
  something else (see §4).

---

## 2. The symptom

A genuine Model1 wheel showed **Match % = 100 for Model1** in the UI, yet
Status said **"Not a known model"**. A 30-image batch run recognized only
18/30.

First lesson: the UI's *Match %* is each model's **share of total confidence**
(relative), while the gate uses **absolute** confidence. When one model is the
only non-zero fit — but a weak one — Match % reads 100% while the gate rejects
it. Both were "correct"; they answer different questions. (This is also why a
truly foreign part shows 0% on all rows: every model clamps to zero.)

To debug with real numbers instead of screenshots, we added **Export CSV** to
the Batch Run dialog. It dumps per-model raw scores *and* absolute confidences
per image. Every diagnosis below came from those CSVs.

---

## 3. Root causes, in the order we found them

### 3.1 The training set didn't cover the real poses
The wheel travels and rotates on the conveyor. Training images covered one
pose family; captures in other positions/rotations scored 24–32 vs ~15 for
trained poses. **Fix:** curate the training folder to span every position and
spoke rotation the live camera actually sees. Diversity beats volume — 20
diverse images outperform 100 near-duplicates.

### 3.2 The calibration span was razor-thin
The first model's stored range was `image_min 22.9 – image_max 23.7` — a span
of 0.8 on a score axis where data ranged 14–32. Confidence was effectively
binary (1.0 or 0.0) and the 0.30 gate degenerated into a hard score cutoff at
~23.5. Tuning the gate was pointless: moving it 0.30→0.15 shifted the cutoff
by ~0.1 score units. This happens when training images are too uniform.

### 3.3 A retrain silently didn't take effect (stale cache)
After retraining, a new batch run produced **bit-for-bit identical scores** —
impossible for new weights. The inference engine caches loaded models by name
and nothing evicted them, so a retrain under the same name kept serving the
old checkpoint until app restart. **Fix:** `models_changed` now clears the
engine cache (`InferenceEngine.invalidate_cache()`); retrains apply on the
next inspection. If results ever look "impossibly unchanged", suspect caching
first.

### 3.4 The validation split was stealing training images
With `test_split_mode="synthetic"`, anomalib held ~20% of the uploads out of
the memory bank for validation. Exactly those held-out images then failed
recognition (scores 17–19 vs ~8 for in-bank images). **Fix:** the trainer now
stages a separate `val/` copy of the uploads and uses
`val_split_mode=SAME_AS_TEST`, so *every* upload (and every rotation) enters
the bank.

### 3.5 The all-normal validation broke the calibration (the sneaky one)
Fixing 3.4 caused the strangest failure: a model with *perfect* coverage
(every image scored ~8, the best yet) rejected **25/25 genuine parts**. Why:
`image_min/image_max` are computed from validation scores. With an all-normal
validation set the range became 7.26–7.90 — so any real capture at 8.0+ was
"above the worst score ever seen" → confidence 0. Great bank, broken ruler.
**Fix:** after training we widen the stored `image_max` to `2 × image_min`.
Result: genuine parts ≈ 0.8–0.9 confidence, unseen poses ≈ 0, foreign wheels
(scores ~48–54) pinned at 0. The margins are huge — genuine ~8 vs foreign
~48 — so this simple rule is robust.

### 3.6 Feature layers matter
The original recipe embedded resnet50 `layer2+layer3`. The proven standalone
script (`Assets/train.py`) used `layer2+layer4` — layer4 features are more
global/semantic and tolerate position/rotation shifts better. We adopted
layer2+layer4. The engine auto-detects a checkpoint's layers from its
memory-bank width (1536 → layer2+3, 2560 → layer2+4), so old and new models
both load.

---

## 4. Training time vs accuracy

The slowest training step is **greedy coreset selection**, which scales with
(bank size × candidates × feature dim) — not the epochs (always 1) and not
the backbone forward pass. Two knobs control it:

- `CORESET_RATIO` (0.1): fraction of patch features kept. We briefly ran 0.5,
  which produced a 500 MB checkpoint and ~10× slower training/inference for
  no measurable accuracy gain — the winning margins (8 vs 48) dwarf any bank
  refinement. Coverage must come from **diverse images**, not hoarded patches.
- `MAX_TRAIN_IMAGES` (60): total training set = up to 25 uploads + rotated
  copies (90/180/270°, spread evenly) topping up to 60.

Backbone stays **resnet50** everywhere (training and inference must match —
the engine's layer detection handles the layer choice, not the backbone).

---

## 5. The final recipe (current code)

| Setting | Value | Where |
|---|---|---|
| Backbone / layers | resnet50, layer2+layer4 | `trainer.py` `LAYERS` |
| Coreset ratio | 0.1 | `trainer.py` `CORESET_RATIO` |
| Min uploads | 10 | `settings.json` `min_training_images` |
| Max uploads | 25 | `trainer.py` `MAX_UPLOAD_IMAGES` |
| Total training images | ≤ 60 (uploads + rotations) | `trainer.py` `MAX_TRAIN_IMAGES` |
| Validation | separate copy of uploads, `SAME_AS_TEST` — nothing held out | `trainer.py` |
| Calibration | `image_max` widened to `2 × image_min` at save | `trainer.py` |
| Epochs | 1 (forced by Patchcore; do not "increase") | — |
| Recognition gate | 0.30 absolute confidence | `settings.json` |

**Measured result:** ~98% correct recognition on live captures, genuine-part
confidences ~0.8+, foreign models at 0.

## 6. How to train a good model (operator checklist)

1. Capture **10–25 images through the same HTTP camera / framing / lighting
   used at inspection** — never from a different source (domain gap).
2. Make the set **span every position and rotation** the wheel shows on the
   conveyor. A short capture session while the part moves naturally, sampled
   every few frames, is ideal.
3. All images must be **known-good parts** of that one model.
4. To retrain an existing model: Options → model data → delete it, then Train
   under the same name. (The engine cache is cleared automatically.)
5. Verify with **Batch Run → Export CSV** on a fresh capture folder: genuine
   parts should sit ~0.8+ confidence, `matched=True`. If a pose still fails,
   add captures of *that pose* to the training folder and retrain — don't
   lower the gate.

## 7. Debugging playbook (when recognition misbehaves)

- **Match % high but "Not a known model"** → absolute confidence below the
  gate; check `winner_confidence` in the CSV, not Match %.
- **Identical scores after a retrain** → the retrain didn't reach inference
  (cache / model wasn't replaced). Restart or check `invalidate_cache`.
- **Everything rejected but scores look great** → calibration
  (`image_min/image_max` in the checkpoint) — see §3.5.
- **One cluster of images fails, timestamps grouped** → a pose/lighting the
  bank hasn't seen; look at those exact images side-by-side with passing ones.
- Always start from a Batch Run CSV. Screenshots hide the numbers that matter.
