import pytest
from django.test import override_settings

from log_panel.conf import get_backend, get_database_alias, get_ranges, get_setting
from log_panel.types import RangeConfig


def test_get_setting_returns_default_when_not_configured():
    assert get_setting("TTL_DAYS") == 90


@override_settings(LOG_PANEL={"TTL_DAYS": 30})
def test_get_setting_returns_user_value():
    assert get_setting("TTL_DAYS") == 30


@override_settings(LOG_PANEL={"TTL_DAYS": 14})
def test_get_setting_falls_back_for_partial_config():
    assert get_setting("TTL_DAYS") == 14
    assert get_setting("PAGE_SIZE") == 10
    assert get_setting("TITLE") == "Log Panel"


def test_get_ranges_returns_typed_range_configs():
    ranges = get_ranges()
    for cfg in ranges.values():
        assert isinstance(cfg, RangeConfig)


def test_get_ranges_returns_all_default_keys():
    ranges = get_ranges()
    assert set(ranges.keys()) == {"24h", "30d", "90d"}


@override_settings(
    LOG_PANEL={
        "RANGES": {
            "1h": {
                "delta": __import__("datetime").timedelta(hours=1),
                "unit": "hour",
                "slots": 1,
                "format": "%H:00",
                "label": "Last hour",
            }
        }
    }
)
def test_get_ranges_normalises_dict_values():
    ranges = get_ranges()
    assert "1h" in ranges
    assert isinstance(ranges["1h"], RangeConfig)
    assert ranges["1h"].label == "Last hour"


def test_get_database_alias_returns_none_by_default():
    assert get_database_alias() is None


@override_settings(LOG_PANEL={"DATABASE_ALIAS": "logs"})
def test_get_database_alias_returns_configured_alias():
    assert get_database_alias() == "logs"


def test_get_backend_returns_none_when_nothing_configured():
    assert get_backend() is None


@override_settings(LOG_PANEL={"DATABASE_ALIAS": "default"})
def test_get_backend_returns_sql_backend_when_alias_configured():
    from log_panel.backends.sql import SqlBackend

    backend = get_backend()
    assert isinstance(backend, SqlBackend)


@override_settings(LOG_PANEL={"BACKEND": "log_panel.backends.sql.SqlBackend"})
def test_get_backend_returns_explicit_backend_when_backend_key_set():
    from log_panel.backends.sql import SqlBackend

    backend = get_backend()
    assert isinstance(backend, SqlBackend)


@override_settings(
    LOG_PANEL={
        "BACKEND": "log_panel.backends.sql.SqlBackend",
        "DATABASE_ALIAS": "default",
    }
)
def test_get_backend_explicit_takes_priority_over_alias():
    from log_panel.backends.sql import SqlBackend

    backend = get_backend()
    assert isinstance(backend, SqlBackend)


@override_settings(
    LOG_PANEL={"DATABASE_ALIAS": "logs"},
    DATABASE_ROUTERS=[],
)
def test_ready_raises_improperly_configured_when_alias_set_without_router():
    from django.core.exceptions import ImproperlyConfigured

    import log_panel
    from log_panel.apps import LogPanelConfig

    config = LogPanelConfig("log_panel", log_panel)
    with pytest.raises(ImproperlyConfigured, match="LogsRouter"):
        config.ready()


@override_settings(
    LOG_PANEL={"DATABASE_ALIAS": "logs"},
    DATABASE_ROUTERS=["log_panel.routers.LogsRouter"],
)
def test_ready_does_not_raise_when_router_is_present():
    import log_panel
    from log_panel.apps import LogPanelConfig

    config = LogPanelConfig("log_panel", log_panel)
    config.ready()


def test_thresholds_defaults():
    from log_panel.conf import get_thresholds

    thresholds = get_thresholds()
    assert thresholds["WARNING"] == 1
    assert thresholds["ERROR"] == 1
    assert thresholds["CRITICAL"] == 1


@override_settings(LOG_PANEL={"THRESHOLDS": {"ERROR": 5, "WARNING": 3}})
def test_thresholds_can_be_overridden():
    from log_panel.conf import get_thresholds

    thresholds = get_thresholds()
    assert thresholds["ERROR"] == 5
    assert thresholds["WARNING"] == 3
    assert thresholds["CRITICAL"] == 1  # default preserved


@override_settings(LOG_PANEL={"THRESHOLDS": {"WARNING": None}})
def test_threshold_can_be_disabled():
    from log_panel.conf import get_thresholds

    assert get_thresholds()["WARNING"] is None


def test_ready_does_not_raise_when_no_alias_configured():
    import log_panel
    from log_panel.apps import LogPanelConfig

    config = LogPanelConfig("log_panel", log_panel)
    config.ready()
