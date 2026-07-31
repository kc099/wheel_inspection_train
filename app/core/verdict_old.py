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


def pick_by_diameter(result, models, measured_px: float,
                     min_confidence: float, tolerance_pct: float):
    """Break an appearance tie using the measured diameter.

    Patchcore compares appearance, so two wheel types with the same pattern but
    different diameters score alike. Among the models that ALREADY pass the
    confidence gate and carry a measured `pixel_diameter`, pick the one whose
    stored value is closest to `measured_px`.

    models       : iterable of ModelData (need .name and .pixel_diameter)
    measured_px  : this frame's measured diameter, in pixels
    tolerance_pct: how far off the stored value may be and still match

    Returns: (chosen_name, reason). `chosen_name` is None whenever the tie-break
    does not apply — the caller then keeps the classifier's own winner, so this
    can never make recognition worse than classification alone.
    """
    if measured_px <= 0:
        return None, "no measurement"

    by_name = {m.name: m for m in models}
    candidates = [
        name for name, conf in result.confidences.items()
        if conf >= min_confidence
        and name in by_name
        and by_name[name].pixel_diameter > 0
    ]
    if len(candidates) < 2:
        return None, "nothing to disambiguate"

    best = min(candidates,
               key=lambda n: abs(by_name[n].pixel_diameter - measured_px))
    stored = by_name[best].pixel_diameter
    off_pct = abs(stored - measured_px) / stored * 100.0
    if off_pct > tolerance_pct:
        return None, f"closest model {best} off by {off_pct:.1f}% (> {tolerance_pct}%)"
    return best, f"{best} (stored {stored:.1f}px vs measured {measured_px:.1f}px)"
