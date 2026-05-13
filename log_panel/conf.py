from datetime import timedelta
from types import ModuleType
from typing import Any

from django.conf import settings

from log_panel.types import RangeConfig, RangeUnit

_UNSET = object()
_backend_cache: Any = _UNSET


def reset_backend_cache() -> None:
    """Reset the cached backend instance — use in tests and after settings changes."""
    global _backend_cache
    _backend_cache = _UNSET


DEFAULTS: dict[str, Any] = {
    # Backend — None means auto-detect
    "BACKEND": None,
    # Storage
    "DATABASE_ALIAS": None,
    "MESSAGE_PREVIEW_LENGTH": 16384,
    "MESSAGE_CHUNK_SIZE": 262144,
    "RETENTION_DAYS": 90,
    "CACHE_TIMEOUT_SECONDS": 30,
    # Auto-attach handler to root logger on startup
    "ATTACH_ROOT_HANDLER": True,
    "LOG_LEVEL": "DEBUG",
    # Alert thresholds per log level — set to None to disable a level
    "THRESHOLDS": {
        "WARNING": 1,
        "ERROR": 1,
        "CRITICAL": 1,
    },
    # UI
    "TITLE": "Log Panel",
    "PAGE_SIZE": 10,
    "LEVEL_COLORS": {
        "NOTSET": "#888",
        "DEBUG": "#888",
        "INFO": "#417690",
        "WARNING": "#c0a000",
        "ERROR": "#c47900",
        "CRITICAL": "#ba2121",
    },
    # Access control — None means any active staff user may view the panel
    "PERMISSION_CALLBACK": None,
    "RANGES": {
        "24h": RangeConfig(
            delta=timedelta(hours=24),
            unit=RangeUnit.HOUR,
            slots=24,
            format="%d. %H:00",
            label="Last 24 hours",
        ),
        "30d": RangeConfig(
            delta=timedelta(days=30),
            unit=RangeUnit.DAY,
            slots=30,
            format="%b %d",
            label="Last 30 days",
        ),
        "90d": RangeConfig(
            delta=timedelta(days=90),
            unit=RangeUnit.DAY,
            slots=90,
            format="%b %d",
            label="Last 90 days",
        ),
    },
}


def get_user_config() -> dict[str, Any]:
    """Return the user configuration dict from Django settings, or an empty dict."""
    return getattr(settings, "LOG_PANEL", {})


def get_setting(key: str) -> Any:
    """Return a value from LOG_PANEL in Django settings, falling back to DEFAULTS."""
    user_config: dict[str, Any] = get_user_config()
    return user_config.get(key, DEFAULTS[key])


def get_thresholds() -> dict[str, int | None]:
    """Return per-level alert thresholds, merging user config with defaults."""
    user_config: dict[str, Any] = get_user_config()
    user_thresholds: dict[str, int | None] = user_config.get("THRESHOLDS", {})
    return {**DEFAULTS["THRESHOLDS"], **user_thresholds}


def get_ranges() -> dict[str, RangeConfig]:
    """Return timeline range settings normalised into typed configs."""
    raw_ranges: dict[str, RangeConfig | dict[str, Any]] = get_setting(key="RANGES")
    return {key: RangeConfig.from_value(value) for key, value in raw_ranges.items()}


def get_database_alias() -> str | None:
    """Return the database alias to use for log storage, or None if not configured."""
    return get_setting(key="DATABASE_ALIAS")


def get_backend():
    """
    Instantiate and return the configured backend, or None if not configured.

    The result is cached for the lifetime of the process so that the underlying
    connection pool is reused across requests.

    Resolution order:
    1. LOG_PANEL['BACKEND'] dotted class path (explicit override).
    2. OrmBackend if LOG_PANEL['DATABASE_ALIAS'] is set.
    3. None — admin will show an unconfigured state.

    Returns:
        A LogsBackend instance, or None.
    """
    global _backend_cache
    if _backend_cache is not _UNSET:
        return _backend_cache

    from log_panel.backends.base import LogsBackend

    explicit: str | None = get_setting(key="BACKEND")
    if explicit:
        from importlib import import_module

        module_path, class_name = explicit.rsplit(".", 1)
        module: ModuleType = import_module(name=module_path)
        cls: Any = getattr(module, class_name)

        backend: LogsBackend = cls()
        _backend_cache = backend
        return _backend_cache

    if get_database_alias():
        from log_panel.backends.sql import OrmBackend

        _backend_cache = OrmBackend()
        return _backend_cache

    _backend_cache = None
    return None


def get_level_colors() -> dict[str, str]:
    """
    Return the level color map used for both CSS generation and the filter dropdown.

    Merges user-configured ``LOG_PANEL['LEVEL_COLORS']`` with defaults, so only
    overridden or added levels need to be specified.
    """
    user_config: dict[str, Any] = getattr(settings, "LOG_PANEL", {})
    user_colors: dict[str, str] = user_config.get("LEVEL_COLORS", {})
    return {**DEFAULTS["LEVEL_COLORS"], **user_colors}


def get_permission_callback():
    """
    Return the configured permission callable, or None.

    The setting must be a dotted path to a callable ``(request: HttpRequest) -> bool``.
    When not configured, the panel falls back to allowing any active staff user.

    Raises:
        ImproperlyConfigured: if the dotted path is set but cannot be imported.
    """
    from django.core.exceptions import ImproperlyConfigured

    dotted: str | None = get_setting(key="PERMISSION_CALLBACK")
    if not dotted:
        return None
    try:
        from importlib import import_module

        module_path, func_name = dotted.rsplit(".", 1)
        module: ModuleType = import_module(name=module_path)
        return getattr(module, func_name)
    except (ImportError, AttributeError, ValueError) as exc:
        raise ImproperlyConfigured(
            f"LOG_PANEL['PERMISSION_CALLBACK'] = {dotted!r} could not be imported: {exc}"
        ) from exc
