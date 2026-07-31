"""Frozen-aware filesystem root — the ONE place the app's data root is decided.

Running from source, the root is the project folder (two levels above app/core).
Running as a PyInstaller build (sys.frozen), the root is the folder holding the
.exe — NOT the bundle's _internal directory, because models/, settings.json,
users.json and history.db must sit next to the executable where they survive
app upgrades and stay visible to engineers.
"""

from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    """The folder all app data lives under. Returns: Path."""
    if getattr(sys, "frozen", False):                 # PyInstaller build
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]        # app/core/paths.py → root


ROOT = app_root()

# Where Capture and PLC triggers save frames — these ARE the training set.
CAPTURES_DIR = Path.home() / "Desktop" / "WheelCaptures"
CAPTURES_DIR1 = Path.home() / "Desktop" / "BackgroundCaptures"
# The empty-conveyor reference lives in a SUB-folder of the captures, on
# purpose. Operators bulk-delete the loose images in WheelCaptures to recapture
# a training set; a sub-folder survives that. It also keeps the reference out of
# training, since list_images() only reads files directly in a folder.
BACKGROUND_DIR = CAPTURES_DIR1
BACKGROUND_PATH = BACKGROUND_DIR / "background.png"
