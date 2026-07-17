"""Settings persistence — Modbus + app settings in one small settings.json.

Model DATA no longer lives here (that moved to the per-model registry, see
registry.py). This file only holds the single settings object, which is a good
fit for a plain JSON file.

    settings.json = { "modbus": {...}, "app": {min_training_images, lru_cache_size} }
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import AppSettings, CameraSettings, ModbusSettings
from .paths import ROOT                         # frozen-aware (PyInstaller safe)

MODELS_DIR = ROOT / "models"
SETTINGS_PATH = ROOT / "settings.json"
_LEGACY_CONFIG = ROOT / "config.json"           # pre-split file, used once to seed modbus


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON via temp-file + rename so a crash can't corrupt it. Returns: None."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_settings() -> tuple[ModbusSettings, AppSettings, CameraSettings]:
    """Read settings.json, seeding it on first run.

    Seeding order: settings.json → else legacy config.json's "modbus" → else
    defaults. Returns: (ModbusSettings, AppSettings, CameraSettings).
    """
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            modbus = ModbusSettings.from_dict(data.get("modbus", {}))
            app = AppSettings.from_dict(data.get("app", {}))
            camera = CameraSettings.from_dict(data.get("http_streamer", {}))
            # Heal older files that predate a section or a field, so new
            # settings (http_streamer, recognition_min_confidence) show up.
            healed = (
                not all(k in data for k in ("modbus", "app", "http_streamer"))
                or not all(
                    k in data.get("app", {})
                    for k in ("recognition_min_confidence",
                              "session_timeout_minutes", "max_profiles")
                )
            )
            if healed:
                save_settings(modbus, app, camera)
            return modbus, app, camera
        except Exception as e:
            print(f"[config] could not read settings.json ({e}); using defaults")

    # First run: seed modbus from a legacy config.json if one exists.
    modbus = ModbusSettings()
    if _LEGACY_CONFIG.exists():
        try:
            legacy = json.loads(_LEGACY_CONFIG.read_text(encoding="utf-8"))
            modbus = ModbusSettings.from_dict(legacy.get("modbus", {}))
        except Exception:
            pass
    app = AppSettings()
    camera = CameraSettings()
    save_settings(modbus, app, camera)
    return modbus, app, camera


def save_settings(modbus: ModbusSettings, app: AppSettings,
                  camera: CameraSettings) -> None:
    """Persist all settings objects to settings.json. Returns: None."""
    _atomic_write_json(SETTINGS_PATH, {
        "modbus": modbus.to_dict(),
        "app": app.to_dict(),
        "http_streamer": camera.to_dict(),
    })
