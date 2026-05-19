from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from django.db import models
from django.db.models import F
from django.db.models.functions import Greatest

from log_panel.datetimes import to_database_datetime
from log_panel.querysets import (
    LogCardQuerySet,
    LogQuery,
    LogQuerySet,
    TimelineBucketQuerySet,
)
from log_panel.types import ERROR_LEVELS, LogLevel, MessageParts, RangeUnit

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

    def get_queryset(self) -> LogQuery:
        """Returns a LogQueryset for the active backend with no filters applied."""
        from log_panel import conf

        return LogQuery(backend=conf.get_backend())


class LogRecordManager(models.Manager):
    """Manager for the Log model — handles record creation and bulk inserts."""

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
            window_start=to_database_datetime(value=window_start),
            window_end=to_database_datetime(value=window_end),
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
        message_parts = self._split_message(message=message)
        log: Log = self.create(
            timestamp=to_database_datetime(value=timestamp),
            level=level,
            logger_name=logger_name,
            message=message_parts.preview,
            message_size=message_parts.size,
            message_chunked=message_parts.is_chunked,
            module=module,
            pathname=pathname,
            line_number=line_number,
        )
        if message_parts.is_chunked:
            from log_panel.models import LogMessageChunk

            LogMessageChunk.objects.db_manager(self.db).bulk_create(
                (
                    LogMessageChunk(log=log, index=index, text=chunk)
                    for index, chunk in enumerate(message_parts.chunks)
                ),
                batch_size=100,
            )

        db_timestamp: datetime = to_database_datetime(value=timestamp)
        from log_panel.models import LogCard, LogTimelineBucket

        LogCard.objects.db_manager(self.db).upsert(
            logger_name=logger_name,
            total_delta=1,
            error_delta=1 if level in ERROR_LEVELS else 0,
            warning_delta=1 if level == LogLevel.WARNING else 0,
            last_seen=db_timestamp,
        )
        LogTimelineBucket.objects.db_manager(self.db).upsert(
            logger_name=logger_name,
            timestamp=db_timestamp,
            level=level,
        )
        return log

    def bulk_create_from_records(self, records: list[dict[str, Any]]) -> list[Log]:
        """Persist multiple log records in a single bulk insert operation."""
        from log_panel.models import LogCard, LogMessageChunk, LogTimelineBucket

        parts_list = [self._split_message(message=r["message"]) for r in records]

        log_instances = [
            self.model(
                timestamp=to_database_datetime(value=r["timestamp"]),
                level=r["level"],
                logger_name=r["logger_name"],
                message=parts.preview,
                message_size=parts.size,
                message_chunked=parts.is_chunked,
                module=r["module"],
                pathname=r["pathname"],
                line_number=r["line_number"],
            )
            for r, parts in zip(records, parts_list, strict=True)
        ]

        created_logs: list[Log] = self.bulk_create(log_instances)

        chunk_instances = [
            LogMessageChunk(log=log, index=index, text=chunk)
            for log, parts in zip(created_logs, parts_list, strict=True)
            if parts.is_chunked
            for index, chunk in enumerate(parts.chunks)
        ]
        if chunk_instances:
            LogMessageChunk.objects.db_manager(self.db).bulk_create(
                chunk_instances, batch_size=100
            )

        total_by_logger: Counter[str] = Counter()
        errors_by_logger: Counter[str] = Counter()
        warnings_by_logger: Counter[str] = Counter()
        last_seen_by_logger: dict[str, datetime] = {}
        for r in records:
            name: Any = r["logger_name"]
            total_by_logger[name] += 1
            if r["level"] in ERROR_LEVELS:
                errors_by_logger[name] += 1
            if r["level"] == LogLevel.WARNING:
                warnings_by_logger[name] += 1
            ts: datetime = to_database_datetime(value=r["timestamp"])
            if name not in last_seen_by_logger or ts > last_seen_by_logger[name]:
                last_seen_by_logger[name] = ts

        for name in total_by_logger:
            LogCard.objects.db_manager(self.db).upsert(
                logger_name=name,
                total_delta=total_by_logger[name],
                error_delta=errors_by_logger[name],
                warning_delta=warnings_by_logger[name],
                last_seen=last_seen_by_logger[name],
            )

        LogTimelineBucket.objects.db_manager(self.db).bulk_upsert(records)

        return created_logs

    @staticmethod
    def _split_message(*, message: str) -> MessageParts:
        from log_panel import conf

        preview_length: int = conf.get_setting(key="MESSAGE_PREVIEW_LENGTH")
        chunk_size: int = conf.get_setting(key="MESSAGE_CHUNK_SIZE")
        if len(message) <= preview_length:
            return MessageParts(preview=message, chunks=[], size=len(message))
        chunks: list[str] = [
            message[index : index + chunk_size]
            for index in range(0, len(message), chunk_size)
        ]
        return MessageParts(
            preview=message[:preview_length],
            chunks=chunks,
            size=len(message),
        )


class LogCardManager(models.Manager):
    """Manager for the LogCard model — atomic counter upserts."""

    def get_queryset(self) -> LogCardQuerySet:
        return LogCardQuerySet(self.model, using=self._db)

    def upsert(
        self,
        *,
        logger_name: str,
        total_delta: int,
        error_delta: int,
        warning_delta: int,
        last_seen: datetime,
    ) -> None:
        """Create or atomically increment counters for *logger_name*."""
        from log_panel.models import Logger

        logger_obj, _ = Logger.objects.db_manager(self.db).get_or_create(
            name=logger_name
        )
        _, created = self.get_or_create(
            logger=logger_obj,
            defaults={
                "total": total_delta,
                "total_errors": error_delta,
                "total_warnings": warning_delta,
                "last_seen": last_seen,
            },
        )
        if not created:
            updates: dict[str, Any] = {
                "total": F("total") + total_delta,
            }
            if error_delta:
                updates["total_errors"] = F("total_errors") + error_delta
            if warning_delta:
                updates["total_warnings"] = F("total_warnings") + warning_delta
            updates["last_seen"] = Greatest(F("last_seen"), last_seen)
            self.filter(logger=logger_obj).update(**updates)

    def replace_snapshot(
        self,
        *,
        logger_name: str,
        total: int,
        total_errors: int,
        total_warnings: int,
        last_seen: datetime,
    ) -> None:
        """Replace counters for *logger_name* with an exact rebuild snapshot."""
        from log_panel.models import Logger

        logger_obj, _ = Logger.objects.db_manager(self.db).get_or_create(
            name=logger_name
        )
        self.update_or_create(
            logger=logger_obj,
            defaults={
                "total": total,
                "total_errors": total_errors,
                "total_warnings": total_warnings,
                "last_seen": last_seen,
            },
        )


class TimelineBucketManager(models.Manager):
    """Manager for the LogTimelineBucket model — atomic bucket upserts."""

    def get_queryset(self) -> TimelineBucketQuerySet:
        return TimelineBucketQuerySet(self.model, using=self._db)

    def upsert(self, *, logger_name: str, timestamp: datetime, level: str) -> None:
        """Create or increment hourly and daily buckets for a single log record."""
        from log_panel.models import Logger

        logger_obj, _ = Logger.objects.db_manager(self.db).get_or_create(
            name=logger_name
        )
        error_delta: Literal[1, 0] = 1 if level in ERROR_LEVELS else 0
        warning_delta: Literal[1, 0] = 1 if level == LogLevel.WARNING else 0

        hour_bucket = timestamp.replace(minute=0, second=0, microsecond=0)
        day_bucket = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)

        for bucket, unit in (
            (hour_bucket, RangeUnit.HOUR),
            (day_bucket, RangeUnit.DAY),
        ):
            self._upsert_single(
                logger=logger_obj,
                bucket=bucket,
                unit=unit,
                log_count_delta=1,
                error_delta=error_delta,
                warning_delta=warning_delta,
            )

    def bulk_upsert(self, records: list[dict[str, Any]]) -> None:
        """Aggregate and upsert timeline buckets for a batch of log records."""
        from log_panel.models import Logger

        BucketKey = tuple[str, datetime, str]
        deltas: dict[BucketKey, list[int]] = {}

        for r in records:
            level = r["level"]
            error_delta: Literal[1, 0] = 1 if level in ERROR_LEVELS else 0
            warning_delta: Literal[1, 0] = 1 if level == LogLevel.WARNING else 0

            ts: datetime = to_database_datetime(value=r["timestamp"])
            name = r["logger_name"]

            hour_bucket = ts.replace(minute=0, second=0, microsecond=0)
            day_bucket = ts.replace(hour=0, minute=0, second=0, microsecond=0)

            for bucket, unit in (
                (hour_bucket, RangeUnit.HOUR),
                (day_bucket, RangeUnit.DAY),
            ):
                key: BucketKey = (name, bucket, unit)
                if key in deltas:
                    deltas[key][0] += 1
                    deltas[key][1] += error_delta
                    deltas[key][2] += warning_delta
                else:
                    deltas[key] = [1, error_delta, warning_delta]

        for (logger_name, bucket, unit), (lc, ed, wd) in deltas.items():
            logger_obj, _ = Logger.objects.db_manager(self.db).get_or_create(
                name=logger_name
            )
            self._upsert_single(
                logger=logger_obj,
                bucket=bucket,
                unit=unit,
                log_count_delta=lc,
                error_delta=ed,
                warning_delta=wd,
            )

    def replace_snapshot(
        self,
        *,
        logger_name: str,
        bucket: datetime,
        unit: str,
        log_count: int,
        error_count: int,
        warning_count: int,
    ) -> None:
        """Replace one timeline bucket with an exact rebuild snapshot."""
        from log_panel.models import Logger

        logger_obj, _ = Logger.objects.db_manager(self.db).get_or_create(
            name=logger_name
        )
        self.update_or_create(
            logger=logger_obj,
            bucket=bucket,
            unit=unit,
            defaults={
                "log_count": log_count,
                "error_count": error_count,
                "warning_count": warning_count,
            },
        )

    def _upsert_single(
        self,
        *,
        logger: Any,
        bucket: datetime,
        unit: str,
        log_count_delta: int,
        error_delta: int,
        warning_delta: int,
    ) -> None:
        """Atomically upsert a single timeline bucket row."""
        _, created = self.get_or_create(
            logger=logger,
            bucket=bucket,
            unit=unit,
            defaults={
                "log_count": log_count_delta,
                "error_count": error_delta,
                "warning_count": warning_delta,
            },
        )
        if not created:
            updates: dict[str, Any] = {
                "log_count": F("log_count") + log_count_delta,
            }
            if error_delta:
                updates["error_count"] = F("error_count") + error_delta
            if warning_delta:
                updates["warning_count"] = F("warning_count") + warning_delta
            self.filter(logger=logger, bucket=bucket, unit=unit).update(**updates)
