"""Shared recognition math — the ONE place model likelihood is computed.

Both single-image and batch inspection call these functions, so the two paths
can never disagree. Keep the likelihood logic here; do not re-implement in the UI.

The app is recognition-only: it decides WHICH model a component is (the winner =
lowest anomaly score). There is no OK/NG defect threshold.
"""

from __future__ import annotations

from enum import Enum

from .models import ClassificationResult


class Level(Enum):
    """UI status severity, used to colour the status dot (README §2 colours)."""
    OK = "ok"        # green  — recognized successfully
    WARN = "warn"    # amber  — recognized but no matching model data / not ready
    ERROR = "error"  # red    — could not classify
    INFO = "info"    # neutral — informational


def match_percentages(result: ClassificationResult) -> dict[str, float]:
    """Per-model "Match %" — each model's share of the total confidence (~100).

    Based on per-model CONFIDENCE (score normalised by each model's own range),
    not raw scores, so it's comparable across differently scaled models and the
    winner (highest confidence) is also the highest Match %.

    Returns: {model_name: percent}. All zero when nothing fits (foreign image).
    """
    conf = result.confidences
    if not conf:
        return {}
    total = sum(conf.values())
    if total <= 0:                               # foreign image: no model fits
        return {n: 0.0 for n in conf}
    return {n: (v / total) * 100.0 for n, v in conf.items()}
