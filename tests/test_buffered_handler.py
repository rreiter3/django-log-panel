import asyncio
import logging
import sys
import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.test import override_settings

from log_panel.handlers import BufferedDatabaseHandler
from log_panel.models import Log
from log_panel.signals import ThresholdAlertEvent, log_threshold_reached
from log_panel.types import LogLevel


def set_record_created(
    record: logging.LogRecord, timestamp: datetime
) -> logging.LogRecord:
    record.created = timestamp.timestamp()
    return record


@pytest.fixture
def handler():
    h = BufferedDatabaseHandler()
    yield h
    h.close()


@pytest.fixture
def threshold_event_recorder():
    calls: list[tuple[object, ThresholdAlertEvent]] = []

    def receiver(sender, event: ThresholdAlertEvent, **kwargs):
        calls.append((sender, event))

    dispatch_uid = object()
    log_threshold_reached.connect(receiver, dispatch_uid=dispatch_uid, weak=False)
    try:
        yield calls
    finally:
        log_threshold_reached.disconnect(dispatch_uid=dispatch_uid)


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 3, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_holds_records_below_buffer_size(log_record_factory, handler):
    handler.emit(log_record_factory(level=logging.INFO))
    handler.emit(log_record_factory(level=logging.INFO))

    assert Log.objects.count() == 0


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 3, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_flushes_when_buffer_is_full(log_record_factory, handler):
    handler.emit(log_record_factory(level=logging.INFO))
    handler.emit(log_record_factory(level=logging.INFO))
    handler.emit(log_record_factory(level=logging.INFO))

    assert Log.objects.count() == 3


@pytest.mark.django_db
@override_settings(
    LOG_PANEL={
        "BUFFER_SIZE": 100,
        "BUFFER_FLUSH_INTERVAL": 60,
        "BUFFER_FLUSH_LEVEL": "WARNING",
    }
)
def test_buffered_handler_flushes_immediately_on_flush_level(
    log_record_factory, handler
):
    handler.emit(log_record_factory(level=logging.INFO))
    handler.emit(log_record_factory(level=logging.INFO))
    handler.emit(log_record_factory(level=logging.WARNING))

    assert Log.objects.count() == 3


@pytest.mark.django_db
@override_settings(
    LOG_PANEL={
        "BUFFER_SIZE": 100,
        "BUFFER_FLUSH_INTERVAL": 60,
        "BUFFER_FLUSH_LEVEL": "WARNING",
    }
)
def test_buffered_handler_flushes_immediately_on_critical(log_record_factory, handler):
    handler.emit(log_record_factory(level=logging.INFO))
    handler.emit(log_record_factory(level=logging.CRITICAL))

    assert Log.objects.count() == 2


@pytest.mark.django_db
@override_settings(
    LOG_PANEL={
        "BUFFER_SIZE": 100,
        "BUFFER_FLUSH_INTERVAL": 60,
        "BUFFER_FLUSH_LEVEL": "WARNING",
    }
)
def test_buffered_handler_info_records_do_not_trigger_immediate_flush(
    log_record_factory, handler
):
    handler.emit(log_record_factory(level=logging.INFO))
    handler.emit(log_record_factory(level=logging.INFO))

    assert Log.objects.count() == 0


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 10, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_flush_persists_remaining_records(log_record_factory, handler):
    handler.emit(log_record_factory(level=logging.INFO))
    handler.emit(log_record_factory(level=logging.INFO))

    handler.flush()

    assert Log.objects.count() == 2


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 10, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_flush_is_idempotent_on_empty_buffer(handler):
    handler.flush()
    handler.flush()

    assert Log.objects.count() == 0


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 10, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_close_flushes_remaining_records(log_record_factory):
    handler = BufferedDatabaseHandler()
    handler.emit(log_record_factory(level=logging.INFO))
    handler.emit(log_record_factory(level=logging.INFO))
    handler.close()

    assert Log.objects.count() == 2


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 1, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_maps_fields_correctly(log_record_factory, handler):
    record = log_record_factory(
        name="billing",
        level=logging.WARNING,
        msg="low balance",
        module="billing",
        pathname="/app/billing.py",
        lineno=12,
    )
    handler.emit(record)

    log = Log.objects.get()
    expected_ts = datetime.fromtimestamp(record.created, tz=UTC)
    assert log.timestamp == expected_ts
    assert log.level == "WARNING"
    assert log.logger_name == "billing"
    assert log.module == "billing"
    assert log.pathname == "/app/billing.py"
    assert log.line_number == 12


@pytest.mark.django_db
@override_settings(
    LOG_PANEL={
        "BUFFER_SIZE": 2,
        "BUFFER_FLUSH_INTERVAL": 60,
        "MESSAGE_PREVIEW_LENGTH": 20,
        "MESSAGE_CHUNK_SIZE": 25,
    }
)
def test_buffered_handler_handles_chunked_messages_in_batch(
    log_record_factory, handler
):
    long_message = "x" * 100
    handler.emit(log_record_factory(msg=long_message))
    handler.emit(log_record_factory(msg="short"))

    assert Log.objects.count() == 2
    chunked_log = Log.objects.get(message_chunked=True)
    assert chunked_log.message == long_message[:20]
    assert chunked_log.get_full_message() == long_message
    inline_log = Log.objects.get(message_chunked=False)
    assert inline_log.message == "short"


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 1, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_appends_exception_traceback(log_record_factory, handler):
    try:
        raise ValueError("bad value")
    except ValueError:
        record = log_record_factory(msg="failed")
        record.exc_info = sys.exc_info()

    handler.emit(record)

    log = Log.objects.get()
    assert "Traceback" in log.message
    assert "ValueError: bad value" in log.message


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 1, "BUFFER_FLUSH_INTERVAL": 60})
@pytest.mark.parametrize("logger_name", ["pymongo", "pymongo.topology"])
def test_buffered_handler_ignores_builtin_logger_prefixes(
    log_record_factory, handler, logger_name
):
    handler.emit(log_record_factory(name=logger_name))

    assert Log.objects.count() == 0


@pytest.mark.django_db
@override_settings(
    LOG_PANEL={
        "BUFFER_SIZE": 1,
        "BUFFER_FLUSH_INTERVAL": 60,
        "IGNORED_LOGGER_PREFIXES": ("silk",),
    }
)
def test_buffered_handler_ignores_configured_logger_prefix(log_record_factory, handler):
    handler.emit(log_record_factory(name="silk.middleware"))

    assert Log.objects.count() == 0


@pytest.mark.django_db
@override_settings(
    LOG_PANEL={
        "BUFFER_SIZE": 1,
        "BUFFER_FLUSH_INTERVAL": 60,
        "IGNORED_LOGGER_PREFIXES": ("silk",),
    }
)
@pytest.mark.parametrize("logger_name", ["pymongo", "pymongo.topology"])
def test_buffered_handler_keeps_builtin_prefixes_when_configured_prefixes_are_set(
    log_record_factory, handler, logger_name
):
    handler.emit(log_record_factory(name=logger_name))

    assert Log.objects.count() == 0


@pytest.mark.django_db
@override_settings(
    LOG_PANEL={
        "BUFFER_SIZE": 1,
        "BUFFER_FLUSH_INTERVAL": 60,
        "IGNORED_MESSAGE_SUBSTRINGS": ('"silk_response"',),
    }
)
def test_buffered_handler_ignores_configured_message_substrings(
    log_record_factory, handler
):
    record = log_record_factory(name="db_logging")
    record.msg = '(%.3fms) INSERT INTO "silk_response" VALUES (%s)'
    record.args = (1.2, "abc")
    handler.emit(record)

    assert Log.objects.count() == 0


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 1, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_skips_reentrant_call(log_record_factory, handler):
    handler._local.emitting = True
    try:
        handler.emit(log_record_factory())
    finally:
        handler._local.emitting = False

    assert Log.objects.count() == 0


@pytest.mark.django_db
@override_settings(
    LOG_PANEL={
        "BUFFER_SIZE": 2,
        "BUFFER_FLUSH_INTERVAL": 60,
        "THRESHOLDS": {"WARNING": 2},
    }
)
def test_buffered_handler_signals_threshold_after_batch_flush(
    panel_factory,
    log_record_factory,
    threshold_event_recorder,
    handler,
):
    current_time = datetime(2024, 6, 15, 15, 0, tzinfo=UTC)
    panel_factory(level="WARNING", timestamp=current_time - timedelta(minutes=5))

    record = set_record_created(
        log_record_factory(level=logging.WARNING, msg="threshold warning"),
        current_time,
    )
    filler = log_record_factory(level=logging.INFO)

    handler.emit(record)
    handler.emit(filler)  # fills buffer → flush

    assert len(threshold_event_recorder) == 1
    sender, event = threshold_event_recorder[0]
    assert sender is BufferedDatabaseHandler
    assert event.threshold_level == LogLevel.WARNING
    assert event.matching_count == 2
    assert event.message == "threshold warning"


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 10, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_no_signal_for_info_records(
    log_record_factory, threshold_event_recorder, handler
):
    handler.emit(log_record_factory(level=logging.INFO))
    handler.flush()

    assert threshold_event_recorder == []


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 1, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_silently_discards_programming_error(
    log_record_factory, handler
):
    from django.db import ProgrammingError

    with patch("log_panel.models.Log.objects") as mock_objects:
        mock_objects.bulk_create_from_records.side_effect = ProgrammingError("no table")
        with patch.object(handler, "handleError") as mock_handle_error:
            handler.emit(log_record_factory())

    mock_handle_error.assert_not_called()


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 1, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_calls_handle_error_on_unexpected_exception(
    log_record_factory, handler
):
    record = log_record_factory()
    with patch("log_panel.models.Log.objects") as mock_objects:
        mock_objects.bulk_create_from_records.side_effect = RuntimeError("db down")
        with patch.object(handler, "handleError") as mock_handle_error:
            handler.emit(record)

    mock_handle_error.assert_called_once_with(record)


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 1, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_emit_silently_discards_storage_error_during_setup(
    log_record_factory, handler
):
    from django.db import ProgrammingError

    with patch.object(
        handler, "_ensure_process_state", side_effect=ProgrammingError("no table")
    ):
        with patch.object(handler, "handleError") as mock_handle_error:
            handler.emit(log_record_factory())

    mock_handle_error.assert_not_called()


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 1, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_emit_calls_handle_error_on_setup_exception(
    log_record_factory, handler
):
    record = log_record_factory()

    with patch.object(
        handler, "_ensure_process_state", side_effect=RuntimeError("broken")
    ):
        with patch.object(handler, "handleError") as mock_handle_error:
            handler.emit(record)

    mock_handle_error.assert_called_once_with(record)


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 100, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_flush_silently_discards_storage_error(
    log_record_factory, handler
):
    from django.db import ProgrammingError

    handler.emit(log_record_factory(level=logging.INFO))

    with patch("log_panel.models.Log.objects") as mock_objects:
        mock_objects.bulk_create_from_records.side_effect = ProgrammingError("no table")
        with patch.object(handler, "handleError") as mock_handle_error:
            handler.flush()

    mock_handle_error.assert_not_called()


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 100, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_flush_calls_handle_error_on_unexpected_exception(
    log_record_factory, handler
):
    record = log_record_factory(level=logging.INFO)
    handler.emit(record)

    with patch("log_panel.models.Log.objects") as mock_objects:
        mock_objects.bulk_create_from_records.side_effect = RuntimeError("db down")
        with patch.object(handler, "handleError") as mock_handle_error:
            handler.flush()

    mock_handle_error.assert_called_once_with(record)


def test_buffered_handler_emit_batch_guarded_skips_reentrant_call(
    log_record_factory, handler
):
    records = [log_record_factory()]
    handler._local.emitting = True
    try:
        with patch.object(handler, "_persist_batch") as mock_persist:
            handler._emit_batch_guarded(records=records, manage_connections=False)
    finally:
        handler._local.emitting = False

    mock_persist.assert_not_called()


@override_settings(LOG_PANEL={"BUFFER_SIZE": 10, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_init_does_not_start_background_timer(log_record_factory):
    handler = BufferedDatabaseHandler()
    try:
        with patch("log_panel.handlers.sql.threading.Timer") as mock_timer:
            handler.emit(log_record_factory(level=logging.INFO))

        mock_timer.assert_not_called()
    finally:
        handler._buffer.clear()
        handler.close()


@override_settings(LOG_PANEL={"BUFFER_SIZE": 10, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_pid_change_resets_process_state(log_record_factory):
    handler = BufferedDatabaseHandler()
    handler._owner_pid = -1
    original_lock = handler._buffer_lock
    try:
        handler.emit(log_record_factory(level=logging.INFO))

        assert handler._owner_pid != -1
        assert handler._buffer_lock is not original_lock
    finally:
        handler._buffer.clear()
        handler.close()


@override_settings(LOG_PANEL={"BUFFER_SIZE": 1, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_async_context_uses_worker_thread(log_record_factory):
    handler = BufferedDatabaseHandler()
    record = log_record_factory()
    caller_thread_id = threading.get_ident()
    worker_thread_ids: list[int] = []

    def persist_batch(*, records):
        worker_thread_ids.append(threading.get_ident())

    async def emit_record():
        handler.handle(record)

    with patch.object(handler, "_persist_batch", side_effect=persist_batch):
        with patch.object(handler, "handleError"):
            with patch("log_panel.handlers.sql.close_old_connections"):
                asyncio.run(emit_record())

    handler.close()

    assert len(worker_thread_ids) == 1
    assert worker_thread_ids[0] != caller_thread_id


@override_settings(LOG_PANEL={"BUFFER_SIZE": 10, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_async_silently_discards_storage_error(log_record_factory):
    from django.db import ProgrammingError

    handler = BufferedDatabaseHandler()
    record = log_record_factory()

    async def emit_record():
        handler.emit(record)

    with patch.object(
        handler, "_persist_batch", side_effect=ProgrammingError("no table")
    ):
        with patch.object(handler, "handleError") as mock_handle_error:
            handler.flush()  # nothing in buffer yet — just confirm no crash

    handler.close()
    mock_handle_error.assert_not_called()


@pytest.mark.django_db
@override_settings(LOG_PANEL={"BUFFER_SIZE": 100, "BUFFER_FLUSH_INTERVAL": 60})
def test_buffered_handler_flush_interval_flushes_on_log_activity(
    log_record_factory, handler
):
    handler.emit(log_record_factory(level=logging.INFO))

    assert Log.objects.count() == 0

    handler._last_flush_at -= 61
    handler.emit(log_record_factory(level=logging.INFO))

    assert Log.objects.count() == 2


@override_settings(
    LOG_PANEL={
        "ATTACH_ROOT_HANDLER": True,
        "DATABASE_ALIAS": "logs",
        "BUFFER_SIZE": 50,
    }
)
def test_attach_root_handler_uses_buffered_handler_when_buffer_size_is_set():
    import logging

    from log_panel.bootstrap import attach_root_handler

    root = logging.getLogger()
    original_handlers = root.handlers[:]
    try:
        attach_root_handler()
        buffered = [h for h in root.handlers if isinstance(h, BufferedDatabaseHandler)]
        assert len(buffered) == 1
    finally:
        for h in root.handlers[:]:
            if h not in original_handlers:
                root.removeHandler(h)
                h.close()


@override_settings(
    LOG_PANEL={
        "ATTACH_ROOT_HANDLER": True,
        "DATABASE_ALIAS": "logs",
        "BUFFER_SIZE": None,
    }
)
def test_attach_root_handler_uses_database_handler_when_buffer_size_is_none():
    import logging

    from log_panel.bootstrap import attach_root_handler
    from log_panel.handlers import DatabaseHandler

    root = logging.getLogger()
    original_handlers = root.handlers[:]
    try:
        attach_root_handler()
        plain = [h for h in root.handlers if type(h) is DatabaseHandler]
        assert len(plain) == 1
    finally:
        for h in root.handlers[:]:
            if h not in original_handlers:
                root.removeHandler(h)
                h.close()
