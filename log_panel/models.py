from django.db import models

from log_panel.managers import PanelManager


class Panel(models.Model):
    """Structured log record stored in a SQL database.

    Used by the SQL backend and ``DatabaseHandler``. When using the MongoDB
    backend this model exists only for the admin registration; no SQL table
    is created (``LogsRouter.allow_migrate`` returns ``False``).
    """

    timestamp = models.DateTimeField(db_index=True)
    level = models.CharField(max_length=10)
    logger_name = models.CharField(max_length=200, db_index=True)
    message = models.TextField()
    module = models.CharField(max_length=200)
    pathname = models.CharField(max_length=500)
    line_number = models.IntegerField()

    objects = PanelManager()

    class Meta:
        verbose_name = "Panel"
        verbose_name_plural = "Panels"
