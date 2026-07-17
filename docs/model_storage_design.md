
# Model Storage Design — supporting up to 100 models

**Status:** proposal for review (not yet implemented)
**Scope:** how trained models + their metadata (name, diameter, height, threshold)
are stored, loaded, added by training, and made classifiable — designed to scale
to a maximum of **100 models**.

This document describes **two options — Plan B (per-model folders)** and
**Plan C (SQLite)** — in enough detail to choose one. Everything else in the app
(UI, inference contract, verdict math) stays the same either way.

---

## 1. The one principle behind both plans

There are **two kinds of data** and they live in **different places**:

| Data | Size | Where it lives |
| --- | --- | --- |
| **Weights** — the `.pt` checkpoint | ~39 MB each (≈3.9 GB at 100) | **Always a file on disk.** Never inside JSON or a DB row. |
| **Metadata** — name, diameter, height, threshold (+ future: created date, #train images, accuracy) | a few bytes per model | JSON sidecar (Plan B) **or** a SQLite row (Plan C) |

So the *only* real difference between Plan B and Plan C is **where the small
metadata rows live**. Weights are files in both.

### Why this matters for the whole app
The rest of the code only ever talks to `AppState`, and `AppState` only talks to
a small persistence module (today `app/config.py`). We replace that one module
with a **model registry**; nothing in the UI / inference / verdict layers moves.

```
UI  ──►  AppState  ──►  registry  ──►  { Plan B: folders | Plan C: sqlite }
                                   └──►  weights (.pt files) — both plans
```

---

## 2. Shared contract (identical for B and C)

Both plans expose the **same Python API**, so we can switch B↔C later by swapping
one file. This is the interface `AppState` will call:

```python
# app/registry.py  (replaces app/config.py)

class ModelRecord:
    """Metadata for one model + the path to its weights.

    name       : unique id, matches how the app/PLC refer to the model
    diameter   : mm
    height     : mm
    threshold  : NG threshold; 0 = use the checkpoint's calibrated value
    weights_path : absolute path to the .pt file
    created    : ISO timestamp (optional, for display/sorting)
    """

def list_models() -> list[ModelRecord]:
    """All registered models. Returns: list[ModelRecord] (≤ 100)."""

def add_model(name, diameter, height, threshold, weights_src_path) -> ModelRecord:
    """Register a newly TRAINED model: copy/move its .pt into the store and
    record its metadata. Returns: the created ModelRecord.
    Raises: ValueError if `name` already exists."""

def update_model(name, *, diameter=None, height=None, threshold=None) -> None:
    """Edit an existing model's dimensions/threshold. Returns: None."""

def delete_model(name) -> None:
    """Remove a model (metadata + weights). Returns: None."""

def weights_path(name) -> str | None:
    """Path to a model's .pt, or None if unknown. Returns: str | None."""
```

`InferenceEngine` loads from `weights_path(name)`; the Model-data window calls
`update_model`; the training pipeline calls `add_model`. That's the whole surface.

---

## 3. Plan B — per-model folder registry (recommended for simplicity)

### 3.1 Directory layout
Each model is a **self-contained folder** holding its weights + a tiny metadata
file. Adding a model = create a folder; deleting = remove it.

```
models/
├── model1/
│   ├── weights.pt          # the checkpoint (~39 MB)
│   └── meta.json           # {name, diameter, height, threshold, created}
├── model2/
│   ├── weights.pt
│   └── meta.json
├── ...
└── model100/
    ├── weights.pt
    └── meta.json
```

`meta.json` (per model):
```json
{
  "name": "model1",
  "diameter": 812.0,
  "height": 140.0,
  "threshold": 0.0,
  "created": "2026-07-11T10:22:00",
  "backbone": "resnet50",
  "train_images": 240
}
```

### 3.2 How each operation works
- **List / load** → scan `models/*/meta.json`, read each (100 tiny reads, instant).
- **Add (training output)** → make `models/<name>/`, move the trained `.pt` in as
  `weights.pt`, write `meta.json`. **Only that folder is touched** — no central
  file to lock or corrupt, so a concurrent UI read is always safe.
- **Update dims/threshold** → rewrite that one `meta.json` (atomically: write
  `meta.json.tmp`, then `os.replace` → rename is atomic on Windows & POSIX).
- **Delete** → remove the folder.

### 3.3 Why B fits the training flow
Your described flow — *train → new model appears → immediately classifiable* —
falls out for free: the training step writes one folder, and the next
`list_models()` scan picks it up. No migration step, no central index to keep in
sync. Weights and their metadata can never drift apart because they share a folder.

### 3.4 Migrating the current 5 models
One-time script: for each `models/modelN.pt`, create `models/modelN/`, move the
file to `weights.pt`, and write a `meta.json` seeded from the existing
`config.json` values (or zeros). Old flat `.pt` files are removed after.

### 3.5 Trade-offs
- ➕ Simplest mental model; atomic per-model add/delete; git-friendly; no DB.
- ➖ Metadata is spread across folders (fine at 100; you rarely hand-inspect all).
- ➖ No built-in querying/history — if you later want "all NG inspections for
  model X last week", that's a separate concern (see §5).

---

## 4. Plan C — SQLite metadata + weights as files

### 4.1 Layout
Weights still live as files; a single `registry.db` holds the metadata rows.

```
models/
├── weights/
│   ├── model1.pt
│   ├── model2.pt
│   └── ... model100.pt
└── registry.db            # SQLite: one row per model
```

### 4.2 Schema
```sql
CREATE TABLE models (
    name         TEXT PRIMARY KEY,     -- unique id (also the weights filename stem)
    diameter     REAL NOT NULL DEFAULT 0,
    height       REAL NOT NULL DEFAULT 0,
    threshold    REAL NOT NULL DEFAULT 0,   -- 0 = use checkpoint's own threshold
    weights_file TEXT NOT NULL,         -- e.g. "weights/model1.pt"
    backbone     TEXT DEFAULT 'resnet50',
    train_images INTEGER DEFAULT 0,
    created      TEXT DEFAULT CURRENT_TIMESTAMP
);
```

### 4.3 How each operation works
- **List / load** → `SELECT * FROM models` (100 rows, instant).
- **Add** → copy the trained `.pt` into `weights/`, then
  `INSERT INTO models (...) VALUES (...)` inside a transaction.
- **Update** → `UPDATE models SET diameter=?, ... WHERE name=?`.
- **Delete** → `DELETE FROM models WHERE name=?` + remove the `.pt`.

All writes are **transactional (ACID)** — a crash mid-write can't corrupt the
store, which is the main robustness win over a central JSON.

### 4.4 Migrating the current 5 models
One-time script: create `registry.db`, move `modelN.pt` into `weights/`, and
`INSERT` a row per model seeded from `config.json`.

### 4.5 Trade-offs
- ➕ Crash-safe transactional writes; trivial to query; easy to add columns
  (accuracy, last-used); natural home if you later add **inspection history**.
- ➕ Single file, stdlib `sqlite3`, no server.
- ➖ Slightly more machinery than 100 rows strictly need.
- ➖ Weights and metadata are in separate places, so a stray manual file
  move can desync them (guard with a startup consistency check).

---

## 5. Loading strategy for 100 models (applies to BOTH plans)

Storage is the easy part at 100; **RAM and per-inspection cost are the real
constraint**, because classification runs *every* loaded model.

- 100 × ~39 MB ≈ **~4 GB** resident if all are held in memory at once.
- 100 forward passes per image (~tens of seconds on CPU; fine on GPU).

Two strategies, chosen independently of B/C:

**(a) Load all resident** — simplest; needs a machine with the RAM (and ideally a
GPU). Good if the target PC is a workstation.

**(b) LRU cache (recommended for 100)** — keep only the *N* most-recently-used
models in memory; load the rest from disk on demand and evict the least-used.
Caps RAM no matter how many models exist. Both B and C support this cleanly
because weights are files addressed by `weights_path(name)`:

```python
# sketch inside InferenceEngine
from functools import lru_cache

@lru_cache(maxsize=N)          # N tuned to available RAM (e.g. 20 → ~0.8 GB)
def _get_module(name: str):
    """Load + cache a model's Patchcore module. Returns: the eval-mode module.
    Evicts the least-recently-used model when the cache is full."""
    sd = torch.load(registry.weights_path(name), map_location=DEVICE, weights_only=False)
    ...
```

> This is why the loading strategy should be decided **when we build training**,
> not the storage format — it's what actually gets expensive at 100.

### 5.1 In plain language — how long does one check take? (for the team)

*Scenario for these numbers: the production PC is an **Advantech IPC-610H, no
GPU (CPU only)**, with **65 models** trained and in use.*

**The one thing to understand:** when a component arrives, the software runs the
image through **every** trained model to see which one fits best. So the time
depends on **how many models exist — not on which model turns out to match.**

> Matching the **65th** model takes the **same** time as matching the 1st. There
> is no "slow model." The cost grows with the *total count* of models, in a
> straight line (double the models ≈ double the time).

**Rough time per component (65 models, CPU-only):**

| Version | Time per component | Memory used |
| --- | --- | --- |
| **Simple** (run each of the 65 models fully) | **~10–25 seconds** | ~2.5 GB |
| **Optimized** (the 65 models share one "feature extractor" — run it once, then do 65 quick comparisons) | **~1–3 seconds** | ~0.5 GB |

*(These are estimates, not yet measured on the actual IPC-610H. We can benchmark
one real run to confirm.)*

**Why the optimized version is so much faster:** every model uses the **same**
image "feature extractor" (a fixed, pre-trained network) and only differs in a
small learned "fingerprint." So instead of processing the image 65 times, we
process it **once** and then compare against 65 fingerprints — which is cheap.

**Why we still cap at 100 and use an LRU cache:** memory. 100 models fully in RAM
would be ~4 GB. An **LRU cache** keeps the recently-used models in memory and
loads the rest only when needed, so RAM never runs away as the model count grows.
At 65 models the machine has enough RAM to hold them all, so nothing gets evicted
and there's no slow-down; the cache only starts swapping near the 100 limit.

**Bottom line for the line/cycle time:** with the simple version, budget
~10–25 s per component at 65 models; with the optimized shared-extractor version,
~1–3 s. If the line's cycle time is tighter than that, the optimized path (and/or
Intel **OpenVINO** CPU acceleration on the IPC) is the way to hit it.

---

## 6. Comparison & recommendation

| | Plan B — folders | Plan C — SQLite |
| --- | --- | --- |
| Add/delete a model | create/remove a folder (atomic) | copy `.pt` + INSERT/DELETE (transactional) |
| Crash-safety of writes | per-file atomic rename | full ACID transactions |
| Weights ↔ metadata linkage | same folder — can't drift | separate — needs a consistency check |
| Querying / future history | none built in | native SQL |
| Extra dependencies | none | none (`sqlite3` is stdlib) |
| Best when… | you want the simplest thing that matches "training adds a folder" | you also want history/queries/audit later |

**Recommendation:** **Plan B** if you want the least moving parts and the
add-a-model flow to be dead simple; **Plan C** if you expect to want inspection
history, accuracy tracking, or ad-hoc queries as the model count grows toward 100.
Both scale to 100 comfortably. Pair either with the **LRU loading strategy (5b)**
so RAM stays bounded.

---

## 7. What actually changes in the codebase (either plan)

Small and contained:

1. **New** `app/registry.py` implementing the §2 API (replaces `app/config.py`).
2. `app/state.py` (`AppState`) calls the registry instead of `config` — same
   `models_changed` signal, so the UI is untouched.
3. `app/inference.py` loads via `registry.weights_path(name)` and (optionally)
   gains the LRU cache from §5.
4. `app/dialogs/model_data.py` calls `update_model` instead of rewriting a JSON
   blob. It also grows the training-launch entry point later.
5. **One-time migration script** to move the current 5 `model*.pt` into the new
   structure.

Modbus settings stay in a small JSON/settings file (they're a single object, not
a growing set) — no need to move those into the registry.

---

## 8. Decision checklist (fill in before implementing)

- [ ] **Store:** Plan B (folders) or Plan C (SQLite)?
- [ ] **Loading:** all-resident (5a) or LRU cache (5b)? If LRU, target `N` =
      available RAM ÷ ~40 MB.
- [ ] **Target machine:** GPU available? How much RAM? (Drives 5a vs 5b.)
- [ ] **Minimum training images `n`:** starting value (you noted this will change
      later — we'll make it a setting).
```
