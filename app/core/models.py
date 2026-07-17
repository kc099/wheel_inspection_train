"""Plain data classes mirroring the WPF app's in-memory model (README §4).

These are dumb containers: no behavior, no Qt. They exist so every other
module (UI, inference, serial) passes around the *same* typed shapes instead
of loose dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ModelData:
    """One wheel model the operator can inspect against.

    The app is recognition-only: it identifies WHICH model a component is
    (lowest anomaly score wins). There is no OK/NG defect threshold.

    name      : must match a model folder in models/ (e.g. "model1"); the PLC
                model_id and the inference lookup both key off this.
    diameter  : wheel diameter in mm, reported to the PLC when recognized.
    height    : wheel height in mm, reported to the PLC when recognized.
    """

    name: str
    diameter: float = 0.0
    height: float = 0.0

    def to_dict(self) -> dict:
        """Serialize for the registry's meta.json. Returns a JSON-safe dict."""
        return {
            "name": self.name,
            "diameter": self.diameter,
            "height": self.height,
        }

    @staticmethod
    def from_dict(d: dict) -> "ModelData":
        """Rebuild from a meta.json entry. Returns a ModelData.
        (Ignores any legacy 'threshold' key from older config files.)"""
        return ModelData(
            name=str(d.get("name", "")),
            diameter=float(d.get("diameter", 0.0)),
            height=float(d.get("height", 0.0)),
        )


@dataclass
class ModbusSettings:
    """Serial / Modbus link configuration (README §4). Defaults match the spec."""

    protocol: str = "Modbus RTU"
    ip_address: str = "127.0.0.1"   # used only for a future TCP transport
    address1: int = 1
    address2: int = 2
    address3: int = 3
    delay_ms: int = 100
    com_port: str = ""              # "" ⇒ auto-detect the first available port
    baud_rate: int = 19200
    slave_id: int = 1

    def to_dict(self) -> dict:
        """Serialize for config.json. Returns a JSON-safe dict."""
        return {
            "protocol": self.protocol,
            "ip_address": self.ip_address,
            "address1": self.address1,
            "address2": self.address2,
            "address3": self.address3,
            "delay_ms": self.delay_ms,
            "com_port": self.com_port,
            "baud_rate": self.baud_rate,
            "slave_id": self.slave_id,
        }

    @staticmethod
    def from_dict(d: dict) -> "ModbusSettings":
        """Rebuild from config.json. Missing keys fall back to defaults."""
        base = ModbusSettings()
        return ModbusSettings(
            protocol=str(d.get("protocol", base.protocol)),
            ip_address=str(d.get("ip_address", base.ip_address)),
            address1=int(d.get("address1", base.address1)),
            address2=int(d.get("address2", base.address2)),
            address3=int(d.get("address3", base.address3)),
            delay_ms=int(d.get("delay_ms", base.delay_ms)),
            com_port=str(d.get("com_port", base.com_port)),
            baud_rate=int(d.get("baud_rate", base.baud_rate)),
            slave_id=int(d.get("slave_id", base.slave_id)),
        )


@dataclass
class ClassificationResult:
    """Everything one inspection produces (recognition-only).

    Single-image and batch inspection both consume this exact shape, so the
    likelihood math in verdict.py never has to care which path produced it.

    ok         : did classification run at all? If False, `error` says why.
    model      : recognized (winning) model name — lowest raw anomaly score.
    score      : the winner's raw anomaly score (lower = better fit).
    confidence : 0..1, derived from the winner's min/max calibration.
    accuracy   : optional percentage, None if not computed.
    scores     : raw anomaly score from EVERY loaded model {name: score}.
    heatmap    : the winner's anomaly overlay as an in-memory (H,W,3) uint8 RGB
                 array, or None when not requested. Never written to disk.
    """

    ok: bool = True
    error: str | None = None
    model: str = ""
    score: float = 0.0
    confidence: float = 0.0
    accuracy: float | None = None
    scores: dict[str, float] = field(default_factory=dict)
    # Per-model confidence (0..1), each score normalised by THAT model's own
    # range. Winner = highest confidence — a fair comparison across models with
    # different raw-score scales.
    confidences: dict[str, float] = field(default_factory=dict)
    heatmap: "object | None" = None      # np.ndarray, kept untyped: no deps here
    # matched=False means even the best-fit model isn't confident enough, i.e.
    # the image doesn't belong to ANY trained model (novelty rejection).
    matched: bool = True


@dataclass
class AppSettings:
    """Non-Modbus app settings persisted alongside ModbusSettings.

    min_training_images : minimum good samples required to train a model
                          (operator-facing guard; you asked to keep this
                          adjustable). The trainer uses up to
                          trainer.MAX_UPLOAD_IMAGES uploads and adds rotated
                          copies of each.
    lru_cache_size      : max models kept resident in memory at once. Default
                          100 = the whole ceiling, so nothing evicts below that;
                          lower it on a RAM-tight box (see docs/model_storage_design.md §5).
    """

    min_training_images: int = 10
    lru_cache_size: int = 100
    # Recognition sensitivity: the winner must reach at least this confidence
    # (0..1) to count as a match; below it the image is "not a known model".
    # Higher = stricter (more NA). Foreign images sit near 0, genuine members
    # near 1, so ~0.3 cleanly separates them. Tune with real parts.
    recognition_min_confidence: float = 0.3
    # Login stays valid this many minutes after the last protected action.
    # 0 = never expires (session lasts until the app closes).
    session_timeout_minutes: int = 5
    # Maximum number of user profiles (1 developer + operators).
    max_profiles: int = 4

    def to_dict(self) -> dict:
        return {
            "min_training_images": self.min_training_images,
            "lru_cache_size": self.lru_cache_size,
            "recognition_min_confidence": self.recognition_min_confidence,
            "session_timeout_minutes": self.session_timeout_minutes,
            "max_profiles": self.max_profiles,
        }

    @staticmethod
    def from_dict(d: dict) -> "AppSettings":
        base = AppSettings()
        return AppSettings(
            min_training_images=int(d.get("min_training_images", base.min_training_images)),
            lru_cache_size=int(d.get("lru_cache_size", base.lru_cache_size)),
            recognition_min_confidence=float(
                d.get("recognition_min_confidence", base.recognition_min_confidence)
            ),
            session_timeout_minutes=int(
                d.get("session_timeout_minutes", base.session_timeout_minutes)
            ),
            max_profiles=int(d.get("max_profiles", base.max_profiles)),
        )


@dataclass
class CameraSettings:
    """HTTP camera stream configuration (persisted under 'http_streamer').

    url     : the MJPEG/HTTP stream URL of the machine's IP camera.
    timeout : seconds to wait when opening/reading the stream before erroring.
    """

    url: str = "http://192.168.100.50:8080/stream-hd"
    timeout: int = 5

    def to_dict(self) -> dict:
        return {"url": self.url, "timeout": self.timeout}

    @staticmethod
    def from_dict(d: dict) -> "CameraSettings":
        base = CameraSettings()
        return CameraSettings(
            url=str(d.get("url", base.url)),
            timeout=int(d.get("timeout", base.timeout)),
        )
