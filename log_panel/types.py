import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, cast


class LogLevel(StrEnum):
    """Valid log level values, derived from Python's logging module constants."""

    CRITICAL = logging.getLevelName(level=logging.CRITICAL)
    ERROR = logging.getLevelName(level=logging.ERROR)
    WARNING = logging.getLevelName(level=logging.WARNING)
    INFO = logging.getLevelName(level=logging.INFO)
    DEBUG = logging.getLevelName(level=logging.DEBUG)
    NOTSET = logging.getLevelName(level=logging.NOTSET)


# Levels that represent error conditions.
ERROR_LEVELS: tuple[Literal[LogLevel.ERROR], Literal[LogLevel.CRITICAL]] = (
    LogLevel.ERROR,
    LogLevel.CRITICAL,
)


class CardFilter(StrEnum):
    """Valid filter values for the cards dashboard."""

    ALL = ""
    ERRORS = "errors"
    WARNINGS = "warnings"


class SlotStatus(StrEnum):
    """Color-coded status for a single timeline slot."""

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


@dataclass
class LogFilters:
    logger_names: list[str] | None = None
    levels: list[str] | None = None
    search: str = ""
    timestamp_from: datetime | None = None
    timestamp_to: datetime | None = None


@dataclass(frozen=True, slots=True)
class ThresholdAlertEvent:
    """Payload emitted when a logger crosses a configured threshold."""

    logger_name: str
    threshold_level: LogLevel
    record_level: LogLevel
    threshold: int
    matching_count: int
    timestamp: datetime
    window_start: datetime
    window_end: datetime
    message: str
    module: str
    pathname: str
    line_number: int


@dataclass(frozen=True, slots=True)
class MessageParts:
    """Structured representation of a stored log message."""

    preview: str
    chunks: list[str]
    size: int

    @property
    def is_chunked(self) -> bool:
        return bool(self.chunks)
