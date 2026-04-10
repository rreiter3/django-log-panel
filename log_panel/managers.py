from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from django.db import models

from log_panel.querysets import PanelQuerySet

if TYPE_CHECKING:
    from log_panel.models import Panel


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
