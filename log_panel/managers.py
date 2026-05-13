from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from django.db import models

from log_panel.querysets import LogQuerySet, LogQueryset

if TYPE_CHECKING:
    from log_panel.models import Log


class LogReader:
    """
    Read-only interface for querying logs outside the admin panel.

    Use in your own views, APIs, or background tasks.  Subclass and
    override :meth:`get_queryset` to apply default filters (e.g. logger
    name or minimum level restrictions) for a specific user role.
    Further filters can still be chained on the returned ``LogQueryset``.
    """

    def get_queryset(self) -> LogQueryset:
        """Returns a LogQueryset for the active backend with no filters applied."""
        from log_panel import conf

        return LogQueryset(backend=conf.get_backend())


class LogRecordManager(models.Manager):
    """Custom manager for ``Log``"""

    def get_queryset(self) -> LogQuerySet:
        return LogQuerySet(self.model, using=self._db)

    def count_threshold_matches(
        self,
        *,
        logger_name: str,
        levels: tuple[str, ...],
        window_start: datetime,
        window_end: datetime,
    ) -> int:
        """Count how many log records match the given logger name and level filters within the specified time window."""
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
    ) -> Log:
        """Persist a single log record."""
        return self.create(
            timestamp=timestamp,
            level=level,
            logger_name=logger_name,
            message=message,
            module=module,
            pathname=pathname,
            line_number=line_number,
        )
