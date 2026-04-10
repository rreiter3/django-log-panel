from collections import defaultdict
from datetime import datetime, timedelta, tzinfo
from typing import Literal

from log_panel import conf
from log_panel.backends.base import LogsBackend
from log_panel.models import Panel
from log_panel.querysets import PanelQuerySet
from log_panel.types import LogLevel, RangeConfig, SlotStatus


class SqlBackend(LogsBackend):
    """Read log data from a SQL database through the Django ORM.

    Supports any database engine that Django supports (PostgreSQL, MySQL, SQLite, MSSQL).
    Time-bucket aggregation uses Django's ``TruncHour`` / ``TruncDay`` with timezone support.
    Message search uses case-insensitive ``icontains``.

    Database routing is handled by ``LogsRouter`` — no explicit alias is needed here.
    """

    def get_queryset(self):
        """Return a base ``PanelQuerySet`` routed by ``LogsRouter``."""
        return Panel.objects.all()

    def get_logger_cards(
        self, now_utc: datetime, range_config: RangeConfig, app_timezone: tzinfo
    ) -> list[dict]:
        """Aggregate per-logger stats and build timeline slots via Django ORM.

        Delegates card aggregation to ``PanelQuerySet.cards_aggregation`` and
        timeline bucketing to ``PanelQuerySet.timeline_aggregation``, then
        assembles final row dicts with timeline slot labels and statuses.

        Args:
            now_utc: Current UTC datetime (timezone-aware).
            range_config: Determines time range, bucket unit, slot count, and label format.
            app_timezone: Timezone used for slot truncation and display labels.
        """
        one_hour_ago: datetime = now_utc - timedelta(hours=1)
        cutoff: datetime = now_utc - range_config.delta

        cards: PanelQuerySet = self.get_queryset().cards_aggregation(
            one_hour_ago=one_hour_ago
        )
        timeline_qs: PanelQuerySet = self.get_queryset().timeline_aggregation(
            cutoff=cutoff, range_config=range_config, app_timezone=app_timezone
        )

        now_bucket_local, slot_delta = self.get_local_now_and_slot_delta(
            now_utc=now_utc,
            app_timezone=app_timezone,
            configured_unit=range_config.unit,
        )
        slots_count: int = range_config.slots

        # Aware slot boundaries in app timezone, oldest first.
        slot_boundaries: list[datetime] = [
            now_bucket_local - slot_delta * i for i in range(slots_count - 1, -1, -1)
        ]
        slot_labels: list[str] = [
            dt.strftime(range_config.format) for dt in slot_boundaries
        ]
        slot_from_iso: list[str] = [
            dt.strftime("%Y-%m-%dT%H:%M") for dt in slot_boundaries
        ]
        slot_to_iso: list[str] = [
            (dt + slot_delta).strftime("%Y-%m-%dT%H:%M") for dt in slot_boundaries
        ]

        # entry["bucket"] may be naive when USE_TZ=False; make it aware so the
        # dict lookup against the aware slot_boundaries list always finds a match.
        from django.utils.timezone import is_naive, make_aware

        thresholds: dict[str, int | None] = conf.get_thresholds()
        error_threshold: int = thresholds.get("ERROR") or 1
        warning_threshold: int = thresholds.get("WARNING") or 1

        timeline_by_logger: dict[str, dict[datetime, SlotStatus]] = defaultdict(dict)
        for entry in timeline_qs:
            status: Literal[SlotStatus.ERROR, SlotStatus.WARNING, SlotStatus.OK] = (
                SlotStatus.ERROR
                if entry["has_error"] >= error_threshold
                else (
                    SlotStatus.WARNING
                    if entry["has_warning"] >= warning_threshold
                    else SlotStatus.OK
                )
            )
            bucket: datetime = entry["bucket"]
            if is_naive(bucket):
                bucket = make_aware(bucket, app_timezone)
            timeline_by_logger[entry["logger_name"]][bucket] = status

        rows: list[dict] = []
        for doc in cards:
            logger_name: str = doc["logger_name"]
            slots: list[dict[str, str]] = [
                {
                    "label": slot_labels[i],
                    "status": timeline_by_logger[logger_name].get(
                        slot_boundaries[i], SlotStatus.EMPTY
                    ),
                    "timestamp_from": slot_from_iso[i],
                    "timestamp_to": slot_to_iso[i],
                }
                for i in range(slots_count)
            ]
            rows.append(
                {
                    "logger_name": logger_name,
                    "total": doc["total"],
                    "total_errors": doc["total_errors"] or 0,
                    "total_warnings": doc["total_warnings"] or 0,
                    "recent_errors": doc["recent_errors"] or 0,
                    "recent_warnings": doc["recent_warnings"] or 0,
                    "last_seen": doc["last_seen"],
                    "timeline": slots,
                }
            )
        return rows

    def get_log_table(
        self,
        logger_name: str,
        level: "LogLevel | str",
        search: str,
        page: int,
        page_size: int,
        app_timezone: tzinfo,
        timestamp_from: datetime | None = None,
        timestamp_to: datetime | None = None,
    ) -> tuple[list[dict], int]:
        """Query individual log entries with optional level and message filters.

        Note: message search uses ``icontains`` (case-insensitive substring), not regex.

        Args:
            logger_name: Filter to this logger.
            level: One of the ``LogLevel`` literals (``"DEBUG"``, ``"INFO"``, etc.) or
                empty string to return all levels.
            search: Message substring filter, or empty string to skip.
            page: 1-based page number.
            page_size: Number of entries per page.
            app_timezone: Timezone for timestamp conversion in the returned dicts.
            timestamp_from: Optional inclusive lower bound for log timestamps.
            timestamp_to: Optional exclusive upper bound for log timestamps.
        """
        qs: PanelQuerySet = self.get_queryset().filter(logger_name=logger_name)
        if level:
            qs: PanelQuerySet = qs.filter(level=level)
        if search:
            qs: PanelQuerySet = qs.filter(message__icontains=search)
        if timestamp_from:
            qs = qs.filter(timestamp__gte=timestamp_from)
        if timestamp_to:
            qs = qs.filter(timestamp__lt=timestamp_to)

        total: int = qs.count()
        skip: int = (page - 1) * page_size
        raw_logs: PanelQuerySet = qs.order_by("-timestamp")[skip : skip + page_size]

        logs: list[dict] = [
            {
                "_id": str(object=log.pk),
                "timestamp": log.timestamp.astimezone(app_timezone),
                "level": log.level,
                "logger_name": log.logger_name,
                "message": log.message,
                "module": log.module,
                "pathname": log.pathname,
                "line_number": log.line_number,
            }
            for log in raw_logs
        ]
        return logs, total
