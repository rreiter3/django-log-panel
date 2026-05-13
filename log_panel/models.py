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
