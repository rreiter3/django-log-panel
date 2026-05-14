from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from django.db import models

from log_panel.datetimes import to_database_datetime
from log_panel.querysets import LogQuerySet, LogQueryset
from log_panel.types import MessageParts

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
        return log

    def bulk_create_from_records(self, records: list[dict[str, Any]]) -> list[Log]:
        """Persist multiple log records in a single bulk insert operation."""
        from log_panel.models import LogMessageChunk

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
