from datetime import datetime, tzinfo
from typing import cast

from django.db import models
from django.db.models import Count, Max, Q
from django.db.models.functions import TruncDay, TruncHour

from log_panel.types import ERROR_LEVELS, LogLevel, RangeConfig, RangeUnit


class PanelQuerySet(models.QuerySet):
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

    def with_total(self) -> "PanelQuerySet":
        """Annotate the total number of log entries per group."""
        return self.annotate(total=Count("id"))

    def with_total_errors(self) -> "PanelQuerySet":
        """Annotate the all-time count of ERROR and CRITICAL entries per group."""
        return self.annotate(total_errors=Count("id", filter=Q(level__in=ERROR_LEVELS)))

    def with_total_warnings(self) -> "PanelQuerySet":
        """Annotate the all-time count of WARNING entries per group."""
        return self.annotate(
            total_warnings=Count("id", filter=Q(level=LogLevel.WARNING))
        )

    def with_recent_errors(self, *, one_hour_ago: datetime) -> "PanelQuerySet":
        """Annotate the count of ERROR/CRITICAL entries emitted since *one_hour_ago*.

        Args:
            one_hour_ago: Timezone-aware lower bound for the "recent" window.
        """
        return self.annotate(
            recent_errors=Count(
                "id", filter=Q(level__in=ERROR_LEVELS, timestamp__gte=one_hour_ago)
            )
        )

    def with_recent_warnings(self, *, one_hour_ago: datetime) -> "PanelQuerySet":
        """Annotate the count of WARNING entries emitted since *one_hour_ago*.

        Args:
            one_hour_ago: Timezone-aware lower bound for the "recent" window.
        """
        return self.annotate(
            recent_warnings=Count(
                "id", filter=Q(level=LogLevel.WARNING, timestamp__gte=one_hour_ago)
            )
        )

    def with_last_seen(self) -> "PanelQuerySet":
        """Annotate the timestamp of the most recent entry per group."""
        return self.annotate(last_seen=Max("timestamp"))

    def cards_aggregation(self, *, one_hour_ago: datetime) -> "PanelQuerySet":
        """Return per-logger totals and recent error/warning counts.

        Chains all card annotation methods in order and groups by
        ``logger_name``, sorted by most recently seen first.

        Args:
            one_hour_ago: Timezone-aware cutoff for the "recent" counts.
        """
        return (
            cast("PanelQuerySet", self.values("logger_name"))
            .with_total()
            .with_total_errors()
            .with_total_warnings()
            .with_recent_errors(one_hour_ago=one_hour_ago)
            .with_recent_warnings(one_hour_ago=one_hour_ago)
            .with_last_seen()
            .order_by("-last_seen")
        )

    def with_has_error(self) -> "PanelQuerySet":
        """Annotate whether any ERROR or CRITICAL entry exists in the group (non-zero = true)."""
        return self.annotate(has_error=Count("id", filter=Q(level__in=ERROR_LEVELS)))

    def with_has_warning(self) -> "PanelQuerySet":
        """Annotate whether any WARNING entry exists in the group (non-zero = true)."""
        return self.annotate(has_warning=Count("id", filter=Q(level=LogLevel.WARNING)))

    def timeline_aggregation(
        self,
        *,
        cutoff: datetime,
        range_config: RangeConfig,
        app_timezone: tzinfo,
    ) -> "PanelQuerySet":
        """Return error/warning presence per logger per time bucket.

        Filters entries at or after *cutoff*, truncates timestamps to hour or
        day boundaries in *app_timezone*, groups by ``logger_name`` and
        ``bucket``, then chains ``with_has_error`` and ``with_has_warning``.

        Args:
            cutoff: Earliest timestamp to include (timezone-aware).
            range_config: Determines the truncation unit (``HOUR`` or ``DAY``).
            app_timezone: Timezone for bucket truncation and label display.
        """
        trunc_class = TruncHour if range_config.unit is RangeUnit.HOUR else TruncDay
        return (
            cast(
                "PanelQuerySet",
                self.filter(timestamp__gte=cutoff)
                .annotate(bucket=trunc_class("timestamp", tzinfo=app_timezone))
                .values("logger_name", "bucket"),
            )
            .with_has_error()
            .with_has_warning()
        )
