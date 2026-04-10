from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any, Literal, cast


class LogLevel(StrEnum):
    """Valid log level values."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# Levels that represent error conditions.
ERROR_LEVELS: tuple[Literal[LogLevel.ERROR], Literal[LogLevel.CRITICAL]] = (
    LogLevel.ERROR,
    LogLevel.CRITICAL,
)


class SlotStatus(StrEnum):
    """Colour-coded status for a single timeline slot."""

    ERROR = "error"
    WARNING = "warning"
    OK = "ok"
    EMPTY = "empty"


class RangeUnit(StrEnum):
    """Supported timeline bucket sizes."""

    HOUR = "hour"
    DAY = "day"


@dataclass(frozen=True, slots=True)
class RangeConfig:
    """Typed configuration for a single admin timeline range."""

    delta: timedelta
    unit: RangeUnit
    slots: int
    format: str
    label: str | None = None

    @classmethod
    def from_value(cls, value: "RangeConfig | Mapping[str, Any]") -> "RangeConfig":
        """Normalise a raw settings value into a typed range config."""
        if isinstance(value, cls):
            return value

        raw: Mapping[str, Any] = cast(typ=Mapping[str, Any], val=value)
        return cls(
            delta=raw["delta"],
            unit=RangeUnit(value=raw["unit"]),
            slots=raw["slots"],
            format=raw["format"],
            label=raw.get("label"),
        )
