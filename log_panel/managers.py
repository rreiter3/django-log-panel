from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from django.db import models
from django.db.models import Case, F, When
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
    from log_panel.models import Log, Logger


class LoggerManager(models.Manager):
    """Manager for the Logger model — resolves logger names to rows in bulk."""

    def get_or_create_many(self, *, names: Iterable[str]) -> dict[str, Logger]:
        """
        Fetch or create Logger rows for *names* in a fixed, small number of queries.

        Used to batch what would otherwise be one get_or_create() round trip per
        logger name across the LogCard/LogTimelineBucket upsert paths.
        """
        unique_names = set(names)
        if not unique_names:
            return {}

        loggers: dict[str, Logger] = {
            logger.name: logger for logger in self.filter(name__in=unique_names)
        }
        missing = unique_names - loggers.keys()
        if missing:
            self.bulk_create(
                [self.model(name=name) for name in missing], ignore_conflicts=True
            )
            loggers.update(
                {logger.name: logger for logger in self.filter(name__in=missing)}
            )
        return loggers


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
        from log_panel.models import LogCard, Logger, LogTimelineBucket

        loggers = Logger.objects.db_manager(self.db).get_or_create_many(
            names=[logger_name]
        )
        LogCard.objects.db_manager(self.db).bulk_upsert(
            [
                {
                    "logger_name": logger_name,
                    "total_delta": 1,
                    "error_delta": 1 if level in ERROR_LEVELS else 0,
                    "warning_delta": 1 if level == LogLevel.WARNING else 0,
                    "last_seen": db_timestamp,
                }
            ],
            loggers=loggers,
        )
        LogTimelineBucket.objects.db_manager(self.db).bulk_upsert(
            [{"logger_name": logger_name, "timestamp": db_timestamp, "level": level}],
            loggers=loggers,
        )
        return log

    def bulk_create_from_records(self, records: list[dict[str, Any]]) -> list[Log]:
        """Persist multiple log records in a single bulk insert operation."""
        from log_panel.models import LogCard, Logger, LogMessageChunk, LogTimelineBucket

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

        loggers = Logger.objects.db_manager(self.db).get_or_create_many(
            names=total_by_logger.keys()
        )

        LogCard.objects.db_manager(self.db).bulk_upsert(
            [
                {
                    "logger_name": name,
                    "total_delta": total_by_logger[name],
                    "error_delta": errors_by_logger[name],
                    "warning_delta": warnings_by_logger[name],
                    "last_seen": last_seen_by_logger[name],
                }
                for name in total_by_logger
            ],
            loggers=loggers,
        )

        LogTimelineBucket.objects.db_manager(self.db).bulk_upsert(
            records, loggers=loggers
        )

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
        self.bulk_upsert(
            [
                {
                    "logger_name": logger_name,
                    "total_delta": total_delta,
                    "error_delta": error_delta,
                    "warning_delta": warning_delta,
                    "last_seen": last_seen,
                }
            ]
        )

    def bulk_upsert(
        self,
        entries: list[dict[str, Any]],
        *,
        loggers: dict[str, Any] | None = None,
    ) -> None:
        """Create or atomically increment counters for a batch of loggers in a fixed number of queries."""
        if not entries:
            return
        if loggers is None:
            from log_panel.models import Logger

            loggers = Logger.objects.db_manager(self.db).get_or_create_many(
                names=(entry["logger_name"] for entry in entries)
            )

        logger_ids = [loggers[entry["logger_name"]].id for entry in entries]
        existing_pk_by_logger_id: dict[Any, Any] = dict(
            self.filter(logger_id__in=logger_ids).values_list("logger_id", "pk")
        )

        to_create: list[Any] = []
        total_cases: list[When] = []
        error_cases: list[When] = []
        warning_cases: list[When] = []
        last_seen_cases: list[When] = []
        update_pks: list[Any] = []

        for entry in entries:
            logger_obj = loggers[entry["logger_name"]]
            pk = existing_pk_by_logger_id.get(logger_obj.id)
            if pk is not None:
                update_pks.append(pk)
                total_cases.append(When(pk=pk, then=F("total") + entry["total_delta"]))
                if entry["error_delta"]:
                    error_cases.append(
                        When(pk=pk, then=F("total_errors") + entry["error_delta"])
                    )
                if entry["warning_delta"]:
                    warning_cases.append(
                        When(pk=pk, then=F("total_warnings") + entry["warning_delta"])
                    )
                last_seen_cases.append(
                    When(pk=pk, then=Greatest(F("last_seen"), entry["last_seen"]))
                )
            else:
                to_create.append(
                    self.model(
                        logger=logger_obj,
                        total=entry["total_delta"],
                        total_errors=entry["error_delta"],
                        total_warnings=entry["warning_delta"],
                        last_seen=entry["last_seen"],
                    )
                )

        if to_create:
            self.bulk_create(to_create)

        if update_pks:
            meta = self.model._meta
            updates: dict[str, Any] = {
                "total": Case(
                    *total_cases,
                    default=F("total"),
                    output_field=meta.get_field("total"),
                ),
            }
            if error_cases:
                updates["total_errors"] = Case(
                    *error_cases,
                    default=F("total_errors"),
                    output_field=meta.get_field("total_errors"),
                )
            if warning_cases:
                updates["total_warnings"] = Case(
                    *warning_cases,
                    default=F("total_warnings"),
                    output_field=meta.get_field("total_warnings"),
                )
            updates["last_seen"] = Case(
                *last_seen_cases,
                default=F("last_seen"),
                output_field=meta.get_field("last_seen"),
            )
            self.filter(pk__in=update_pks).update(**updates)

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
        self.bulk_upsert(
            [{"logger_name": logger_name, "timestamp": timestamp, "level": level}]
        )

    def bulk_upsert(
        self,
        records: list[dict[str, Any]],
        *,
        loggers: dict[str, Any] | None = None,
    ) -> None:
        """Aggregate and upsert timeline buckets for a batch of log records in a fixed number of queries."""
        if not records:
            return
        if loggers is None:
            from log_panel.models import Logger

            loggers = Logger.objects.db_manager(self.db).get_or_create_many(
                names=(r["logger_name"] for r in records)
            )

        BucketKey = tuple[Any, datetime, str]
        deltas: dict[BucketKey, list[int]] = {}

        for r in records:
            level = r["level"]
            error_delta: Literal[1, 0] = 1 if level in ERROR_LEVELS else 0
            warning_delta: Literal[1, 0] = 1 if level == LogLevel.WARNING else 0

            ts: datetime = to_database_datetime(value=r["timestamp"])
            logger_id = loggers[r["logger_name"]].id

            hour_bucket = ts.replace(minute=0, second=0, microsecond=0)
            day_bucket = ts.replace(hour=0, minute=0, second=0, microsecond=0)

            for bucket, unit in (
                (hour_bucket, RangeUnit.HOUR),
                (day_bucket, RangeUnit.DAY),
            ):
                key: BucketKey = (logger_id, bucket, unit)
                if key in deltas:
                    deltas[key][0] += 1
                    deltas[key][1] += error_delta
                    deltas[key][2] += warning_delta
                else:
                    deltas[key] = [1, error_delta, warning_delta]

        if not deltas:
            return

        logger_ids = {logger_id for logger_id, _, _ in deltas}
        existing_pk_by_key: dict[BucketKey, Any] = {
            (logger_id, bucket, unit): pk
            for pk, logger_id, bucket, unit in self.filter(
                logger_id__in=logger_ids
            ).values_list("pk", "logger_id", "bucket", "unit")
        }

        to_create: list[Any] = []
        count_cases: list[When] = []
        error_cases: list[When] = []
        warning_cases: list[When] = []
        update_pks: list[Any] = []

        for key, (lc, ed, wd) in deltas.items():
            logger_id, bucket, unit = key
            pk = existing_pk_by_key.get(key)
            if pk is not None:
                update_pks.append(pk)
                count_cases.append(When(pk=pk, then=F("log_count") + lc))
                if ed:
                    error_cases.append(When(pk=pk, then=F("error_count") + ed))
                if wd:
                    warning_cases.append(When(pk=pk, then=F("warning_count") + wd))
            else:
                to_create.append(
                    self.model(
                        logger_id=logger_id,
                        bucket=bucket,
                        unit=unit,
                        log_count=lc,
                        error_count=ed,
                        warning_count=wd,
                    )
                )

        if to_create:
            self.bulk_create(to_create)

        if update_pks:
            meta = self.model._meta
            updates: dict[str, Any] = {
                "log_count": Case(
                    *count_cases,
                    default=F("log_count"),
                    output_field=meta.get_field("log_count"),
                ),
            }
            if error_cases:
                updates["error_count"] = Case(
                    *error_cases,
                    default=F("error_count"),
                    output_field=meta.get_field("error_count"),
                )
            if warning_cases:
                updates["warning_count"] = Case(
                    *warning_cases,
                    default=F("warning_count"),
                    output_field=meta.get_field("warning_count"),
                )
            self.filter(pk__in=update_pks).update(**updates)

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
