"""Geometric diameter measurement — background subtraction + circle fit.

Ported from the reference rig's `compute_diameter_mm` (RoboWorks
wheel_inspection_Ref/realsense_streamer.py) and extended with the masking stage
that project had dropped.

Why this exists: Patchcore compares *appearance*, so two wheel types with the
same spoke pattern but different diameters recognise as one model. Measuring
the part geometrically lets us break that tie (see docs/retraining_strategy.md
§5). Measurement NEVER overrides classification — it only refines it, and every
failure path falls back to the classifier's own answer.

Nothing here writes to disk: masks are returned in memory for optional display.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core.paths import ROOT

# OpenCV is optional everywhere else in the app; keep that contract.
try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

# 0 = pick the threshold automatically (Otsu). Anything 1..255 is a fixed cut.
AUTO_THRESHOLD = 0

# --- how the enclosing circle is fitted (operator-configurable) --------------
# "min-enclosing" : minEnclosingCircle over ALL mask pixels (default). The
#                   circle encloses every white pixel of the binary mask.
# "hull"          : convex hull of only the SIGNIFICANT fragments (drops small
#                   specks first), then minEnclosingCircle. Robust to distant noise.
FIT_MIN_ENCLOSING = "min-enclosing"
FIT_HULL = "hull"
_VALID_METHODS = (FIT_MIN_ENCLOSING, FIT_HULL)
# Friendly aliases so a natural spelling in the config still resolves.
_METHOD_ALIASES = {
    "mask": FIT_MIN_ENCLOSING,
    "min enclosing": FIT_MIN_ENCLOSING,
    "min-circle": FIT_MIN_ENCLOSING,
    "convex hull": FIT_HULL,
    "convexhull": FIT_HULL,
    "convex-hull": FIT_HULL,
}

# Threshold method names surfaced in the config so it is self-documenting.
THR_OTSU = "otsu"     # automatic per-frame (OpenCV Otsu)
THR_FIXED = "fixed"   # a fixed 1..255 cut

# Sits next to the exe / project root; edit it to switch method without a
# rebuild. Re-read on every measurement so changes take effect immediately.
MEASURE_CONFIG_PATH = ROOT / "measure_config.json"


def _normalize_method(name: str) -> str | None:
    """Map a config value (incl. the old 'mask' alias) to a canonical method."""
    name = str(name).strip().lower()
    name = _METHOD_ALIASES.get(name, name)
    return name if name in _VALID_METHODS else None


def load_measure_config(default_threshold: int = AUTO_THRESHOLD) -> dict:
    """Read measure_config.json — the measurement pipeline's config.

    Returns a dict with:
      fit_method       : 'min-enclosing' | 'hull'
      threshold        : resolved int cut (0 = Otsu auto)
      threshold_method : 'otsu' | 'fixed'  (for display/logging)

    `default_threshold` is used when the file omits the threshold keys, so the
    live UI slider value still applies. A missing file or bad value falls back
    to safe defaults — the feature can never be broken by a typo.
    """
    cfg = {
        "fit_method": FIT_MIN_ENCLOSING,
        "threshold": int(default_threshold),
        "threshold_method": THR_OTSU if default_threshold <= AUTO_THRESHOLD else THR_FIXED,
        # All operator controls live here now (the UI checkboxes were removed).
        "measure_diameter": True,
        "tie_confidence": 0.5,
        "diameter_tolerance_pct": 3.0,
        "show_binary_mask": False,
        "show_heatmap_overlay": False,
    }
    try:
        data = json.loads(MEASURE_CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return cfg
    except Exception as e:
        print(f"[measure] could not read measure_config.json ({e}); using defaults", flush=True)
        return cfg

    method = _normalize_method(data.get("fit_method", FIT_MIN_ENCLOSING))
    if method:
        cfg["fit_method"] = method
    else:
        print(f"[measure] unknown fit_method {data.get('fit_method')!r}; "
              f"using '{FIT_MIN_ENCLOSING}'", flush=True)

    tm = str(data.get("threshold_method", "")).strip().lower()
    if tm == THR_OTSU:
        cfg["threshold"], cfg["threshold_method"] = AUTO_THRESHOLD, THR_OTSU
    elif tm == THR_FIXED:
        cfg["threshold"] = int(data.get("threshold_value", default_threshold))
        cfg["threshold_method"] = THR_FIXED
    # else: no threshold keys in the file → keep the default (UI slider) value.

    cfg["measure_diameter"] = bool(data.get("measure_diameter", cfg["measure_diameter"]))
    cfg["tie_confidence"] = float(data.get("tie_confidence", cfg["tie_confidence"]))
    cfg["diameter_tolerance_pct"] = float(
        data.get("diameter_tolerance_pct", cfg["diameter_tolerance_pct"]))
    cfg["show_binary_mask"] = bool(data.get("show_binary_mask", cfg["show_binary_mask"]))
    cfg["show_heatmap_overlay"] = bool(
        data.get("show_heatmap_overlay", cfg["show_heatmap_overlay"]))
    return cfg

# Morphology kernels. Deliberately modest: a 25px CLOSE x3 on a 720p frame
# costs ~a second and froze the UI, and it is not needed — _component_hull()
# already bridges spoke gaps and roller-overlap bands geometrically. Morphology
# only has to despeckle and tidy edges here.
_OPEN_SIZE = 5
_CLOSE_SIZE = 9
_CLOSE_ITERS = 2

# Fragments at least this fraction of the largest one are treated as part of the
# same component and merged into the hull.
_FRAGMENT_MIN_RATIO = 0.02

# A contour hugging the frame edge means the part is cut off — its diameter
# would be under-measured, so we reject it rather than report a wrong number.
_EDGE_MARGIN_PX = 2


@dataclass
class Measurement:
    """One diameter measurement plus the intermediate mask for display."""

    ok: bool = False
    reason: str = ""                    # why it failed, for the log
    # PRIMARY: minEnclosingCircle over the component hull. Exact on clean
    # circles, and it is the estimator the rig's pixel_to_mm was calibrated
    # with — switching estimator would silently bias every mm reading.
    diameter_px: float = 0.0
    # Secondary, logged for comparison: 2*sqrt(area/pi). Less sensitive to a
    # single outlying point, but reads ~1-2% low on a hull. Compare the two on
    # real captures before preferring it.
    diameter_area_px: float = 0.0
    diameter_mm: float = 0.0
    centre: tuple[float, float] = (0.0, 0.0)
    # The cut actually used. With threshold=0 this is whatever Otsu computed,
    # so the operator can read it off and pin it as a fixed value if wanted.
    threshold_used: int = 0
    auto_threshold: bool = True
    # 1.0 = the hull exactly fills its circle (a true disc). Lower means the
    # hull has protrusions, and since minEnclosingCircle is set by the two
    # furthest points, a low value warns the diameter is being inflated.
    roundness: float = 0.0
    # True when the empty-conveyor reference was actually subtracted; False when
    # we fell back to plain thresholding on the raw frame (no/again-sized bg).
    used_background: bool = False
    # Which fit was applied this measurement ("min-enclosing" or "hull").
    fit_method: str = FIT_MIN_ENCLOSING
    mask: object = field(default=None, repr=False)   # np.ndarray | None
    # Colour overlay: mask + fitted hull + fitted circle, for the debug window.
    overlay: object = field(default=None, repr=False)


def load_background(path: str | Path):
    """Read the stored empty-conveyor reference. Returns: ndarray | None."""
    if not CV2_AVAILABLE:
        return None
    p = Path(path)
    if not p.exists():
        return None
    return cv2.imread(str(p))


def build_mask(frame: np.ndarray, background: np.ndarray | None,
               threshold: int = AUTO_THRESHOLD) -> tuple[np.ndarray, int]:
    """Isolate the component as a binary mask.

    With a background: absdiff removes everything that didn't change (rollers,
    rails, fixed shadows). Without one: fall back to plain thresholding, which
    works because the wheels are light on a dark rig — less robust, but it keeps
    the feature usable before a reference has been captured.

    Returns: (uint8 mask where 255 = component, the threshold actually used).
    With threshold=0 the second value is the cut Otsu computed for this frame.
    """
    if background is not None and background.shape == frame.shape:
        work = cv2.absdiff(frame, background)
    else:
        work = frame

    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)

    # cv2.threshold returns the cut it used; with THRESH_OTSU the value passed
    # in is ignored and Otsu's computed cut comes back instead.
    if threshold <= AUTO_THRESHOLD:
        used, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:
        used, mask = cv2.threshold(gray, int(threshold), 255, cv2.THRESH_BINARY)

    k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_OPEN_SIZE, _OPEN_SIZE))
    k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_CLOSE_SIZE, _CLOSE_SIZE))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_open)                        # despeckle
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_close, iterations=_CLOSE_ITERS)
    return mask, int(used)


def _component_hull(contours):
    """Merge the component's fragments into one convex outline.

    A wheel's mask often breaks into arcs — spoke gaps, and low-contrast bands
    where the part overlaps a bright roller. Taking only the biggest fragment
    would measure an arc instead of the wheel, so fragments comparable in size
    to the largest are merged and wrapped in a convex hull. For a round part the
    hull IS the outline, and it closes the internal spoke holes for free.

    Returns: the hull contour, or None if there is nothing usable.
    """
    if not contours:
        return None
    areas = [cv2.contourArea(c) for c in contours]
    biggest = max(areas)
    if biggest <= 0:
        return None
    keep = [c for c, a in zip(contours, areas) if a >= biggest * _FRAGMENT_MIN_RATIO]
    return cv2.convexHull(np.vstack(keep))


def _mask_hull(contours):
    """Min-enclosing method — convex hull of the SIGNIFICANT fragments.

    Was 'all mask points', but a single speck then inflated the circle (a
    timestamp-text blob pushed one wheel to 569 mm; see docs/work_log.md
    2026-07-28). It now applies the SAME >= 2% fragment filter as _component_hull
    so distant specks are dropped before the enclosing circle is fitted.
    Returns: the hull contour, or None.
    """
    return _component_hull(contours)


def measure(image_path: str, background: np.ndarray | None,
            pixel_to_mm: float, threshold: int = AUTO_THRESHOLD,
            fit_method: str = FIT_MIN_ENCLOSING) -> Measurement:
    """Measure the component's diameter in one frame.

    fit_method:
      "min-enclosing" — minEnclosingCircle over ALL mask pixels (default).
      "hull"          — convex hull of the significant fragments only (robust).
    Both then fit minEnclosingCircle; the convex-hull step is kept intact.

    Returns: a Measurement (ok=False + reason when it cannot measure).
    """
    if not CV2_AVAILABLE:
        return Measurement(reason="OpenCV not installed", fit_method=fit_method)

    frame = cv2.imread(str(image_path))
    if frame is None:
        return Measurement(reason=f"could not read {Path(image_path).name}",
                           fit_method=fit_method)

    if background is not None and background.shape != frame.shape:
        # Wrong-sized reference is worse than none: it would diff garbage.
        background = None
    used_background = background is not None

    mask, used = build_mask(frame, background, threshold)
    auto = threshold <= AUTO_THRESHOLD
    method = _normalize_method(fit_method) or FIT_MIN_ENCLOSING
    # Carried on every return so a failed measurement still shows the operator
    # which cut produced the bad mask.
    ctx = dict(mask=mask, threshold_used=used, auto_threshold=auto,
               used_background=used_background, fit_method=method)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return Measurement(reason="no contour found", **ctx)

    # Both methods now drop fragments < 2% of the largest before fitting, then
    # convex-hull + minEnclosingCircle. (Per 2026-07-28: min-enclosing used to
    # enclose ALL pixels, but specks inflated it.) The two are equivalent today;
    # the split is kept so the behaviour can diverge again without a config change.
    contour = _mask_hull(contours) if method == FIT_MIN_ENCLOSING else _component_hull(contours)
    if contour is None:
        return Measurement(reason="empty contour", **ctx)
    area = float(cv2.contourArea(contour))
    if area <= 0:
        return Measurement(reason="empty contour", **ctx)

    # Reject a part that runs off the frame — its diameter is not observable.
    h, w = mask.shape[:2]
    x, y, cw, ch = cv2.boundingRect(contour)
    if (x <= _EDGE_MARGIN_PX or y <= _EDGE_MARGIN_PX
            or x + cw >= w - _EDGE_MARGIN_PX or y + ch >= h - _EDGE_MARGIN_PX):
        return Measurement(reason="component touches frame edge", **ctx)

    (cx, cy), radius = cv2.minEnclosingCircle(contour)
    d_circle = 2.0 * float(radius)
    circle_area = math.pi * radius * radius

    # Visual proof of the fit: green hull (what was measured) over the white
    # mask, red circle (what was reported). If the red circle is much bigger
    # than the green outline, the diameter is being driven by a protrusion.
    overlay = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    cv2.drawContours(overlay, [contour], -1, (0, 255, 0), 2)
    cv2.circle(overlay, (int(cx), int(cy)), int(radius), (0, 0, 255), 2)
    cv2.drawMarker(overlay, (int(cx), int(cy)), (0, 0, 255), cv2.MARKER_CROSS, 20, 2)
    ctx["overlay"] = overlay

    return Measurement(
        ok=True,
        diameter_px=d_circle,
        diameter_area_px=2.0 * math.sqrt(area / math.pi),
        diameter_mm=d_circle * float(pixel_to_mm),
        centre=(float(cx), float(cy)),
        roundness=float(area / circle_area) if circle_area else 0.0,
        **ctx,
    )


def measure_folder(paths, background, pixel_to_mm: float,
                   threshold: int = AUTO_THRESHOLD,
                   fit_method: str = FIT_MIN_ENCLOSING) -> float:
    """Median pixel diameter over many images — used at training time.

    The median (not mean) so one bad mask can't shift a model's stored
    calibration. Returns: median diameter in pixels, or 0.0 if nothing measured.
    """
    values = [
        m.diameter_px for m in
        (measure(p, background, pixel_to_mm, threshold, fit_method) for p in paths)
        if m.ok
    ]
    return float(np.median(values)) if values else 0.0
