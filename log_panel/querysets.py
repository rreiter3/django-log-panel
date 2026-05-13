from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, tzinfo
from typing import Any, cast

from django.db import models
from django.db.models import Count, Max, Q
from django.db.models.functions import TruncDay, TruncHour
from django.utils import timezone as django_timezone

from log_panel.types import ERROR_LEVELS, LogFilters, LogLevel, RangeConfig, RangeUnit


def levels_at_or_above(min_level: str) -> list[str]:
    """Return all LogLevel values at or above *min_level* by numeric severity."""
    level_value: Any = logging.getLevelName(level=min_level.upper())
    if not isinstance(level_value, int):
        raise ValueError(
            f"Unknown log level: {min_level!r}. "
            f"Valid levels: {[level.value for level in LogLevel if level != LogLevel.NOTSET]}"
        )
    return [
        level.value
        for level in LogLevel
        if level != LogLevel.NOTSET
        and isinstance(logging.getLevelName(level=level.value), int)
        and logging.getLevelName(level=level.value) >= level_value
    ]


class LogQueryset:
    """Chainable log filter accumulator evaluated against the active backend."""

    def __init__(self, backend, filters: LogFilters | None = None) -> None:
        self._backend = backend
        self._filters: LogFilters = filters or LogFilters()

    def filter(
        self,
        *,
        logger_names: list[str] | None = None,
        min_level: str | None = None,
        search: str | None = None,
        timestamp_from: datetime | None = None,
        timestamp_to: datetime | None = None,
    ) -> LogQueryset:
        """Further filter the logs by logger name, minimum level, message content, or timestamp range."""
        updated: LogFilters = replace(self._filters)
        if logger_names is not None:
            updated.logger_names: list[str] = logger_names
        if min_level is not None:
            updated.levels: list[str] = levels_at_or_above(min_level)
        if search is not None:
            updated.search: str = search
        if timestamp_from is not None:
            updated.timestamp_from: datetime = timestamp_from
        if timestamp_to is not None:
            updated.timestamp_to: datetime = timestamp_to
        return LogQueryset(backend=self._backend, filters=updated)

    def _tz(self):
        return django_timezone.get_default_timezone()

    def __len__(self) -> int:
        if self._backend is None:
            return 0
        return self._backend.count_logs(
            logger_names=self._filters.logger_names,
            levels=self._filters.levels,
            search=self._filters.search,
            timestamp_from=self._filters.timestamp_from,
            timestamp_to=self._filters.timestamp_to,
        )

    def __iter__(self):
        if self._backend is None:
            return iter([])
        return iter(
            self._backend.query_logs(
                logger_names=self._filters.logger_names,
                levels=self._filters.levels,
                search=self._filters.search,
                offset=0,
                limit=None,
                app_timezone=self._tz(),
                timestamp_from=self._filters.timestamp_from,
                timestamp_to=self._filters.timestamp_to,
            )
        )

    def __getitem__(self, key):
        if isinstance(key, slice):
            if self._backend is None:
                return []
            start = key.start or 0
            stop = key.stop
            limit = None if stop is None else stop - start
            return self._backend.query_logs(
                logger_names=self._filters.logger_names,
                levels=self._filters.levels,
                search=self._filters.search,
                offset=start,
                limit=limit,
                app_timezone=self._tz(),
                timestamp_from=self._filters.timestamp_from,
                timestamp_to=self._filters.timestamp_to,
            )
        if isinstance(key, int):
            if key < 0:
                raise ValueError("Negative indexing is not supported")
            result = self._backend.query_logs(
                logger_names=self._filters.logger_names,
                levels=self._filters.levels,
                search=self._filters.search,
                offset=key,
                limit=1,
                app_timezone=self._tz(),
                timestamp_from=self._filters.timestamp_from,
                timestamp_to=self._filters.timestamp_to,
            )
            if not result:
                raise IndexError("index out of range")
            return result[0]
        raise TypeError(f"indices must be integers or slices, not {type(key).__name__}")


class LogQuerySet(models.QuerySet):
    """Aggregation helpers for log analytics."""

    def count_threshold_matches(
        self,
        *,
        logger_name: str,
        levels: tuple[str, ...],
        window_start: datetime,
        window_end: datetime,
    ) -> int:
        """Count matching records for one logger inside an inclusive time window."""
        return self.filter(
            logger_name=logger_name,
            level__in=levels,
            timestamp__gte=window_start,
            timestamp__lte=window_end,
        ).count()

    def with_total(self) -> LogQuerySet:
        """Annotate the total number of log entries per group."""
        return self.annotate(total=Count("id"))

    def with_total_errors(self) -> LogQuerySet:
        """Annotate the all-time count of ERROR and CRITICAL entries per group."""
        return self.annotate(total_errors=Count("id", filter=Q(level__in=ERROR_LEVELS)))

    def with_total_warnings(self) -> LogQuerySet:
        """Annotate the all-time count of WARNING entries per group."""
        return self.annotate(
            total_warnings=Count("id", filter=Q(level=LogLevel.WARNING))
        )

    def with_recent_errors(self, *, one_hour_ago: datetime) -> LogQuerySet:
        """Annotate the count of ERROR/CRITICAL entries emitted since *one_hour_ago*."""
        return self.annotate(
            recent_errors=Count(
                "id", filter=Q(level__in=ERROR_LEVELS, timestamp__gte=one_hour_ago)
            )
        )

    def with_recent_warnings(self, *, one_hour_ago: datetime) -> LogQuerySet:
        """Annotate the count of WARNING entries emitted since *one_hour_ago*."""
        return self.annotate(
            recent_warnings=Count(
                "id", filter=Q(level=LogLevel.WARNING, timestamp__gte=one_hour_ago)
            )
        )

    def with_last_seen(self) -> LogQuerySet:
        """Annotate the timestamp of the most recent entry per group."""
        return self.annotate(last_seen=Max("timestamp"))

    def cards_aggregation(self, *, one_hour_ago: datetime) -> LogQuerySet:
        """
        Return per-logger totals and recent error/warning counts.

        Chains all card annotation methods in order and groups by
        ``logger_name``, sorted by most recently seen first.
        """
        return (
            cast("LogQuerySet", self.values("logger_name"))
            .with_total()
            .with_total_errors()
            .with_total_warnings()
            .with_recent_errors(one_hour_ago=one_hour_ago)
            .with_recent_warnings(one_hour_ago=one_hour_ago)
            .with_last_seen()
            .order_by("-last_seen")
        )

    def with_has_error(self) -> LogQuerySet:
        """Annotate whether any ERROR or CRITICAL entry exists in the group (non-zero = true)."""
        return self.annotate(has_error=Count("id", filter=Q(level__in=ERROR_LEVELS)))

    def with_has_warning(self) -> LogQuerySet:
        """Annotate whether any WARNING entry exists in the group (non-zero = true)."""
        return self.annotate(has_warning=Count("id", filter=Q(level=LogLevel.WARNING)))

    def timeline_aggregation(
        self,
        *,
        cutoff: datetime,
        range_config: RangeConfig,
        app_timezone: tzinfo,
    ) -> LogQuerySet:
        """
        Return error/warning presence per logger per time bucket.

        Filters entries at or after *cutoff*, truncates timestamps to hour or
        day boundaries in *app_timezone*, groups by ``logger_name`` and
        ``bucket``, then chains ``with_has_error`` and ``with_has_warning``.
        """
        trunc_class = TruncHour if range_config.unit is RangeUnit.HOUR else TruncDay
        return (
            cast(
                "LogQuerySet",
                self.filter(timestamp__gte=cutoff)
                .annotate(bucket=trunc_class("timestamp", tzinfo=app_timezone))
                .values("logger_name", "bucket"),
            )
            .with_has_error()
            .with_has_warning()
        )
