import dataclasses
from datetime import timedelta

import pytest

from log_panel.types import (
    ERROR_LEVELS,
    LogLevel,
    RangeConfig,
    RangeUnit,
    SlotStatus,
)


def test_log_level_values():
    assert LogLevel.DEBUG == "DEBUG"
    assert LogLevel.INFO == "INFO"
    assert LogLevel.WARNING == "WARNING"
    assert LogLevel.ERROR == "ERROR"
    assert LogLevel.CRITICAL == "CRITICAL"


def test_slot_status_values():
    assert SlotStatus.ERROR == "error"
    assert SlotStatus.WARNING == "warning"
    assert SlotStatus.OK == "ok"
    assert SlotStatus.EMPTY == "empty"


def test_range_unit_values():
    assert RangeUnit.HOUR == "hour"
    assert RangeUnit.DAY == "day"


def test_error_levels_contains_error_and_critical():
    assert LogLevel.ERROR in ERROR_LEVELS
    assert LogLevel.CRITICAL in ERROR_LEVELS


def test_error_levels_does_not_contain_warning():
    assert LogLevel.WARNING not in ERROR_LEVELS
    assert LogLevel.INFO not in ERROR_LEVELS
    assert LogLevel.DEBUG not in ERROR_LEVELS


def test_range_config_from_value_with_dict():
    raw = {
        "delta": timedelta(hours=24),
        "unit": "hour",
        "slots": 24,
        "format": "%H:00",
        "label": "Last 24 hours",
    }
    range_config = RangeConfig.from_value(raw)
    assert isinstance(range_config, RangeConfig)
    assert range_config.delta == timedelta(hours=24)
    assert range_config.unit is RangeUnit.HOUR
    assert range_config.slots == 24
    assert range_config.format == "%H:00"
    assert range_config.label == "Last 24 hours"


def test_range_config_from_value_with_existing_instance():
    original = RangeConfig(
        delta=timedelta(days=30),
        unit=RangeUnit.DAY,
        slots=30,
        format="%b %d",
        label="Last 30 days",
    )
    result = RangeConfig.from_value(original)
    assert result is original


def test_range_config_from_value_normalises_unit_string():
    raw = {"delta": timedelta(hours=1), "unit": "hour", "slots": 1, "format": "%H:00"}
    range_config = RangeConfig.from_value(raw)
    assert range_config.unit is RangeUnit.HOUR

    raw_day = {"delta": timedelta(days=1), "unit": "day", "slots": 1, "format": "%b %d"}
    range_config_day = RangeConfig.from_value(raw_day)
    assert range_config_day.unit is RangeUnit.DAY


def test_range_config_from_value_label_defaults_to_none():
    raw = {"delta": timedelta(hours=1), "unit": "hour", "slots": 1, "format": "%H:00"}
    range_config = RangeConfig.from_value(raw)
    assert range_config.label is None


def test_range_config_is_frozen():
    range_config = RangeConfig(
        delta=timedelta(hours=24),
        unit=RangeUnit.HOUR,
        slots=24,
        format="%H:00",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        range_config.slots = 99  # ty: ignore[invalid-assignment]
