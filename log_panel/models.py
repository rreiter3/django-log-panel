import uuid

from django.db import models
from django.db.models.indexes import Index

from log_panel.managers import LogRecordManager


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
