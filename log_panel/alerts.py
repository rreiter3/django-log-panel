from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from log_panel import conf
from log_panel.signals import ThresholdAlertEvent, log_threshold_reached
from log_panel.types import LogLevel

ThresholdCountCallback = Callable[[tuple[str, ...], datetime, datetime], int]

_dispatch_local = threading.local()


@dataclass(frozen=True, slots=True)
class ThresholdConfig:
    level: LogLevel
    threshold: int


def maybe_emit_threshold_signal(
    *,
    sender: object,
    logger_name: str,
    record_level: str,
    timestamp: datetime,
    message: str,
    module: str,
    pathname: str,
    line_number: int,
    count_matching_records: ThresholdCountCallback,
) -> None:
    """Emit the threshold signal when a log level count crosses its configured limit."""
    threshold_config = get_threshold_config(record_level)
    if threshold_config is None:
        return
    if getattr(_dispatch_local, "dispatching", False):
        return
    if not log_threshold_reached.has_listeners(sender):
        return

    window_end: datetime = timestamp
    window_start: datetime = timestamp - timedelta(hours=1)
    matching_count: int = count_matching_records(
        (threshold_config.level.value,),
        window_start,
        window_end,
    )
    if matching_count != threshold_config.threshold:
        return

    event = ThresholdAlertEvent(
        logger_name=logger_name,
        threshold_level=threshold_config.level,
        record_level=LogLevel(record_level),
        threshold=threshold_config.threshold,
        matching_count=matching_count,
        timestamp=timestamp,
        window_start=window_start,
        window_end=window_end,
        message=message,
        module=module,
        pathname=pathname,
        line_number=line_number,
    )

    try:
        _dispatch_local.dispatching = True
        log_threshold_reached.send_robust(sender=sender, event=event)
    finally:
        _dispatch_local.dispatching = False


def get_threshold_config(record_level: str) -> ThresholdConfig | None:
    """Return the threshold config for *record_level*, or None if the level is not configured."""
    thresholds = conf.get_thresholds()
    threshold = thresholds.get(record_level)
    if threshold is None:
        return None
    try:
        level = LogLevel(record_level)
    except ValueError:
        return None
    return ThresholdConfig(level=level, threshold=threshold)
