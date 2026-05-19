from datetime import datetime, timedelta, tzinfo

from django.db.models import Q

from log_panel import conf
from log_panel.backends.base import LogsBackend
from log_panel.datetimes import to_database_datetime, to_display_datetime
from log_panel.models import Log, LogCard, LogTimelineBucket
from log_panel.querysets import LogQuerySet
from log_panel.types import RangeConfig, RangeUnit, SlotStatus


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
        self,
        now_utc: datetime,
        range_config: RangeConfig,
        app_timezone: tzinfo,
        page: int = 1,
        page_size: int = 5,
        card_filter: str = "",
    ) -> tuple[list[dict], int]:
        """
        Read pre-computed LogCard rows with pagination, then enrich with
        recent counts and timeline slots for only the current page's loggers.
        """
        one_hour_ago: datetime = to_database_datetime(
            value=now_utc - timedelta(hours=1), app_timezone=app_timezone
        )
        cutoff: datetime = to_database_datetime(
            value=now_utc - range_config.delta, app_timezone=app_timezone
        )

        card_qs = (
            LogCard.objects.select_related("logger")
            .order_by("-last_seen")
            .for_card_filter(card_filter)  # ty: ignore[unresolved-attribute]
        )
        total_cards: int = card_qs.count()
        skip: int = (page - 1) * page_size
        page_cards = list(card_qs[skip : skip + page_size])

        if not page_cards:
            return [], total_cards

        page_logger_names: list[str] = [c.logger.name for c in page_cards]
        page_logger_ids: list = [c.logger_id for c in page_cards]

        recent_by_logger: dict[str, dict[str, int]] = {
            r["logger_name"]: r
            for r in self.get_queryset()
            .filter(logger_name__in=page_logger_names)
            .recent_aggregation(one_hour_ago=one_hour_ago)
        }

        bucket_unit: str = "hour" if range_config.unit is RangeUnit.HOUR else "day"
        thresholds: dict[str, int | None] = conf.get_thresholds()
        timeline_by_logger = (
            LogTimelineBucket.objects.all()
            .for_loggers(page_logger_ids, bucket_unit, cutoff)  # ty: ignore[unresolved-attribute]
            .to_status_map(
                error_threshold=thresholds.get("ERROR") or 1,
                warning_threshold=thresholds.get("WARNING") or 1,
                app_timezone=app_timezone,
            )
        )

        now_bucket_local, slot_delta = self.get_local_now_and_slot_delta(
            now_utc=now_utc,
            app_timezone=app_timezone,
            configured_unit=range_config.unit,
        )
        slots_count: int = range_config.slots

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

        rows: list[dict] = []
        for card in page_cards:
            logger_name: str = card.logger.name
            recent: dict[str, int] = recent_by_logger.get(logger_name, {})
            logger_timeline = timeline_by_logger.get(logger_name, {})
            slots: list[dict[str, str]] = [
                {
                    "label": slot_labels[i],
                    "status": logger_timeline.get(slot_boundaries[i], SlotStatus.EMPTY),
                    "timestamp_from": slot_from_iso[i],
                    "timestamp_to": slot_to_iso[i],
                }
                for i in range(slots_count)
            ]
            rows.append(
                {
                    "logger_name": logger_name,
                    "total": card.total,
                    "total_errors": card.total_errors,
                    "total_warnings": card.total_warnings,
                    "recent_errors": recent.get("recent_errors", 0) or 0,
                    "recent_warnings": recent.get("recent_warnings", 0) or 0,
                    "last_seen": card.last_seen,
                    "timeline": slots,
                }
            )
        return rows, total_cards

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
                "id": str(object=log.pk),
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
