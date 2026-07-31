"""AppState — the single source of truth for model data + settings.

It's a QObject so it can emit Qt signals when things change: the UI, inference,
and serial layers react through signals instead of holding references to each
other. Models are backed by the per-model registry; settings by config.py.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, Signal

from . import config, registry
from .auth import User
from .models import AppSettings, CameraSettings, ModbusSettings, ModelData


class AppState(QObject):
    # Emitted after the model set changes (edit, train, delete) so inference can
    # pick up new/edited models and dropdowns repopulate.
    models_changed = Signal()
    # Emitted after Modbus settings change so the serial handler can re-open.
    settings_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._settings, self._app_settings, self._camera_settings = config.load_settings()
        # --- login session ---
        self._user: User | None = None      # who is logged in (None = nobody)
        self._last_active: float = 0.0      # monotonic time of last protected action

    # --- session ------------------------------------------------------------
    @property
    def current_user(self) -> User | None:
        """The logged-in user, or None if nobody is (or the session expired).

        Expiry: `session_timeout_minutes` after the last protected action
        (0 = never expires). Returns: User | None.
        """
        if self._user is None:
            return None
        timeout_min = self._app_settings.session_timeout_minutes
        if timeout_min > 0 and (time.monotonic() - self._last_active) > timeout_min * 60:
            self._user = None               # idle too long → session lapsed
            return None
        return self._user

    def login(self, user: User) -> None:
        """Start a session for `user`. Returns: None."""
        self._user = user
        self.touch()

    def touch(self) -> None:
        """Mark activity, restarting the idle timeout. Returns: None."""
        self._last_active = time.monotonic()

    @property
    def is_developer(self) -> bool:
        """True if the current session belongs to a developer. Returns: bool."""
        user = self.current_user
        return bool(user and user.is_developer)

    @property
    def username(self) -> str:
        """Name of the logged-in user, or '' if nobody. Returns: str."""
        user = self.current_user
        return user.username if user else ""

    # --- reads -------------------------------------------------------------
    @property
    def models(self) -> list[ModelData]:
        """Current models, read fresh from the registry. Returns: list[ModelData]."""
        return registry.list_models()

    @property
    def settings(self) -> ModbusSettings:
        """Current Modbus settings. Returns: ModbusSettings."""
        return self._settings

    @property
    def app_settings(self) -> AppSettings:
        """Current app settings (min images, LRU size). Returns: AppSettings."""
        return self._app_settings

    @property
    def camera_settings(self) -> CameraSettings:
        """Current HTTP camera settings (url, timeout). Returns: CameraSettings."""
        return self._camera_settings

    def model_by_name(self, name: str) -> ModelData | None:
        """Look up a model's dims by name (for the PLC frame). Returns: ModelData | None."""
        for m in registry.list_models():
            if m.name == name:
                return m
        return None

    # --- writes ------------------------------------------------------------
    def set_models(self, models: list[ModelData]) -> None:
        """Persist edited dimensions for existing models, then notify.

        Only updates metadata (name is fixed to the folder). Returns: None.
        """
        for m in models:
            registry.save_meta(m)
        self.models_changed.emit()

    def notify_models_changed(self) -> None:
        """Fire models_changed after training/deleting adds/removes a model."""
        self.models_changed.emit()

    def save(self) -> None:
        """Persist the current settings objects as-is.

        For in-place tweaks to app_settings (e.g. the mask threshold) that
        don't need the settings_changed signal. Returns: None.
        """
        config.save_settings(self._settings, self._app_settings, self._camera_settings)

    def set_settings(self, settings: ModbusSettings) -> None:
        """Replace Modbus settings, persist, notify. Returns: None."""
        self._settings = settings
        config.save_settings(self._settings, self._app_settings, self._camera_settings)
        self.settings_changed.emit()
