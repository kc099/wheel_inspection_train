# Resume Entry — Wheel Inspection System

Copy-paste material for putting this project on a resume / CV / LinkedIn.

> **Accuracy note — read before using these bullets.** The shipped app is
> **recognition-only**: it decides *which* wheel model a part is, and there is no
> OK/NG defect threshold in the code (`app/core/verdict.py` and
> `app/inference/engine.py` both say so explicitly). The OK/NG defect language in
> `README_PYTHON_PORT.md` describes the older WPF spec, not what was built. The
> wording below reflects the actual behaviour — do not reintroduce "defect
> detection" claims unless the feature is actually added back.

---

## Project name

**Automated Wheel Recognition & Inspection System**

With a subtitle, if the format allows:

> *Vision-based wheel-model recognition for a PLC-controlled production line*

Alternatives:

- Vision-Based Wheel Recognition (Desktop AI Application)
- AI Wheel Identification & Sorting System

---

## One-line description

> Desktop application that identifies which wheel model a part is from a camera
> image using unsupervised anomaly-detection models, and reports the recognized
> model's dimensions to a PLC over serial.

---

## Resume entry (3–4 lines)

**Automated Wheel Inspection System** — Python, PySide6, PyTorch, anomalib (PatchCore), pySerial

- Built an end-to-end desktop inspection application that identifies wheels by
  scoring each image against an extensible set of trained PatchCore models,
  returning the recognized model, confidence, anomaly heatmap, and per-model
  match breakdown, with a novelty gate that rejects unknown parts outright.
- Integrated the app with plant hardware over a Modbus-style serial link — a
  background thread detects PLC trigger frames and replies with a 13-byte
  measurement frame carrying diameter, height, model ID, and match status.
- Diagnosed and fixed a model-recognition pipeline that was rejecting genuine
  parts, raising recognition accuracy from ~60% to ~98% by correcting score
  calibration and the confidence gate; added a batch-test harness with CSV export
  and misclassification review to drive the analysis.
- Shipped it as a standalone Windows executable (PyInstaller) with user
  authentication, role-based access, in-app model training, and SQLite inspection
  history.

---

## Variant — software-weighted (~70% engineering / ~30% ML)

Use this when applying to software / application / systems roles, where the
build-and-ship work matters more than the modelling.

**Automated Wheel Inspection System** — Python, PySide6/Qt, pySerial, SQLite, PyInstaller, PyTorch

- Designed and built a multi-window PySide6 desktop application for shop-floor
  operators — live inspection dashboard, model-data management, batch testing,
  activity log, and Modbus configuration — backed by a single shared app-state
  layer so every screen stays in sync.
- Integrated the app with plant hardware over a Modbus-style serial link: a
  background thread detects PLC trigger frames with cooldown and re-entrancy
  guards, then replies with a 13-byte measurement frame carrying diameter,
  height, model ID, and pass/fail.
- Added the production concerns around the core loop — user authentication with
  role-based access, SQLite inspection history, persistent settings, and a
  batch-test harness with CSV export and misclassification review — and packaged
  it all as a standalone Windows executable via PyInstaller.
- Wired in the inference layer (anomalib PatchCore checkpoints running
  in-process) behind a single shared verdict module, so single-image and batch
  inspection can never disagree; tuned score calibration to lift recognition
  accuracy from ~60% to ~98%.

---

## Tailoring notes

- **Cutting to 3 bullets:** drop the last one. Keep the accuracy bullet — it has a
  concrete before/after number and shows debugging depth, which is rarer on a
  resume than "built a UI."
- **ML-focused roles:** lead with the PatchCore / anomaly-detection bullet.
- **Software or embedded roles:** lead with the PLC serial-integration bullet.
- **Interview prep:** the ~60% → ~98% claim is backed by
  [recognition_tuning_journey.md](recognition_tuning_journey.md). Be ready to
  explain why PatchCore has no epochs and cannot overfit — that doc is the prep
  material, and it is a memorable thing to be able to explain.

---

## Under-sold work worth adding

Things the project actually does that neither bullet set currently mentions. Swap
one in if it fits the role better than what's there.

- **In-app model training with rotation augmentation.** An operator can train a
  new wheel model from ~10 good photos without touching code: the trainer adds
  90/180/270° rotated copies, turning 10 uploads into 40 training samples for a
  fuller memory bank ([trainer.py](../app/inference/trainer.py)). Strong bullet —
  it is a real product feature, not plumbing, and shows you designed for a
  non-technical user.
- **Live IP-camera streaming.** MJPEG/HTTP frames read on a background thread and
  handed to the UI as `QImage` via Qt signals, with OpenCV as an optional
  dependency so the app still runs without it
  ([camera.py](../app/comms/camera.py)).
- **Graceful degradation as a design stance.** No serial port → trigger detection
  stays off. No OpenCV → no live stream. No network → `pre_trained=False` avoids
  a doomed weight download. The app always starts. Worth stating once explicitly;
  it reads as production maturity.
- **Designed for offline production machines.** Nothing assumes internet at
  runtime. Interviewers notice this because most portfolio projects assume it.
- **Storage architecture for scale.** [model_storage_design.md](model_storage_design.md)
  weighs per-model folders vs. SQLite for ~100 models at ~39 MB of weights each
  (≈3.9 GB). Note it is a proposal, not shipped — describe it as a design
  document, never as built.

### Suggested extra bullet, if you have room

> Added operator-facing model training that fits a new wheel model from ~10
> photos in-app — with automatic rotation augmentation to quadruple the sample
> count — so the plant can onboard a new wheel type without a developer.

---

## Numbers to fill in before sending

These would strengthen the entry, but only you know them. Do not invent any.

- **Inference latency** — seconds per inspection, and how it scales with model
  count (every registered model runs on every image, so this matters).
- **Throughput / production context** — is it deployed? How many inspections per
  shift? "Deployed on a production line at <company>" outranks every technical
  detail on this page.
- **Test-set size** behind the ~98% — "98% on a 30-image batch" is honest and
  still good; an unqualified "98%" invites the question you least want.
- **Model count in production** and the timeline / team size (solo? how long?).
- **A repo or demo link**, if it can be shared. If the code is client-owned, say
  so rather than omitting it silently.

---

## Interview questions to expect

Grouped by what the interviewer is probing. The short answers are the shape of a
good reply, not a script — say them in your own words.

### A. The ML approach

1. **"Why anomaly detection instead of a normal image classifier?"**
   Only good samples exist per wheel model; there is no labelled defect set and
   no balanced multi-class dataset. PatchCore learns "what normal looks like"
   from good images alone, and a new wheel model means training one more memory
   bank rather than retraining a shared classifier head.
2. **"How does PatchCore actually work?"**
   A frozen ResNet-50 extracts patch features from good images into a **memory
   bank**. At inference, each patch of the new image is compared to the bank and
   the distance becomes the anomaly score — lower means more familiar. No
   gradient training.
3. **"How many epochs did you train for?"**
   A trick question worth owning: epochs are meaningless here. One pass builds
   the bank, and anomalib force-overrides `max_epochs` to 1 for PatchCore.
4. **"Can it overfit? What happens if you add more training images?"**
   It cannot overfit in the gradient sense. More good samples only *widen* what
   is accepted; they never narrow it. So when results got worse, the cause was
   always elsewhere — that reasoning is what drove the debugging.
5. **"Why ResNet-50 and layers 2+4 specifically?"**
   Mid-level layers carry texture, deeper layers carry shape. The layer choice is
   recoverable from the checkpoint's memory-bank width
   ([engine.py:69-73](../app/inference/engine.py#L69-L73)), which is how older
   and newer checkpoints both still load.

### B. The recognition logic (the strongest area — know this cold)

6. **"You run every model on every image. How do you pick the winner?"**
   By highest **per-model confidence**, not lowest raw score. Raw scores are not
   comparable across models because each has its own scale; confidence is the
   score normalised by that model's own calibrated min/max range.
7. **"What's the difference between Match % and confidence?"**
   Match % is *relative* — each model's share of total confidence, summing to
   ~100. The novelty gate uses *absolute* confidence. This is exactly why a part
   could read Match % = 100 and still be rejected as unknown: it was the only
   non-zero fit, but a weak one. Two different questions, both answers correct.
8. **"How do you reject a part that isn't any known model?"**
   A novelty gate: the winner's absolute confidence must clear
   `recognition_min_confidence` (0.30). Below it → "Not a known model." A truly
   foreign part clamps to zero on every model.
9. **"Where did the ~60% → ~98% come from?"**
   Score calibration and the confidence gate were wrong; genuine parts were being
   rejected. Walk through [recognition_tuning_journey.md](recognition_tuning_journey.md).
10. **"How did you debug it?"**
    Screenshots were not enough, so CSV export was added to the batch dialog to
    dump per-model raw scores *and* absolute confidences per image. Every
    diagnosis came from those CSVs. This is a good "I built the instrument before
    I fixed the bug" story.

### C. System / architecture design

11. **"How do you keep single-image and batch inspection from disagreeing?"**
    The shared recognition math lives in exactly one module
    ([verdict.py](../app/core/verdict.py)) that both paths call. Duplicated
    scoring logic in the UI would drift.
12. **"How does this scale to 100 models?"**
    An **LRU cache** keyed by model name — loaded on demand, least-recently-used
    evicted at the cap. Checkpoints are ~100–140 MB, so holding all of them
    resident does not scale.
13. **"What happens when a model is retrained under the same name?"**
    The cache must be invalidated, or it keeps serving stale weights until
    restart. `invalidate_cache()` exists precisely for this — a good example of a
    caching bug anticipated rather than shipped.
14. **"How do you keep the UI responsive while models load?"**
    Model warm-up runs on a background `QThread` that signals progress and
    readiness back to the UI; `shutdown()` waits on it so closing mid-load does
    not crash.
15. **"Why in-process inference instead of a separate model server?"**
    The original WPF app needed a subprocess because the UI was C# and the models
    were Python. An all-Python port removes that boundary — no IPC, no JSON-lines
    protocol, no second process to supervise.

### D. Hardware / production concerns

16. **"Walk me through the PLC handshake."**
    Background thread polls the serial port (~10 ms); ≥6 bytes counts as a
    trigger frame; a 4 s cooldown and a 1 s processing lock prevent
    double-firing; the reply is a fixed 13-byte frame with big-endian float32
    height and diameter.
17. **"Why the cooldown and the processing lock — aren't they redundant?"**
    They solve different problems. The lock prevents re-entrant handling of one
    trigger; the cooldown prevents a second physical trigger being accepted too
    soon. Also mention `_inspection_in_progress`, which guards overlapping cycles
    at the app level.
18. **"Your frame doesn't append a CRC. Why?"**
    Honest answer: the firmware's existing frame does not, so the port matches it
    rather than silently changing the protocol. A CRC-16 helper exists for when
    the protocol is revised. Good "match the contract, don't unilaterally break
    it" answer.
19. **"What breaks if the serial port is missing?"**
    The app still starts; trigger detection just stays off. Inspection must not
    be blocked by an absent PLC.
20. **"You stopped writing heatmaps to disk. Why?"**
    Every inspection used to save an overlay PNG, which grows without bound on a
    production machine. Overlays are now rendered in memory only when the UI asks
    for one. A real operational-cost catch.
21. **"Why `pre_trained=False` when loading the backbone?"**
    The checkpoint already holds every weight and the strict load overwrites the
    backbone anyway, so downloading ImageNet weights is wasted — and fails on an
    offline production machine.

### E. Likely weak spots — prepare these honestly

22. **"How do you know it's 98%? What was the test set?"**
    Be precise about sample count and whether it was held out. Do not overstate.
23. **"Is 0.30 tuned or guessed?"** Explain how it was chosen and what happens on
    either side of it (false rejects vs. accepting foreign parts).
24. **"What are the automated tests?"** There is no test suite in the repo. The
    honest answer is that batch runs with CSV export served as the regression
    harness — and that a unit-tested `verdict.py` would be the first thing to add.
25. **"What would you do differently?"** Have a real answer ready: tests around
    the recognition math, calibration stored explicitly rather than inferred from
    checkpoint internals, and per-model confidence thresholds instead of one
    global 0.30.
26. **"What was hardest?"** The Match % vs. absolute confidence distinction — two
    metrics that were both correct yet told opposite stories, which is why the bug
    survived so long.

---

## Tech stack (for a skills line)

Python · PySide6/Qt · PyTorch · anomalib (PatchCore) · torchvision · NumPy ·
Pillow · pySerial (Modbus RTU) · SQLite · PyInstaller
