# Wheel Inspection — Python Port Spec

A rebuild of the WPF *Wheel Inspection System* in Python, keeping the same screen layout and runtime behavior. This document is the spec: it describes what each screen looks like and what it does, so the Python UI can be built to match.

The original `MainWindow.xaml` and `SignalHandler.cs` are kept under `assets/` as the visual and protocol reference — read them for exact pixel/byte details; read this for intent.

> **Out of scope:** everything triggered by the **Train** button (the training wizard, the training runner, and the `prepare_dataset.py` / `train.py` / `train_new_model.py` pipeline). That is being redesigned separately. The Train button still appears in the layout below as a placeholder, but its behavior is intentionally undocumented here.

---

## 1. What the app does

An operator inspects a wheel: a camera image (uploaded, or later captured live) is classified against a set of trained models. The app decides **OK** or **NG (defect)** by comparing an anomaly score to a threshold, shows a heatmap of where the anomaly is, and sends the recognized wheel's dimensions and pass/fail back to a PLC over a serial (Modbus-style) link. A hardware trigger on the serial port runs the same inspection automatically.

There are two ways an inspection starts:
- **Manual** — operator uploads an image and clicks *Run Inspection*.
- **Triggered** — the PLC sends a trigger frame on the serial port; the app runs the inspection on the currently loaded image and replies with a measurement frame.

---

## 2. Color palette

From `App.xaml`. Reproduce these exactly.

| Role | Hex |
| --- | --- |
| Window / header background | `#C8E6C9` (light green) |
| Card / frame background | `#FFFFFF` |
| Card border | `#AFCDB1` |
| Image area background | `#222222` |
| Title text | `#0052CC` (blue) |
| Primary text | `#1B3A17` |
| Muted text | `#5F6F52` |
| Text on light | `#3A4A2E` |
| Placeholder text | `#BBBBBB` |
| Normal button | `#1B5E20`, hover `#2E7D32`, pressed `#124017`, white text |
| Primary/danger button ("Run Inspection") | `#C62828`, hover `#D84343`, pressed `#A51F1F` |
| Status bar background | `#2E7D32` |
| Status dot — OK / Warn / Error | `#4CAF50` / `#FFA726` / `#E53935` |

Cards have a soft drop shadow and 8px rounded corners; buttons are 5px rounded.

---

## 3. Main window layout

Three horizontal bands: **title bar**, **body**, **status bar**.

### 3.1 Title bar (light green)
- **Left** — a hamburger **☰ Options** menu with items: *Model data*, *Model data view*, *Train new model*, *Batch test*, *Modbus settings*.
- **Center** — title text **"WHEEL INSPECTION SYSTEM"**, large bold blue.
- **Right** — Taurus logo, then a right-aligned date (`dd-MM-yyyy`) and time (`HH:mm:ss`) that tick every second.

### 3.2 Body — three columns
Column widths: left `*`, center `*`, right fixed `280px`. Each column is a white rounded card.

**Column 0 — Camera Capture**
- Header "Camera Capture".
- A dark image area that shows the uploaded image (or, later, the live feed). When empty it shows the placeholder text "No image loaded".

**Column 1 — Result**
- Header "Result".
- **Detection results panel** — a two-column label/value grid:
  - Predicted Model
  - Confidence (`%`)
  - Accuracy (`%`)
  - Diameter (mm)
  - Height (mm)
  - Anomaly Score
  - NG Threshold
  - Status — colored: green OK, amber warning, red NG.
- **All-models breakdown** — a small scrollable table with one row per loaded model: **Model / Score / Threshold / Match %**. The recognized model's row is bold and highlighted. This shows how every model scored the image, not just the winner.
- **Heatmap image area** — the anomaly overlay for the recognized model; placeholder "No result" when empty.

**Column 2 — Camera Controls** (stacked buttons, grouped by labeled section)
- **IMAGE SOURCE**
  - *Upload Image (single)* — opens a file picker; loads one image into Camera Capture.
  - *Upload Batch (100s of images)* — opens the Batch Test window.
- **LIVE CAMERA (COMING SOON)** — *Start Live*, *Stop Live*, *Capture*. Present but not wired; leave as stubs.
- **INSPECTION**
  - *Run Inspection* — the big red primary button. Runs classification on the loaded image.
  - Below it: a small status dot + text ("Loading models…", "N model(s) loaded", or an error) reflecting the model-server state.
- **Train** — green button (placeholder; behavior out of scope, see note at top).

### 3.3 Status bar (dark green)
A colored dot + a status message spanning the bottom. Every meaningful action updates it (e.g. "Loaded image: …", "Classifying image…", "Classified: model3 (91%) — OK", errors in red).

---

## 4. Data model

Mirror these as plain Python classes / dataclasses. In the original they live in a single in-memory store (`AppState`) — no file/DB persistence — so entries are lost on restart. The Python port may add persistence; if not, match the in-memory behavior.

**ModelData** — one wheel model:
- `name: str`
- `diameter: float` (mm)
- `height: float` (mm)
- `threshold: float` — NG anomaly-score threshold. `0` means *unset*: fall back to the checkpoint's own calibrated threshold reported per classification.

**ModbusSettings**:
- `protocol: str` = "Modbus RTU"
- `ip_address: str` = "127.0.0.1" (for TCP)
- `address1/2/3: int` = 1/2/3
- `delay_ms: int` = 100
- `com_port: str` = "" (empty ⇒ auto-detect first available)
- `baud_rate: int` = 19200
- `slave_id: int` = 1

**App state** also holds the list of `ModelData` (the models entered by the operator) and raises a "models changed" signal when the set changes, so the inference layer can reload.

---

## 5. Classification & the inference contract

In the WPF app the models run in a **separate long-lived Python process** (`infer_server.py`) that the app talks to over JSON-lines on stdin/stdout, to keep the ~100–140 MB checkpoints resident between clicks. **In an all-Python port you almost certainly want to load the models in-process instead** and skip the subprocess entirely — but the *data contract* below is what the UI consumes, so keep the same fields whatever you do.

**Per-classification result** (what `Run Inspection` and Batch Test both consume):
- `ok: bool` and, if not ok, an `error` string.
- `model: str` — the recognized (winning) model name.
- `score: float` — the winner's raw anomaly score.
- `confidence: float` (0..1).
- `accuracy: float | None` (percentage, optional).
- `scores: {model_name: float}` — raw anomaly score from **every** loaded model.
- `threshold: float | None` and `thresholds: {model_name: float}` — each checkpoint's own calibrated threshold.
- `heatmap: str | None` — path to the anomaly overlay image for the recognized model.

Plus a **readiness / model-list** signal: the set of loaded model names, emitted when the models finish loading and whenever they're reloaded. The UI uses it to drive the status dot ("N model(s) loaded") and to populate the Batch Test dropdown.

### Verdict logic (port this exactly — shared by single and batch)

- **Resolve score for a model** — prefer `scores[model]`, else the top-level `score`.
- **Resolve threshold for a model** — a user override in that model's `ModelData.threshold` (if `> 0`) wins; else `thresholds[model]`; else top-level `threshold`.
- **Evaluate** — if either score or threshold is unknown → status *"Classified (no threshold)"*, level **Warn**. Otherwise **pass = score ≤ threshold**: pass → *"OK"* (green); fail → *"NG - Defect Detected"* (red). Note the direction: **lower score is better** (anomaly score).
- **Per-model "Match %" / likelihood** — a softmax over the *negated* raw scores across all loaded models, ×100, so the values sum to ~100%. This answers "how likely is this image to be each model" — a low value means some other model fits better, not merely that this model's score is far from its own threshold. Used for the *Match %* column and the *All Models* grid; the winner is starred/bolded.

### Manual inspection flow
1. Guard: an image must be loaded and the model set ready; otherwise set a Warn status and stop.
2. Set status "Classifying image…", clear the result panel.
3. Classify. On failure show the error in red.
4. On success: fill the All-Models grid, resolve score/threshold, evaluate OK/NG, show the heatmap (fall back to the plain image if none), populate the result panel, set a summary status line.
5. Look up the recognized model's `ModelData`. If none exists, status "Recognized '<name>' but no matching model data" (Warn) and **don't** send to the PLC. If found, send the measurement frame (§6) with the pass/fail result.

---

## 6. Signal handler (serial trigger + measurement frame)

Reference: `assets/signal_handler1.py` (the original) and `assets/SignalHandler.cs` (the C# port this section documents). Serial config: **8 data bits, Even parity, 1 stop bit**, baud from settings, 500 ms read/write timeouts.

### Trigger detection (background thread)
- Resolve the COM port: use the configured one, else auto-detect the first available. If none, report and stay off (the app must still run).
- Loop: read whatever bytes are waiting into a buffer.
  - A **trigger frame is 6–8 bytes**. When the buffer reaches ≥ 6 bytes, treat it as a trigger.
  - **Cooldown 4.0 s** between accepted triggers; drop bytes received during cooldown.
  - A **1 s processing lock** (`_isProcessingSignal`) prevents re-entrancy: fire the "signal detected" callback once, then clear the flag after ~1 s.
  - Incomplete frames time out after 2 s; buffer is cleared if it grows past ~2× max frame size.
  - Poll ~every 10 ms.
- "Wheel present" = a trigger arrived within the last 30 s.

### Measurement frame — 13 bytes, sent back on the same open port
```
[0]  slave_id
[1]  0x03                      (function code)
[2..5]   height_mm   float32, big-endian
[6..9]   diameter_mm float32, big-endian
[10] model_id                  (see mapping)
[11] status                    0x01 = PASS, 0x00 = FAIL
[12] detection_confirmed       0x01 / 0x00
```
- **model_id mapping**: `A1→1, A2→2, B1→3, B2→4, C1→5`; otherwise the first integer found in the name, mod 256; else 0.
- A **Modbus RTU CRC-16** helper (poly `0xA001`, init `0xFFFF`) is provided for completeness. Note: the current 13-byte frame does **not** append the CRC — match that unless you intend to change the protocol, and confirm against the firmware.
- The frame is written on the *same* serial port opened by the detection loop, so sending only works while detection is running.

---

## 7. Batch Test

Opens from *Options → Batch Test* (and the *Upload Batch* button). Requires the models to be loaded.

- **Input**: *Upload Images…* (multi-select) or *Browse Folder…* (top level only). Extensions: `.png .jpg .jpeg .bmp`.
- **Ground-truth dropdown** — "These images are all known-good samples of: <model>". Optional. This is asserted **once for the whole batch** (no per-image labeling). If left on "(unknown / mixed)", the run only reports predictions; if set, the run also computes real accuracy.
- **Run** — classifies each image **sequentially** (one at a time; the model server / model is a single consumer). Progress bar + "Processing i/N". *Cancel* stops between images.
- **Results grid**, one row per image: `# / Image / Predicted Model / True Model / Correct / Score / Confidence / Status`, plus **one dynamically-added `Match: <model>` column per loaded model** (inserted before Confidence the first time that model name appears in a result). Rows are color-coded: OK green, NG red, no-threshold amber (`Warn`), and a batch-only **grey `Fail`** for images the server couldn't classify at all (kept distinct from a defective-but-classified NG).
- **Summary line**: totals, OK/NG/no-threshold/failed counts, average confidence, and — if a true model was chosen — `correct/total` accuracy against that label.
- **Review Misclassified** — after a scored run, collect every row where some model *other* than the asserted one scored a Match ≥ 50% (since Match% sums to ~100%, this fires exactly when a wrong model won). Copy their heatmaps to `results/batch_review/<timestamp>_<trueModel>/` and open a heatmap **gallery** window so the misses can be eyeballed for a common cause.
- **Export CSV** — every row (including all model columns) plus the summary as a trailing line.

Changing the ground-truth dropdown **clears** the grid — stale rows scored against a different truth would poison the next summary.

---

## 8. Secondary windows

Drive these off the data model in §4; exact field layout should follow the data classes. (Their original XAML wasn't part of this spec's reference set, so match behavior, not pixels.)

- **Model data** — add/edit `ModelData` entries (name, diameter, height, threshold). Saving updates the in-memory model list.
- **Model data view** — read-only list/table of the entered models.
- **Modbus settings** — edit `ModbusSettings` (§4).
- **Misclassified review** — the gallery opened by Batch Test's *Review Misclassified* (heatmap tiles with `filename / predicted / true` captions).

---

## 9. Suggested Python structure

Nothing here is prescriptive, but to keep the same behavior:

- **UI**: PySide6/PyQt (there is already a Qt reference app, `assets/wheel_classifier_app.py`) or another toolkit — the layout in §3 maps cleanly to a 3-column grid inside a header/body/status-bar page.
- **Inference**: load the anomaly models in-process (the reference training stack is `anomalib` Patchcore checkpoints producing a per-image `pred_score` and a 256×256 `anomaly_map`). Expose one `classify(image_path) -> result` matching §5, and a `models` list + a "ready" signal.
- **Serial**: `pyserial`, mirroring §6.
- **State**: a single app-state object holding models + Modbus settings, with a "models changed" signal.

Keep the score/threshold/verdict/likelihood math (§5) in one shared module so single-image and batch inspection can never disagree.
