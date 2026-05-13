import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import partial
from logging import Formatter, Handler, LogRecord
from typing import Any

from django.db import close_old_connections

from log_panel.alerts import maybe_emit_threshold_signal
from log_panel.datetimes import from_record_timestamp


class DatabaseHandler(Handler):
    """
    Persist log records.

    The target database alias is resolved via ``LOG_PANEL['DATABASE_ALIAS']``.
    """

    _local = threading.local()
    _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="log-panel-sql")

    @staticmethod
    def count_matching_records(
        logger_name: str,
        levels: tuple[str, ...],
        window_start: datetime,
        window_end: datetime,
    ) -> int:
        from log_panel.models import Log

        return Log.objects.count_threshold_matches(
            logger_name=logger_name,
            levels=levels,
            window_start=window_start,
            window_end=window_end,
        )

    def emit(self, record: LogRecord) -> None:
        """
        Format and insert a log record into the configured SQL database.

        A thread-local guard prevents infinite recursion when a database execute wrapper itself emits log records
        while this handler is mid-write.

        In ASGI contexts, Django protects the sync ORM from running inside an active event loop. Those records are
        persisted through a dedicated sync worker thread while preserving logging's synchronous delivery semantics.

        Records emitted before the ``log_panel`` table exists (i.e. during ``migrate``) are silently discarded so they
        cannot poison an in-progress migration transaction.
        """
        if getattr(self._local, "emitting", False):
            return
        if self._is_ignored_logger(logger_name=record.name):
            return
        if self._is_ignored_message(record=record):
            return
        storage_error_classes = self._storage_error_classes()
        try:
            if self._in_async_context():
                self._wait_for_worker(record=record)
                return

            self._emit_guarded(record=record, manage_connections=False)
        except storage_error_classes:
            pass
        except Exception:
            self.handleError(record)

    @staticmethod
    def _is_ignored_logger(*, logger_name: str) -> bool:
        from log_panel.conf import (
            get_ignored_logger_names,
            get_ignored_logger_prefixes,
        )

        ignored_names = get_ignored_logger_names()
        if logger_name in ignored_names:
            return True

        for prefix in get_ignored_logger_prefixes():
            if logger_name == prefix or logger_name.startswith(f"{prefix}."):
                return True
        return False

    @staticmethod
    def _is_ignored_message(*, record: LogRecord) -> bool:
        from log_panel.conf import get_ignored_message_substrings

        ignored_substrings = get_ignored_message_substrings()
        if not ignored_substrings:
            return False

        raw_parts: list[str] = []
        if isinstance(record.msg, str):
            raw_parts.append(record.msg)
        if isinstance(record.args, tuple):
            raw_parts.extend(arg for arg in record.args if isinstance(arg, str))
        for substring in ignored_substrings:
            if any(substring in part for part in raw_parts):
                return True

        message = record.getMessage()
        return any(substring in message for substring in ignored_substrings)

    @staticmethod
    def _storage_error_classes() -> tuple[type[Exception], ...]:
        from django.db import DatabaseError, InternalError, ProgrammingError

        storage_error_classes: tuple[type[Exception], ...] = (
            ProgrammingError,
            InternalError,
            DatabaseError,
        )
        try:
            from pymongo.errors import PyMongoError
        except ImportError:  # pragma: no cover
            return storage_error_classes
        return (*storage_error_classes, PyMongoError)

    @staticmethod
    def _in_async_context() -> bool:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False
        return True

    def _emit_in_worker(self, record: LogRecord) -> None:
        self._emit_guarded(record=record, manage_connections=True)

    def _wait_for_worker(self, *, record: LogRecord) -> None:
        future = self._executor.submit(self._emit_in_worker, record)
        lock_released = False
        is_owned: Any = getattr(self.lock, "_is_owned", None)
        if callable(is_owned) and is_owned():
            # Handler.handle holds this lock around emit; release it while waiting so worker-thread logging can hit
            # the thread-local recursion guard instead of blocking behind the caller.
            self.release()
            lock_released = True
        try:
            future.result()
        finally:
            if lock_released:
                self.acquire()

    def _emit_guarded(self, *, record: LogRecord, manage_connections: bool) -> None:
        if getattr(self._local, "emitting", False):
            return
        self._local.emitting = True
        if manage_connections:
            close_old_connections()
        try:
            self._persist_record(record=record)
        finally:
            if manage_connections:
                close_old_connections()
            self._local.emitting = False

    def _persist_record(self, *, record: LogRecord) -> None:
        from log_panel.models import Log

        panel = Log.objects.create_from_record(
            timestamp=from_record_timestamp(timestamp=record.created),
            level=record.levelname,
            logger_name=record.name,
            message=self._format_message(record=record),
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

    @classmethod
    def _format_message(cls, *, record: LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + Formatter().formatException(ei=record.exc_info)
        return message
