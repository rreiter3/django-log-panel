import threading
from datetime import UTC, datetime
from functools import partial
from logging import Formatter, Handler, LogRecord

from log_panel.alerts import maybe_emit_threshold_signal


class DatabaseHandler(Handler):
    """Write log records to a SQL database via Django ORM.

    The target database alias is resolved via ``LOG_PANEL['DATABASE_ALIAS']``.

    Configure in Django LOGGING::

        'handlers': {
            'log_panel': {
                'class': 'log_panel.handlers.DatabaseHandler',
            }
        }
    """

    _local = threading.local()

    @staticmethod
    def count_matching_records(
        logger_name: str,
        levels: tuple[str, ...],
        window_start: datetime,
        window_end: datetime,
    ) -> int:
        from log_panel.models import Panel

        return Panel.objects.count_threshold_matches(
            logger_name=logger_name,
            levels=levels,
            window_start=window_start,
            window_end=window_end,
        )

    def emit(self, record: LogRecord) -> None:
        """Format and insert a log record into the configured SQL database.

        A thread-local guard prevents infinite recursion when a database execute wrapper itself emits log records
        while this handler is mid-write.

        Records emitted before the ``log_panel`` table exists (i.e. during ``migrate``) are silently discarded so they
        cannot poison an in-progress migration transaction.
        """
        if getattr(self._local, "emitting", False):
            return
        self._local.emitting = True
        try:
            from django.db import InternalError, ProgrammingError, transaction

            from log_panel.conf import get_database_alias
            from log_panel.models import Panel

            alias = get_database_alias()
            with transaction.atomic(using=alias):
                panel = Panel.objects.create_from_record(
                    timestamp=datetime.fromtimestamp(timestamp=record.created, tz=UTC),
                    level=record.levelname,
                    logger_name=record.name,
                    message=record.getMessage()
                    + (
                        "\n" + Formatter().formatException(ei=record.exc_info)
                        if record.exc_info
                        else ""
                    ),
                    module=record.module,
                    pathname=record.pathname,
                    line_number=record.lineno,
                )
            maybe_emit_threshold_signal(
                sender=self.__class__,
                logger_name=panel.logger_name,
                record_level=panel.level,
                timestamp=panel.timestamp,
                message=panel.message,
                module=panel.module,
                pathname=panel.pathname,
                line_number=panel.line_number,
                count_matching_records=partial(
                    self.count_matching_records, panel.logger_name
                ),
            )
        except (ProgrammingError, InternalError):
            pass
        except Exception:
            self.handleError(record)
        finally:
            self._local.emitting = False
