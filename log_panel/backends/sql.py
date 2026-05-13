from collections import defaultdict
from datetime import datetime, timedelta, tzinfo
from typing import Literal

from django.db.models import Q

from log_panel import conf
from log_panel.backends.base import LogsBackend
from log_panel.datetimes import to_database_datetime, to_display_datetime
from log_panel.models import Log
from log_panel.querysets import LogQuerySet
from log_panel.types import LogLevel, RangeConfig, SlotStatus


class OrmBackend(LogsBackend):
    """
    Read log data through the Django ORM.

    Supports any database engine that Django supports, including SQL databases
    (PostgreSQL, MySQL, SQLite, MSSQL) and MongoDB via ``django-mongodb-backend``.
    Time-bucket aggregation uses Django's ``TruncHour`` / ``TruncDay`` with timezone support.
    Message search uses case-insensitive ``icontains``.

    """

    def get_queryset(self):
        """Return a base ``LogQuerySet`` routed by ``LogsRouter``."""
        return Log.objects.all()

    def get_logger_cards(
        self, now_utc: datetime, range_config: RangeConfig, app_timezone: tzinfo
    ) -> list[dict]:
        """
        Aggregate per-logger stats and build timeline slots.

        Delegates card aggregation to ``LogQuerySet.cards_aggregation`` and
        timeline bucketing to ``LogQuerySet.timeline_aggregation``, then
        assembles final row dicts with timeline slot labels and statuses.
        """
        one_hour_ago: datetime = to_database_datetime(
            value=now_utc - timedelta(hours=1), app_timezone=app_timezone
        )
        cutoff: datetime = to_database_datetime(
            value=now_utc - range_config.delta, app_timezone=app_timezone
        )

        cards: LogQuerySet = self.get_queryset().cards_aggregation(
            one_hour_ago=one_hour_ago
        )
        timeline_qs: LogQuerySet = self.get_queryset().timeline_aggregation(
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

    def _apply_log_filters(
        self,
        logger_names: list[str] | None,
        levels: list[str] | None,
        search: str,
        timestamp_from: datetime | None,
        timestamp_to: datetime | None,
        app_timezone: tzinfo | None = None,
    ) -> LogQuerySet:
        """Apply the given filters to a base queryset."""
        qs: LogQuerySet = self.get_queryset()
        if logger_names is not None:
            qs = qs.filter(logger_name__in=logger_names)
        if levels is not None:
            qs = qs.filter(level__in=levels)
        if search:
            qs = qs.filter(
                Q(message__icontains=search) | Q(message_chunks__text__icontains=search)
            ).distinct()
        if timestamp_from:
            qs = qs.filter(
                timestamp__gte=to_database_datetime(
                    value=timestamp_from, app_timezone=app_timezone
                )
            )
        if timestamp_to:
            qs = qs.filter(
                timestamp__lt=to_database_datetime(
                    value=timestamp_to, app_timezone=app_timezone
                )
            )
        return qs

    def query_logs(
        self,
        logger_names: list[str] | None,
        levels: list[str] | None,
        search: str,
        offset: int,
        limit: int | None,
        app_timezone: tzinfo,
        timestamp_from: datetime | None = None,
        timestamp_to: datetime | None = None,
    ) -> list[dict]:
        """Query individual log entries with optional level and message filters."""
        qs: LogQuerySet = self._apply_log_filters(
            logger_names, levels, search, timestamp_from, timestamp_to, app_timezone
        ).order_by("-timestamp")
        raw_logs: LogQuerySet = (
            qs[offset:] if limit is None else qs[offset : offset + limit]
        ).prefetch_related("message_chunks")
        return [
            {
                "_id": str(object=log.pk),
                "timestamp": to_display_datetime(
                    value=log.timestamp, app_timezone=app_timezone
                ),
                "level": log.level,
                "logger_name": log.logger_name,
                "message": log.get_full_message(),
                "message_preview": log.message,
                "message_size": log.message_size,
                "message_chunked": log.message_chunked,
                "module": log.module,
                "pathname": log.pathname,
                "line_number": log.line_number,
            }
            for log in raw_logs
        ]

    def count_logs(
        self,
        logger_names: list[str] | None,
        levels: list[str] | None,
        search: str,
        timestamp_from: datetime | None = None,
        timestamp_to: datetime | None = None,
    ) -> int:
        """Count log entries matching the given filters, for pagination purposes."""
        return self._apply_log_filters(
            logger_names, levels, search, timestamp_from, timestamp_to
        ).count()

    def get_modules(self, logger_name: str) -> list[str]:
        """Return a sorted list of distinct module names for the given logger."""
        return sorted(
            self.get_queryset()
            .filter(logger_name=logger_name)
            .values_list("module", flat=True)
            .distinct()
        )

    def get_log_table(
        self,
        logger_name: str,
        level: LogLevel | str,
        search: str,
        page: int,
        page_size: int,
        app_timezone: tzinfo,
        timestamp_from: datetime | None = None,
        timestamp_to: datetime | None = None,
        module: str = "",
    ) -> tuple[list[dict], int]:
        """Query individual log entries with optional level and message filters."""
        qs: LogQuerySet = self.get_queryset().filter(logger_name=logger_name)
        if level:
            qs: LogQuerySet = qs.filter(level=level)
        if module:
            qs: LogQuerySet = qs.filter(module=module)
        if search:
            qs: LogQuerySet = qs.filter(
                Q(message__icontains=search) | Q(message_chunks__text__icontains=search)
            ).distinct()
        if timestamp_from:
            qs = qs.filter(
                timestamp__gte=to_database_datetime(
                    value=timestamp_from, app_timezone=app_timezone
                )
            )
        if timestamp_to:
            qs = qs.filter(
                timestamp__lt=to_database_datetime(
                    value=timestamp_to, app_timezone=app_timezone
                )
            )

        total: int = qs.count()
        skip: int = (page - 1) * page_size
        raw_logs: LogQuerySet = qs.order_by("-timestamp")[skip : skip + page_size]

        logs: list[dict] = [
            {
                "_id": str(object=log.pk),
                "timestamp": to_display_datetime(
                    value=log.timestamp, app_timezone=app_timezone
                ),
                "level": log.level,
                "logger_name": log.logger_name,
                "message": log.message,
                "message_size": log.message_size,
                "message_chunked": log.message_chunked,
                "module": log.module,
                "pathname": log.pathname,
                "line_number": log.line_number,
            }
            for log in raw_logs
        ]
        return logs, total
