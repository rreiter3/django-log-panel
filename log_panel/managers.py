from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from django.db import models
from django.utils import timezone as django_timezone

from log_panel.querysets import PanelQuerySet
from log_panel.types import LogLevel

if TYPE_CHECKING:
    from log_panel.models import Panel


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


@dataclass
class LogFilters:
    logger_names: list[str] | None = None
    levels: list[str] | None = None
    search: str = ""
    timestamp_from: datetime | None = None
    timestamp_to: datetime | None = None


class LogQueryset:
    """Chainable log filter accumulator evaluated against the active backend.

    Each call to :meth:`filter` returns a new ``LogQueryset`` with the
    updated constraints — the original is not modified.  Call :meth:`page`
    to execute the query and retrieve a page of results.

    Example::

        from log_panel.managers import LogManager

        class MyLogManager(LogManager):
            def get_queryset(self):
                return super().get_queryset().filter(
                    logger_names=["orders", "machines"],
                    min_level="WARNING",
                )

        # In a view:
        manager = MyLogManager()
        logs, total = manager.get_queryset().filter(search=request.GET.get("q", "")).page(1, 20)
    """

    def __init__(self, backend, filters: LogFilters | None = None) -> None:
        self._backend = backend
        self._filters = filters or LogFilters()

    def filter(
        self,
        *,
        logger_names: list[str] | None = None,
        min_level: str | None = None,
        search: str | None = None,
        timestamp_from: datetime | None = None,
        timestamp_to: datetime | None = None,
    ) -> LogQueryset:
        """Return a new ``LogQueryset`` with additional constraints applied.

        Args:
            logger_names: Restrict results to logs from these loggers.
                Replaces any previously set logger name filter.
            min_level: Minimum log level to include (inclusive).
                ``"WARNING"`` returns WARNING, ERROR, and CRITICAL.
                Replaces any previously set level filter.
            search: Case-insensitive message substring filter.
            timestamp_from: Inclusive lower bound for log timestamps.
            timestamp_to: Exclusive upper bound for log timestamps.
        """
        updated = replace(self._filters)
        if logger_names is not None:
            updated.logger_names = logger_names
        if min_level is not None:
            updated.levels = levels_at_or_above(min_level)
        if search is not None:
            updated.search = search
        if timestamp_from is not None:
            updated.timestamp_from = timestamp_from
        if timestamp_to is not None:
            updated.timestamp_to = timestamp_to
        return LogQueryset(self._backend, updated)

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


class LogManager:
    """Base class for querying logs through the active backend.

    Subclass and override :meth:`get_queryset` to apply default filters
    (e.g. logger name or minimum level restrictions) for a specific
    user role.  Further filters can still be chained in the view.

    Example::

        class OperatorLogManager(LogManager):
            def get_queryset(self):
                return super().get_queryset().filter(
                    logger_names=["orders", "shipments"],
                    min_level="WARNING",
                )
    """

    def get_queryset(self) -> LogQueryset:
        """Return an unrestricted ``LogQueryset`` for the active backend."""
        from log_panel import conf

        return LogQueryset(conf.get_backend())


class PanelManager(models.Manager):
    """Custom manager for ``Panel``"""

    def get_queryset(self) -> PanelQuerySet:
        return PanelQuerySet(self.model, using=self._db)

    def count_threshold_matches(
        self,
        *,
        logger_name: str,
        levels: tuple[str, ...],
        window_start: datetime,
        window_end: datetime,
    ) -> int:
        return self.get_queryset().count_threshold_matches(
            logger_name=logger_name,
            levels=levels,
            window_start=window_start,
            window_end=window_end,
        )

    def create_from_record(
        self,
        timestamp: datetime,
        level: str,
        logger_name: str,
        message: str,
        module: str,
        pathname: str,
        line_number: int,
    ) -> Panel:
        """Persist a single structured log record.

        Args:
            timestamp: When the log record was emitted (timezone-aware UTC).
            level: Log level string (e.g. ``"ERROR"``).
            logger_name: Python logger name.
            message: Formatted log message.
            module: Module where the record was emitted.
            pathname: Full path of the source file.
            line_number: Line number within the source file.
        """
        return self.create(
            timestamp=timestamp,
            level=level,
            logger_name=logger_name,
            message=message,
            module=module,
            pathname=pathname,
            line_number=line_number,
        )
