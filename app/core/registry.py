"""Model registry — Plan B: one folder per model (see docs/model_storage_design.md).

Layout:
    models/<name>/weights.pt      the checkpoint
    models/<name>/meta.json       {name, diameter, height, created}

Why folders (not a central JSON): training just writes one new folder, and it's
picked up on the next scan — no central file to lock or corrupt, and a model's
weights and metadata can never drift apart. Scales cleanly to the 100-model cap.

Weights are ALWAYS files; only the small metadata lives in meta.json.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from .models import ModelData
from .paths import ROOT                         # frozen-aware (PyInstaller safe)

MODELS_DIR = ROOT / "models"
_LEGACY_CONFIG = ROOT / "config.json"           # pre-registry storage, used to seed dims
_WEIGHTS = "weights.pt"
_META = "meta.json"

# Hard ceiling on registered models. Deliberately private and NEVER surfaced in
# the UI — error messages must stay generic (no figure shown to the user).
_MAX_MODELS = 50


def at_capacity() -> bool:
    """Is the registry full? (The limit itself is intentionally not exposed.)"""
    return len(_model_folders()) >= _MAX_MODELS


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON so a crash mid-write can't corrupt the file.

    Writes a temp file then renames it (rename is atomic). Returns: None.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _legacy_dims() -> dict[str, dict]:
    """Read old config.json (if any) to seed dims during migration.

    Returns: {name: {"diameter": .., "height": ..}}. Empty if no legacy file.
    """
    if not _LEGACY_CONFIG.exists():
        return {}
    try:
        data = json.loads(_LEGACY_CONFIG.read_text(encoding="utf-8"))
        return {m["name"]: m for m in data.get("models", []) if "name" in m}
    except Exception:
        return {}


def _migrate_flat_models() -> None:
    """One-time: convert models/<name>.pt → models/<name>/weights.pt + meta.json.

    Idempotent — a second run finds no flat files and does nothing. Returns: None.
    """
    if not MODELS_DIR.is_dir():
        return
    flat = list(MODELS_DIR.glob("*.pt"))
    if not flat:
        return
    seed = _legacy_dims()
    for pt in flat:
        name = pt.stem
        folder = MODELS_DIR / name
        folder.mkdir(exist_ok=True)
        shutil.move(str(pt), str(folder / _WEIGHTS))
        dims = seed.get(name, {})
        _atomic_write_json(folder / _META, {
            "name": name,
            "diameter": float(dims.get("diameter", 0.0)),
            "height": float(dims.get("height", 0.0)),
            "pixel_diameter": 0.0,          # unmeasured: skips the diameter check
            "created": datetime.now().isoformat(timespec="seconds"),
        })


def _model_folders() -> list[Path]:
    """Folders under models/ that contain a weights.pt. Returns: list[Path]."""
    if not MODELS_DIR.is_dir():
        return []
    return sorted(
        p for p in MODELS_DIR.iterdir()
        if p.is_dir() and (p / _WEIGHTS).exists()
    )


def list_models() -> list[ModelData]:
    """All registered models (migrating legacy flat files first).

    Returns: list[ModelData] sorted by name (≤ 100 in practice).
    """
    _migrate_flat_models()
    out: list[ModelData] = []
    for folder in _model_folders():
        meta_path = folder / _META
        if meta_path.exists():
            try:
                out.append(ModelData.from_dict(
                    json.loads(meta_path.read_text(encoding="utf-8"))
                ))
                continue
            except Exception:
                pass
        out.append(ModelData(name=folder.name))     # weights present but no/bad meta
    return out


def weights_path(name: str) -> str | None:
    """Absolute path to a model's checkpoint. Returns: str, or None if missing."""
    p = MODELS_DIR / name / _WEIGHTS
    return str(p) if p.exists() else None


def exists(name: str) -> bool:
    """Is a model registered under `name`? Returns: bool."""
    return (MODELS_DIR / name / _WEIGHTS).exists()


def save_meta(model: ModelData) -> None:
    """Update an existing model's meta.json (dims edited in the UI).

    Preserves the original `created` timestamp. Returns: None.
    """
    folder = MODELS_DIR / model.name
    folder.mkdir(parents=True, exist_ok=True)
    created = datetime.now().isoformat(timespec="seconds")
    meta_path = folder / _META
    if meta_path.exists():
        try:
            created = json.loads(meta_path.read_text(encoding="utf-8")).get("created", created)
        except Exception:
            pass
    data = model.to_dict()
    data["created"] = created
    _atomic_write_json(meta_path, data)


def add_model(model: ModelData, weights_src: str | Path) -> None:
    """Register a newly trained model: place its weights + write metadata.

    weights_src : path to the freshly trained .pt (moved into the store).
    Returns: None. Raises ValueError if the name is already taken.
    """
    if exists(model.name):
        raise ValueError(f"A model named '{model.name}' already exists.")
    if at_capacity():
        raise ValueError(
            "Model limit reached — delete an unused model to add a new one."
        )
    folder = MODELS_DIR / model.name
    folder.mkdir(parents=True, exist_ok=True)
    shutil.move(str(weights_src), str(folder / _WEIGHTS))
    save_meta(model)


def delete_model(name: str) -> None:
    """Remove a model entirely (folder + weights + meta). Returns: None."""
    folder = MODELS_DIR / name
    if folder.is_dir():
        shutil.rmtree(folder)


# Characters that would break a folder name on Windows.
_BAD_NAME_CHARS = set('\\/:*?"<>|')


def validate_name(name: str) -> str:
    """Check a proposed model name is a usable folder name.

    Returns: the trimmed name. Raises ValueError if empty or containing a
    path/reserved character (the name becomes a folder, so it must be safe).
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Model name cannot be empty.")
    if any(c in _BAD_NAME_CHARS for c in name):
        raise ValueError("Model name cannot contain \\ / : * ? \" < > |")
    return name


def rename_model(old: str, new: str) -> None:
    """Rename a model: move its folder and update meta.json's name.

    Why the folder move: the folder name IS the model id, so renaming only the
    metadata would leave the weights unfindable. Returns: None. Raises
    ValueError if `new` is invalid or already taken.
    """
    new = validate_name(new)
    if new == old:
        return
    if exists(new):
        raise ValueError(f"A model named '{new}' already exists.")
    old_folder = MODELS_DIR / old
    if not (old_folder / _WEIGHTS).exists():
        raise ValueError(f"Model '{old}' not found.")
    shutil.move(str(old_folder), str(MODELS_DIR / new))

    # Keep meta.json's name in sync with the new folder name.
    meta_path = MODELS_DIR / new / _META
    data = {"name": new, "diameter": 0.0, "height": 0.0}
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            data["name"] = new
        except Exception:
            data = {"name": new, "diameter": 0.0, "height": 0.0}
    _atomic_write_json(meta_path, data)
