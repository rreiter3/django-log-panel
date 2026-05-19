import uuid
from typing import TYPE_CHECKING, Protocol

from django.db import models
from django.db.models.indexes import Index

from log_panel.managers import LogCardManager, LogRecordManager, TimelineBucketManager
from log_panel.types import RangeUnit

if TYPE_CHECKING:
    from django.db.models.query import QuerySet

    class MessageChunkManager(Protocol):
        def order_by(self, *field_names: str) -> QuerySet["LogMessageChunk"]: ...


class Log(models.Model):
    """Represent a log record."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(db_index=True)
    level = models.CharField(max_length=10)
    logger_name = models.CharField(max_length=200, db_index=True)
    message = models.TextField()
    message_size = models.PositiveIntegerField(default=0)
    message_chunked = models.BooleanField(default=False, db_index=True)
    module = models.CharField(max_length=200)
    pathname = models.CharField(max_length=500)
    line_number = models.IntegerField()

    objects = LogRecordManager()
    if TYPE_CHECKING:
        message_chunks: MessageChunkManager

    class Meta:
        db_table = "log_panel_log"
        verbose_name = "Log"
        verbose_name_plural = "Logs"
        indexes: list[Index] = [
            models.Index(
                fields=("timestamp", "logger_name", "level"),
                name="timestamp_logger_level",
            ),
            models.Index(
                fields=("logger_name", "-timestamp"),
                name="logger_name_timestamp",
            ),
        ]

    def get_full_message(self) -> str:
        """Return the complete message, reassembling chunked payloads when needed."""
        if not self.message_chunked:
            return self.message
        return "".join(
            self.message_chunks.order_by("index").values_list("text", flat=True)
        )


class LogMessageChunk(models.Model):
    """Store a segment of a large log message."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    log = models.ForeignKey(
        Log,
        on_delete=models.CASCADE,
        related_name="message_chunks",
    )
    index = models.PositiveIntegerField()
    text = models.TextField()

    class Meta:
        db_table = "log_panel_log_message_chunk"
        verbose_name = "Log message chunk"
        verbose_name_plural = "Log message chunks"
        constraints = [
            models.UniqueConstraint(
                fields=("log", "index"),
                name="unique_log_message_chunk",
            )
        ]
        indexes: list[Index] = [
            models.Index(fields=("log", "index"), name="log_message_chunk_order"),
        ]


class Logger(models.Model):
    """Represents a logger."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, unique=True)

    class Meta:
        db_table = "log_panel_logger"

    def __str__(self) -> str:  # pragma: no cover
        return self.name


class LogCard(models.Model):
    """Pre-computed per-logger counters for the cards dashboard."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    logger = models.OneToOneField(Logger, on_delete=models.CASCADE, related_name="card")
    total = models.PositiveBigIntegerField(default=0)
    total_errors = models.PositiveBigIntegerField(default=0)
    total_warnings = models.PositiveBigIntegerField(default=0)
    last_seen = models.DateTimeField(null=True, blank=True)

    objects = LogCardManager()

    class Meta:
        db_table = "log_panel_log_card"
        ordering = ["-last_seen"]

    def __str__(self) -> str:  # pragma: no cover
        return f"LogCard({self.logger})"


class LogTimelineBucket(models.Model):
    """Pre-computed log/error/warning counts for a single time bucket."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    logger = models.ForeignKey(
        Logger, on_delete=models.CASCADE, related_name="timeline_buckets"
    )
    bucket = models.DateTimeField()
    unit = models.CharField(max_length=4, choices=RangeUnit.choices())
    log_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    warning_count = models.PositiveIntegerField(default=0)

    objects = TimelineBucketManager()

    class Meta:
        db_table = "log_panel_log_timeline_bucket"
        constraints = [
            models.UniqueConstraint(
                fields=("logger", "bucket", "unit"),
                name="unique_timeline_bucket",
            )
        ]
        indexes: list[Index] = [
            models.Index(
                fields=("logger", "unit", "bucket"),
                name="timeline_logger_unit_bucket",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"TimelineBucket({self.logger}, {self.unit}, {self.bucket})"
