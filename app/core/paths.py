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
