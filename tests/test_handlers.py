import asyncio
import logging
import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.test import override_settings

from log_panel.handlers import DatabaseHandler
from log_panel.models import Log
from log_panel.signals import ThresholdAlertEvent, log_threshold_reached
from log_panel.types import LogLevel


def set_record_created(
    record: logging.LogRecord, timestamp: datetime
) -> logging.LogRecord:
    record.created = timestamp.timestamp()
    return record


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
def test_database_handler_emit_creates_panel_record(log_record_factory):
    handler = DatabaseHandler()
    handler.emit(log_record_factory())
    assert Log.objects.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("logger_name", ["pymongo", "pymongo.topology"])
def test_database_handler_ignores_internal_loggers(log_record_factory, logger_name):
    handler = DatabaseHandler()
    handler.emit(log_record_factory(name=logger_name))
    assert Log.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "logger_name",
    ["django.db.backends", "django_mongodb_backend", "sql.scada"],
)
def test_database_handler_keeps_database_and_sql_loggers(
    log_record_factory,
    logger_name,
):
    handler = DatabaseHandler()
    handler.emit(log_record_factory(name=logger_name))
    assert Log.objects.filter(logger_name=logger_name).count() == 1


@pytest.mark.django_db
def test_database_handler_emit_maps_fields_correctly(log_record_factory):
    record = log_record_factory(
        name="billing",
        level=logging.WARNING,
        msg="low balance",
        module="billing",
        pathname="/app/billing.py",
        lineno=12,
    )
    handler = DatabaseHandler()
    handler.emit(record)

    panel = Log.objects.get()
    expected_ts = datetime.fromtimestamp(record.created, tz=UTC)
    assert panel.timestamp == expected_ts
    assert panel.level == "WARNING"
    assert panel.logger_name == "billing"
    assert panel.module == "billing"
    assert panel.pathname == "/app/billing.py"
    assert panel.line_number == 12
    assert panel.timestamp.tzinfo is not None


@pytest.mark.django_db
@override_settings(USE_TZ=False, TIME_ZONE="Europe/Budapest")
def test_database_handler_emit_uses_naive_timestamp_when_use_tz_false(
    log_record_factory,
):
    current_time = datetime(2024, 6, 15, 12, 0, tzinfo=UTC)
    record = set_record_created(log_record_factory(), current_time)

    handler = DatabaseHandler()
    handler.emit(record)

    panel = Log.objects.get()
    assert panel.timestamp == datetime(2024, 6, 15, 14, 0)
    assert panel.timestamp.tzinfo is None


@pytest.mark.django_db
def test_database_handler_emit_stores_raw_message(log_record_factory):
    record = log_record_factory(msg="raw message")
    handler = DatabaseHandler()
    formatter = logging.Formatter("[%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    handler.emit(record)

    panel = Log.objects.get()
    assert panel.message == "raw message"


@pytest.mark.django_db
@override_settings(
    LOG_PANEL={
        "MESSAGE_PREVIEW_LENGTH": 20,
        "MESSAGE_CHUNK_SIZE": 25,
    }
)
def test_database_handler_emit_chunks_large_messages(log_record_factory):
    message = "x" * 100
    record = log_record_factory(msg=message)
    handler = DatabaseHandler()

    handler.emit(record)

    panel = Log.objects.get()
    assert panel.message == message[:20]
    assert panel.message_size == 100
    assert panel.message_chunked is True
    assert panel.get_full_message() == message


@pytest.mark.django_db
@override_settings(
    LOG_PANEL={
        "MESSAGE_PREVIEW_LENGTH": 20,
        "MESSAGE_CHUNK_SIZE": 25,
    }
)
def test_database_handler_emit_keeps_small_messages_inline(log_record_factory):
    message = "x" * 20
    record = log_record_factory(msg=message)
    handler = DatabaseHandler()

    handler.emit(record)

    panel = Log.objects.get()
    assert panel.message == message
    assert panel.message_size == 20
    assert panel.message_chunked is False
    assert panel.message_chunks.count() == 0


@pytest.mark.django_db
def test_database_handler_emit_calls_handle_error_on_exception(log_record_factory):
    handler = DatabaseHandler()
    record = log_record_factory()

    with patch("log_panel.models.Log.objects") as mock_objects:
        mock_objects.create_from_record.side_effect = RuntimeError("db down")
        with patch.object(handler, "handleError") as mock_handle_error:
            handler.emit(record)
            mock_handle_error.assert_called_once_with(record)


def test_database_handler_emit_from_async_context_uses_worker_thread(
    log_record_factory,
):
    handler = DatabaseHandler()
    record = log_record_factory()
    caller_thread_id = threading.get_ident()
    worker_thread_ids: list[int] = []

    def persist_record(*, record: logging.LogRecord) -> None:
        worker_thread_ids.append(threading.get_ident())

    async def emit_record() -> None:
        handler.handle(record)

    with patch.object(
        handler, "_persist_record", side_effect=persist_record
    ) as mock_persist_record:
        with patch.object(handler, "handleError") as mock_handle_error:
            with patch(
                "log_panel.handlers.sql.close_old_connections"
            ) as mock_close_old_connections:
                asyncio.run(emit_record())

    mock_persist_record.assert_called_once_with(record=record)
    mock_handle_error.assert_not_called()
    assert len(worker_thread_ids) == 1
    assert worker_thread_ids[0] != caller_thread_id
    assert mock_close_old_connections.call_count == 2


def test_database_handler_async_worker_skips_reentrant_call(log_record_factory):
    handler = DatabaseHandler()
    outer_record = log_record_factory(msg="outer")
    nested_record = log_record_factory(msg="nested")
    persisted_messages: list[str] = []

    def persist_record(*, record: logging.LogRecord) -> None:
        persisted_messages.append(record.getMessage())
        handler.handle(nested_record)

    async def emit_record() -> None:
        handler.handle(outer_record)

    with patch.object(handler, "_persist_record", side_effect=persist_record):
        asyncio.run(emit_record())

    assert persisted_messages == ["outer"]


def test_database_handler_async_worker_silently_discards_database_error(
    log_record_factory,
):
    from django.db import ProgrammingError

    handler = DatabaseHandler()
    record = log_record_factory()

    async def emit_record() -> None:
        handler.emit(record)

    with patch.object(
        handler, "_persist_record", side_effect=ProgrammingError("no table")
    ):
        with patch.object(handler, "handleError") as mock_handle_error:
            asyncio.run(emit_record())

    mock_handle_error.assert_not_called()


def test_database_handler_async_worker_silently_discards_pymongo_error(
    log_record_factory,
):
    pytest.importorskip("pymongo")
    from pymongo.errors import BulkWriteError

    handler = DatabaseHandler()
    record = log_record_factory()

    async def emit_record() -> None:
        handler.emit(record)

    with patch.object(
        handler,
        "_persist_record",
        side_effect=BulkWriteError({"writeErrors": []}),
    ):
        with patch.object(handler, "handleError") as mock_handle_error:
            asyncio.run(emit_record())

    mock_handle_error.assert_not_called()


def test_database_handler_async_worker_calls_handle_error_on_exception(
    log_record_factory,
):
    handler = DatabaseHandler()
    record = log_record_factory()

    async def emit_record() -> None:
        handler.emit(record)

    with patch.object(handler, "_persist_record", side_effect=RuntimeError("db down")):
        with patch.object(handler, "handleError") as mock_handle_error:
            asyncio.run(emit_record())

    mock_handle_error.assert_called_once_with(record)


@pytest.mark.django_db
@pytest.mark.parametrize("level", [logging.DEBUG, logging.INFO])
def test_database_handler_emit_does_not_signal_for_non_alert_levels(
    log_record_factory,
    threshold_event_recorder,
    level,
):
    handler = DatabaseHandler()

    handler.emit(log_record_factory(level=level))

    assert threshold_event_recorder == []


@pytest.mark.django_db
@override_settings(LOG_PANEL={"THRESHOLDS": {"WARNING": 3}})
def test_database_handler_emit_does_not_signal_before_warning_threshold(
    panel_factory,
    log_record_factory,
    threshold_event_recorder,
):
    current_time = datetime(2024, 6, 15, 15, 0, tzinfo=UTC)
    panel_factory(level="WARNING", timestamp=current_time - timedelta(minutes=5))
    handler = DatabaseHandler()
    record = set_record_created(
        log_record_factory(level=logging.WARNING, msg="second warning"),
        current_time,
    )

    handler.emit(record)

    assert threshold_event_recorder == []


@pytest.mark.django_db
@override_settings(LOG_PANEL={"THRESHOLDS": {"WARNING": 2}})
def test_database_handler_emit_signals_when_warning_threshold_is_reached(
    panel_factory,
    log_record_factory,
    threshold_event_recorder,
):
    current_time = datetime(2024, 6, 15, 15, 0, tzinfo=UTC)
    panel_factory(level="WARNING", timestamp=current_time - timedelta(minutes=5))
    handler = DatabaseHandler()
    record = set_record_created(
        log_record_factory(level=logging.WARNING, msg="threshold warning"),
        current_time,
    )

    handler.emit(record)

    assert len(threshold_event_recorder) == 1
    sender, event = threshold_event_recorder[0]
    assert sender is DatabaseHandler
    assert event.threshold_level == LogLevel.WARNING
    assert event.record_level == LogLevel.WARNING
    assert event.threshold == 2
    assert event.matching_count == 2
    assert event.timestamp == current_time
    assert event.window_start == current_time - timedelta(hours=1)
    assert event.window_end == current_time
    assert event.message == "threshold warning"


@pytest.mark.django_db
@override_settings(LOG_PANEL={"THRESHOLDS": {"ERROR": 2}})
def test_database_handler_emit_signals_when_error_threshold_is_reached(
    panel_factory,
    log_record_factory,
    threshold_event_recorder,
):
    current_time = datetime(2024, 6, 15, 15, 0, tzinfo=UTC)
    panel_factory(level="ERROR", timestamp=current_time - timedelta(minutes=10))
    handler = DatabaseHandler()
    record = set_record_created(
        log_record_factory(level=logging.ERROR, msg="threshold error"),
        current_time,
    )

    handler.emit(record)

    assert len(threshold_event_recorder) == 1
    _, event = threshold_event_recorder[0]
    assert event.threshold_level == LogLevel.ERROR
    assert event.record_level == LogLevel.ERROR
    assert event.matching_count == 2


@pytest.mark.django_db
@override_settings(LOG_PANEL={"THRESHOLDS": {"CRITICAL": 2}})
def test_database_handler_emit_critical_fires_its_own_threshold(
    panel_factory,
    log_record_factory,
    threshold_event_recorder,
):
    current_time = datetime(2024, 6, 15, 15, 0, tzinfo=UTC)
    panel_factory(level="CRITICAL", timestamp=current_time - timedelta(minutes=10))
    handler = DatabaseHandler()
    record = set_record_created(
        log_record_factory(level=logging.CRITICAL, msg="second critical"),
        current_time,
    )

    handler.emit(record)

    assert len(threshold_event_recorder) == 1
    _, event = threshold_event_recorder[0]
    assert event.threshold_level == LogLevel.CRITICAL
    assert event.record_level == LogLevel.CRITICAL
    assert event.matching_count == 2


@pytest.mark.django_db
@override_settings(LOG_PANEL={"THRESHOLDS": {"WARNING": 2}})
def test_database_handler_emit_does_not_signal_once_threshold_is_already_exceeded(
    panel_factory,
    log_record_factory,
    threshold_event_recorder,
):
    current_time = datetime(2024, 6, 15, 15, 0, tzinfo=UTC)
    panel_factory(level="WARNING", timestamp=current_time - timedelta(minutes=20))
    panel_factory(level="WARNING", timestamp=current_time - timedelta(minutes=10))
    handler = DatabaseHandler()
    record = set_record_created(log_record_factory(level=logging.WARNING), current_time)

    handler.emit(record)

    assert threshold_event_recorder == []


@pytest.mark.django_db
@override_settings(LOG_PANEL={"THRESHOLDS": {"WARNING": 2}})
def test_database_handler_emit_ignores_records_older_than_one_hour_for_threshold_count(
    panel_factory,
    log_record_factory,
    threshold_event_recorder,
):
    current_time = datetime(2024, 6, 15, 15, 0, tzinfo=UTC)
    panel_factory(
        level="WARNING",
        timestamp=current_time - timedelta(hours=1, seconds=1),
    )
    handler = DatabaseHandler()
    record = set_record_created(log_record_factory(level=logging.WARNING), current_time)

    handler.emit(record)

    assert threshold_event_recorder == []


@pytest.mark.django_db
def test_database_handler_emit_does_not_signal_when_persistence_fails(
    log_record_factory,
    threshold_event_recorder,
):
    handler = DatabaseHandler()
    record = log_record_factory(level=logging.WARNING)

    with patch("log_panel.models.Log.objects") as mock_objects:
        mock_objects.create_from_record.side_effect = RuntimeError("db down")
        with patch.object(handler, "handleError") as mock_handle_error:
            handler.emit(record)

    assert threshold_event_recorder == []
    mock_handle_error.assert_called_once_with(record)


@pytest.mark.django_db
@override_settings(LOG_PANEL={"THRESHOLDS": {"WARNING": 1}})
def test_database_handler_emit_ignores_receiver_exceptions_for_threshold_signal(
    log_record_factory,
):
    handler = DatabaseHandler()
    record = log_record_factory(level=logging.WARNING)

    def failing_receiver(sender, event: ThresholdAlertEvent, **kwargs):
        raise RuntimeError("receiver failed")

    dispatch_uid = object()
    log_threshold_reached.connect(
        failing_receiver,
        dispatch_uid=dispatch_uid,
        weak=False,
    )
    try:
        handler.emit(record)
    finally:
        log_threshold_reached.disconnect(dispatch_uid=dispatch_uid)

    assert Log.objects.count() == 1


@pytest.mark.django_db
def test_database_handler_emit_skips_reentrant_call(log_record_factory):
    handler = DatabaseHandler()
    handler._local.emitting = True
    try:
        handler.emit(log_record_factory())
    finally:
        handler._local.emitting = False
    assert Log.objects.count() == 0


@pytest.mark.django_db
def test_database_handler_emit_silently_discards_programming_error(log_record_factory):
    from django.db import ProgrammingError

    handler = DatabaseHandler()
    record = log_record_factory()
    with patch("log_panel.models.Log.objects") as mock_objects:
        mock_objects.create_from_record.side_effect = ProgrammingError("no table")
        with patch.object(handler, "handleError") as mock_handle_error:
            handler.emit(record)
    mock_handle_error.assert_not_called()
