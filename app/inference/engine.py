"""In-process anomaly inference over the registry's Patchcore checkpoints.

Recognition-only: for one image we run every model, and the winner is the model
with the LOWEST raw anomaly score (best fit). No OK/NG threshold.

Scaling to 100 models (see docs/model_storage_design.md §5): models are loaded
through an **LRU cache** keyed by name — loaded on demand from disk, and the
least-recently-used model is evicted when the cache is full. `lru_cache_size`
comes from settings (default 100 = hold everything up to the cap, so nothing
evicts below that).
"""

from __future__ import annotations

import traceback
from collections import OrderedDict

import numpy as np
import torch
from PIL import Image
from PySide6.QtCore import QObject, QThread, Signal

from ..core import registry
from ..core.models import ClassificationResult

# ImageNet normalisation — the preprocessing Patchcore was trained with.
_MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
_STD = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
_IMG_SIZE = 256

# NOTE: heatmap overlays are rendered in memory ONLY when requested (UI
# checkbox) and are never written to disk — every inspection used to save an
# overlay PNG under results/, which grows without bound in production.


class _LoadedModel:
    """A resident checkpoint plus the calibration used at inference time."""

    def __init__(self, module, img_min: float, img_max: float) -> None:
        self.module = module          # anomalib Patchcore (eval mode)
        self.img_min = img_min        # min/max map a raw score → 0..1 confidence
        self.img_max = img_max


def _preprocess(image_path: str) -> torch.Tensor:
    """Load an image → normalised (1,3,256,256) tensor. Returns: torch.Tensor.
    Raises on an unreadable file (caller turns it into a result error)."""
    img = Image.open(image_path).convert("RGB").resize((_IMG_SIZE, _IMG_SIZE))
    arr = np.asarray(img, dtype=np.float32) / 255.0
    t = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0)
    return (t - _MEAN) / _STD


def _load_module(name: str) -> _LoadedModel:
    """Build one Patchcore model from its checkpoint on disk.

    Returns: a _LoadedModel (module in eval mode + min/max). Raises if the
    weights are missing or anomalib is unavailable.
    """
    from anomalib.models import Patchcore

    path = registry.weights_path(name)
    if path is None:
        raise FileNotFoundError(f"No weights for model '{name}'")
    sd = torch.load(path, map_location="cpu", weights_only=False)
    # The backbone layers are baked into the checkpoint via its memory-bank
    # width (resnet50: layer2=512, layer3=1024, layer4=2048 channels), so
    # models trained before and after the layer2+layer4 switch both load.
    bank = sd.get("model.memory_bank")
    dim = int(bank.shape[-1]) if hasattr(bank, "shape") else 0
    layers = {1536: ["layer2", "layer3"], 2560: ["layer2", "layer4"]}.get(
        dim, ["layer2", "layer4"]
    )
    # pre_trained=False: the checkpoint holds EVERY weight (strict load below
    # overwrites the backbone anyway), so downloading ImageNet weights from the
    # HF Hub here is pure waste — and fails on an offline production machine.
    module = Patchcore(backbone="resnet50", layers=layers, pre_trained=False)
    module.load_state_dict(sd, strict=True)
    module.eval()

    def _f(key: str) -> float:
        v = sd.get(key)
        return float(v) if hasattr(v, "numel") else 0.0

    return _LoadedModel(
        module,
        _f("post_processor.image_min"),
        _f("post_processor.image_max"),
    )


class _Warmer(QThread):
    """Background thread that pre-loads models into the cache after startup."""

    progress = Signal(int, int)   # (loaded, total)
    done = Signal(list)           # -> list[str] of names that are ready
    failed = Signal(str)

    def __init__(self, engine: "InferenceEngine") -> None:
        super().__init__()
        self._engine = engine

    def run(self) -> None:
        try:
            import anomalib  # noqa: F401  (fail fast if the stack is missing)
        except Exception as e:
            self.failed.emit(f"anomalib not available: {e}")
            return
        names = [m.name for m in registry.list_models()]
        if not names:
            self.failed.emit("No models registered")
            return
        # Warm up to the cache limit; the rest load lazily on first use.
        for i, name in enumerate(names[: self._engine.cache_size], start=1):
            try:
                self._engine._ensure_loaded(name)
            except Exception as e:
                traceback.print_exc()
                self.failed.emit(f"Failed loading {name}: {e}")
                return
            self.progress.emit(i, min(len(names), self._engine.cache_size))
        self.done.emit(names)


class InferenceEngine(QObject):
    """Owns the LRU model cache and runs classification (recognition-only).

    Signals:
      ready(list[str]) — model names available, emitted once warm-up finishes.
      error(str)       — a load error to surface on the status dot.
    """

    ready = Signal(list)
    error = Signal(str)

    def __init__(self, cache_size: int = 100, min_confidence: float = 0.3) -> None:
        super().__init__()
        self.cache_size = max(1, cache_size)
        # Winner must reach this confidence to count as a real match; below it
        # the image "doesn't belong to any model" (see classify + models.py).
        self.min_confidence = min_confidence
        self._cache: "OrderedDict[str, _LoadedModel]" = OrderedDict()
        self._warmer: _Warmer | None = None

    # --- model set ---------------------------------------------------------
    @property
    def model_names(self) -> list[str]:
        """Names currently registered on disk. Returns: list[str]."""
        return [m.name for m in registry.list_models()]

    @property
    def is_ready(self) -> bool:
        """True once at least one model is registered. Returns: bool."""
        return bool(registry.list_models())

    def load_async(self) -> None:
        """Warm the cache in the background. Returns: None (see ready/error)."""
        self._warmer = _Warmer(self)
        self._warmer.done.connect(lambda names: self.ready.emit(names))
        self._warmer.failed.connect(self.error.emit)
        self._warmer.start()

    def shutdown(self) -> None:
        """Wait for the background warm-up thread before the app exits.

        Prevents a crash-on-close if models are still loading. Returns: None.
        """
        if self._warmer and self._warmer.isRunning():
            self._warmer.wait(5000)

    # --- LRU cache ---------------------------------------------------------
    def invalidate_cache(self) -> None:
        """Drop every cached model so the next classify reloads from disk.

        Must be called whenever a model is (re)trained, renamed, or deleted —
        the cache is keyed by name, so a retrain under the same name would
        otherwise keep serving the old weights until the app restarts. Returns:
        None.
        """
        self._cache.clear()

    def _ensure_loaded(self, name: str) -> _LoadedModel:
        """Return a model from the cache, loading + evicting as needed.

        LRU: a cache hit is moved to the most-recent end; a miss loads from disk
        and, if the cache is full, drops the least-recently-used model. Returns:
        the _LoadedModel for `name`.
        """
        lm = self._cache.get(name)
        if lm is not None:
            self._cache.move_to_end(name)         # mark most-recently-used
            return lm
        lm = _load_module(name)                   # cache miss → load from disk
        self._cache[name] = lm
        self._cache.move_to_end(name)
        while len(self._cache) > self.cache_size:  # evict least-recently-used
            self._cache.popitem(last=False)
        return lm

    # --- classification ----------------------------------------------------
    def classify(self, image_path: str, want_heatmap: bool = False) -> ClassificationResult:
        """Run every registered model on one image; winner = lowest score.

        want_heatmap: render the winner's overlay (in memory only, no file) —
        skipped by default because production runs don't need it.
        Runs synchronously on the caller's thread. Returns: a populated
        ClassificationResult (ok=False + error on failure).
        """
        names = self.model_names
        if not names:
            return ClassificationResult(ok=False, error="No models registered")
        try:
            x = _preprocess(image_path)
        except Exception as e:
            return ClassificationResult(ok=False, error=f"Bad image: {e}")

        scores: dict[str, float] = {}
        confidences: dict[str, float] = {}
        maps: dict[str, np.ndarray] = {}
        try:
            with torch.no_grad():
                for name in names:
                    lm = self._ensure_loaded(name)
                    out = lm.module.model(x)
                    score = float(out.pred_score.reshape(-1)[0])
                    scores[name] = score
                    # Normalise this score into 0..1 using THIS model's own range,
                    # so confidences are comparable across differently scaled models.
                    span = (lm.img_max - lm.img_min) or 1.0
                    confidences[name] = float(min(max(1.0 - (score - lm.img_min) / span, 0.0), 1.0))
                    if want_heatmap:
                        amap = out.anomaly_map
                        maps[name] = amap.reshape(amap.shape[-2], amap.shape[-1]).cpu().numpy()
        except Exception as e:
            traceback.print_exc()
            return ClassificationResult(ok=False, error=f"Inference error: {e}")

        # Winner = highest per-model confidence (best fit relative to each
        # model's own normal range) — NOT lowest raw score, which isn't
        # comparable across models with different score scales.
        winner = max(confidences, key=confidences.get)
        confidence = confidences[winner]

        # Novelty gate: if even the best-confidence model is below the threshold,
        # the image doesn't belong to any trained model → "not a known model".
        matched = confidence >= self.min_confidence

        heatmap = (
            self._render_overlay(image_path, maps[winner]) if want_heatmap else None
        )
        return ClassificationResult(
            ok=True, model=winner, score=scores[winner],
            confidence=confidence, scores=scores, confidences=confidences,
            heatmap=heatmap, matched=matched,
        )

    def _render_overlay(self, image_path: str, amap: np.ndarray) -> np.ndarray | None:
        """Colour the anomaly map and blend it over the input — in memory ONLY,
        nothing is written to disk (production storage must not grow).
        Returns: (H,W,3) uint8 RGB array, or None on failure (caller falls back
        to the plain image)."""
        try:
            base = Image.open(image_path).convert("RGB").resize((_IMG_SIZE, _IMG_SIZE))
            m = amap.astype(np.float32)
            m = (m - m.min()) / ((m.max() - m.min()) or 1.0)
            heat = _jet(m)
            return (0.55 * np.asarray(base) + 0.45 * heat).astype(np.uint8)
        except Exception:
            traceback.print_exc()
            return None


def _jet(x: np.ndarray) -> np.ndarray:
    """Map a 0..1 array to a blue→red 'jet'-like RGB image (no matplotlib).
    Returns: (H,W,3) uint8 array."""
    r = np.clip(1.5 - np.abs(4 * x - 3), 0, 1)
    g = np.clip(1.5 - np.abs(4 * x - 2), 0, 1)
    b = np.clip(1.5 - np.abs(4 * x - 1), 0, 1)
    return (np.stack([r, g, b], axis=-1) * 255).astype(np.uint8)
