import asyncio
import logging
import os
import sys
import threading
import time
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

    def __init__(self) -> None:
        super().__init__()
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="log-panel-sql",
        )

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
        if self._is_migration_command():
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

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        super().close()

    @staticmethod
    def _is_migration_command() -> bool:
        return bool({"makemigrations", "migrate"} & set(sys.argv))

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


class BufferedDatabaseHandler(DatabaseHandler):
    """
    A DatabaseHandler that accumulates records in memory and flushes them in batches.

    Flushes when any of the following conditions are met:

    - The buffer reaches ``LOG_PANEL['BUFFER_SIZE']`` records.
    - A record at or above ``LOG_PANEL['BUFFER_FLUSH_LEVEL']`` is emitted.
    - A later record arrives after ``LOG_PANEL['BUFFER_FLUSH_INTERVAL']`` seconds.
    - ``flush()`` or ``close()`` is called explicitly.

    Batches are written with a single ``bulk_create`` call.  Threshold signals fire
    per-record after each flush, so alert semantics are preserved.

    Records not yet flushed at process exit are lost only on abrupt termination
    (SIGKILL, OOM). Normal shutdown calls ``close()``, which drains the buffer first.
    """

    def __init__(self) -> None:
        super().__init__()
        self._buffer: list[LogRecord] = []
        self._buffer_lock = threading.Lock()
        self._closed = False
        self._owner_pid = os.getpid()
        self._last_flush_at = time.monotonic()

    def emit(self, record: LogRecord) -> None:
        if getattr(self._local, "emitting", False):
            return
        if self._is_migration_command():
            return
        if self._is_ignored_logger(logger_name=record.name):
            return
        if self._is_ignored_message(record=record):
            return

        storage_error_classes = self._storage_error_classes()
        try:
            self._ensure_process_state()

            from log_panel.conf import (
                get_buffer_flush_interval,
                get_buffer_flush_level,
                get_buffer_size,
            )

            buffer_size: int = get_buffer_size() or 100
            flush_interval: float = get_buffer_flush_interval()
            flush_level_no: int = logging.getLevelName(get_buffer_flush_level())

            batch: list[LogRecord] | None = None
            with self._buffer_lock:
                self._buffer.append(record)
                interval_elapsed = (
                    time.monotonic() - self._last_flush_at >= flush_interval
                )
                if (
                    len(self._buffer) >= buffer_size
                    or record.levelno >= flush_level_no
                    or interval_elapsed
                ):
                    batch = self._buffer[:]
                    self._buffer.clear()

            if batch:
                self._dispatch_batch(batch)
                self._last_flush_at = time.monotonic()
        except storage_error_classes:
            pass
        except Exception:
            self.handleError(record)

    def flush(self) -> None:
        if self._is_migration_command():
            return
        self._ensure_process_state()
        with self._buffer_lock:
            if not self._buffer:
                return
            batch = self._buffer[:]
            self._buffer.clear()
        self._dispatch_batch(batch)
        self._last_flush_at = time.monotonic()

    def close(self) -> None:
        self._closed = True
        self.flush()
        super().close()

    def _ensure_process_state(self) -> None:
        current_pid = os.getpid()
        if current_pid == self._owner_pid:
            return
        self._owner_pid = current_pid
        self._buffer_lock = threading.Lock()
        self._last_flush_at = time.monotonic()

    def _dispatch_batch(self, records: list[LogRecord]) -> None:
        storage_error_classes = self._storage_error_classes()
        try:
            if self._in_async_context():
                self._wait_for_batch(records=records)
            else:
                self._emit_batch_guarded(records=records, manage_connections=False)
        except storage_error_classes:
            pass
        except Exception:
            for record in records:
                self.handleError(record)

    def _wait_for_batch(self, *, records: list[LogRecord]) -> None:
        future = self._executor.submit(self._emit_batch_in_worker, records)
        lock_released = False
        is_owned: Any = getattr(self.lock, "_is_owned", None)
        if callable(is_owned) and is_owned():
            self.release()
            lock_released = True
        try:
            future.result()
        finally:
            if lock_released:
                self.acquire()

    def _emit_batch_in_worker(self, records: list[LogRecord]) -> None:
        self._emit_batch_guarded(records=records, manage_connections=True)

    def _emit_batch_guarded(
        self, *, records: list[LogRecord], manage_connections: bool
    ) -> None:
        if getattr(self._local, "emitting", False):
            return
        self._local.emitting = True
        if manage_connections:
            close_old_connections()
        try:
            self._persist_batch(records=records)
        finally:
            if manage_connections:
                close_old_connections()
            self._local.emitting = False

    def _persist_batch(self, *, records: list[LogRecord]) -> None:
        from log_panel.models import Log

        formatted = [
            {
                "timestamp": from_record_timestamp(timestamp=r.created),
                "level": r.levelname,
                "logger_name": r.name,
                "message": self._format_message(record=r),
                "module": r.module,
                "pathname": r.pathname,
                "line_number": r.lineno,
            }
            for r in records
        ]

        logs: list[Log] = Log.objects.bulk_create_from_records(formatted)

        for log in logs:
            maybe_emit_threshold_signal(
                sender=self.__class__,
                logger_name=log.logger_name,
                record_level=log.level,
                timestamp=log.timestamp,
                message=log.message,
                module=log.module,
                pathname=log.pathname,
                line_number=log.line_number,
                count_matching_records=partial(
                    self.count_matching_records, log.logger_name
                ),
            )
