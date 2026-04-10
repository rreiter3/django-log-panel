from dataclasses import dataclass
from datetime import datetime

from django.dispatch import Signal

from log_panel.types import LogLevel


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


log_threshold_reached = Signal()

__all__ = ["ThresholdAlertEvent", "log_threshold_reached"]
