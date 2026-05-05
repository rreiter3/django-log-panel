import logging
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from log_panel.exceptions.mongodb import MongoDBConnectionError, PyMongoNotInstalled
from log_panel.handlers import DatabaseHandler, MongoDBHandler
from log_panel.models import Panel
from log_panel.signals import ThresholdAlertEvent, log_threshold_reached
from log_panel.types import LogLevel


def make_pymongo_mock():
    mock_pymongo = MagicMock()
    mock_pymongo.ASCENDING = 1
    mock_pymongo.errors.ServerSelectionTimeoutError = type(
        "ServerSelectionTimeoutError", (Exception,), {}
    )
    return mock_pymongo


class MongoCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []
        self.count_queries: list[dict] = []

    def insert_one(self, doc: dict) -> None:
        self.docs.append(doc.copy())

    def count_documents(self, query: dict) -> int:
        self.count_queries.append(query)
        levels = query["level"].get("$in", [])
        timestamp_filter = query["timestamp"]
        return sum(
            1
            for doc in self.docs
            if doc["logger_name"] == query["logger_name"]
            and doc["level"] in levels
            and timestamp_filter["$gte"] <= doc["timestamp"] <= timestamp_filter["$lte"]
        )


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
    assert Panel.objects.count() == 1


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

    panel = Panel.objects.get()
    expected_ts = datetime.fromtimestamp(record.created, tz=UTC)
    assert panel.timestamp == expected_ts
    assert panel.level == "WARNING"
    assert panel.logger_name == "billing"
    assert panel.module == "billing"
    assert panel.pathname == "/app/billing.py"
    assert panel.line_number == 12
    assert panel.timestamp.tzinfo is not None


@pytest.mark.django_db
def test_database_handler_emit_stores_raw_message(log_record_factory):
    record = log_record_factory(msg="raw message")
    handler = DatabaseHandler()
    formatter = logging.Formatter("[%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    handler.emit(record)

    panel = Panel.objects.get()
    assert panel.message == "raw message"


@pytest.mark.django_db
def test_database_handler_emit_calls_handle_error_on_exception(log_record_factory):
    handler = DatabaseHandler()
    record = log_record_factory()

    with patch("log_panel.models.Panel.objects") as mock_objects:
        mock_objects.create_from_record.side_effect = RuntimeError("db down")
        with patch.object(handler, "handleError") as mock_handle_error:
            handler.emit(record)
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

    with patch("log_panel.models.Panel.objects") as mock_objects:
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

    assert Panel.objects.count() == 1


@override_settings(LOG_PANEL={"CONNECTION_STRING": "mongodb://localhost:27017"})
def test_mongodb_handler_raises_pymongo_not_installed_when_missing():
    handler = MongoDBHandler()

    with patch.dict(sys.modules, {"pymongo": None}):
        with pytest.raises(PyMongoNotInstalled):
            handler.get_collection()


@override_settings(LOG_PANEL={"CONNECTION_STRING": None})
def test_mongodb_handler_raises_improperly_configured_without_connection_string():
    handler = MongoDBHandler()

    mock_pymongo = MagicMock()
    mock_pymongo.ASCENDING = 1

    with patch.dict(
        sys.modules, {"pymongo": mock_pymongo, "pymongo.errors": MagicMock()}
    ):
        with pytest.raises(ImproperlyConfigured, match="CONNECTION_STRING"):
            handler.get_collection()


@override_settings(LOG_PANEL={"CONNECTION_STRING": ""})
def test_mongodb_handler_raises_improperly_configured_for_empty_connection_string():
    handler = MongoDBHandler()

    mock_pymongo = MagicMock()
    mock_pymongo.ASCENDING = 1

    with patch.dict(
        sys.modules, {"pymongo": mock_pymongo, "pymongo.errors": MagicMock()}
    ):
        with pytest.raises(ImproperlyConfigured, match="CONNECTION_STRING"):
            handler.get_collection()


@override_settings(LOG_PANEL={"CONNECTION_STRING": "mongodb://localhost:27017"})
def test_mongodb_handler_get_collection_creates_ttl_index():
    handler = MongoDBHandler()
    mock_pymongo = make_pymongo_mock()

    with patch.dict(
        sys.modules, {"pymongo": mock_pymongo, "pymongo.errors": mock_pymongo.errors}
    ):
        handler.get_collection()

    mock_collection = mock_pymongo.MongoClient.return_value["log_panel"]["logs"]
    calls = mock_collection.create_index.call_args_list
    ttl_calls = [c for c in calls if c[1].get("expireAfterSeconds") is not None]
    assert len(ttl_calls) == 1
    assert ttl_calls[0][1]["expireAfterSeconds"] == 90 * 24 * 3600


@override_settings(LOG_PANEL={"CONNECTION_STRING": "mongodb://bad-host:27017"})
def test_mongodb_handler_get_collection_raises_connection_error_after_retries():
    handler = MongoDBHandler()
    mock_pymongo = make_pymongo_mock()
    mock_client = mock_pymongo.MongoClient.return_value
    mock_client.admin.command.side_effect = (
        mock_pymongo.errors.ServerSelectionTimeoutError("timeout")
    )

    with patch.dict(
        sys.modules, {"pymongo": mock_pymongo, "pymongo.errors": mock_pymongo.errors}
    ):
        with patch("time.sleep") as mock_sleep:
            with pytest.raises(MongoDBConnectionError):
                handler.get_collection()

    assert mock_client.admin.command.call_count == 5
    assert mock_sleep.call_count == 4
    delays = [call.args[0] for call in mock_sleep.call_args_list]
    assert delays == [0.5, 1.0, 2.0, 4.0]


@override_settings(LOG_PANEL={"CONNECTION_STRING": "mongodb://localhost:27017"})
def test_mongodb_handler_get_collection_succeeds_on_third_retry():
    handler = MongoDBHandler()
    mock_pymongo = make_pymongo_mock()
    mock_client = mock_pymongo.MongoClient.return_value

    timeout_exc = mock_pymongo.errors.ServerSelectionTimeoutError("timeout")
    mock_client.admin.command.side_effect = [timeout_exc, timeout_exc, {"ok": 1.0}]

    with patch.dict(
        sys.modules, {"pymongo": mock_pymongo, "pymongo.errors": mock_pymongo.errors}
    ):
        with patch("time.sleep"):
            handler.get_collection()

    assert mock_client.admin.command.call_count == 3


@override_settings(LOG_PANEL={"CONNECTION_STRING": "mongodb://localhost:27017"})
def test_mongodb_handler_get_collection_reconnects_after_fork():
    handler = MongoDBHandler()
    mock_pymongo = make_pymongo_mock()
    first_client = MagicMock()
    second_client = MagicMock()
    mock_pymongo.MongoClient.side_effect = [first_client, second_client]

    with patch.dict(
        sys.modules, {"pymongo": mock_pymongo, "pymongo.errors": mock_pymongo.errors}
    ):
        with patch(
            "log_panel.handlers.mongodb.os.getpid", side_effect=[1000, 1000, 2001, 2001]
        ):
            handler.get_collection()
            handler.get_collection()
            handler.get_collection()

    assert mock_pymongo.MongoClient.call_count == 2
    first_client.close.assert_not_called()


def test_mongodb_handler_emit_inserts_document(log_record_factory):
    handler = MongoDBHandler()
    mock_collection = MagicMock()

    with patch.object(handler, "get_collection", return_value=mock_collection):
        handler.emit(log_record_factory())

    mock_collection.insert_one.assert_called_once()
    doc = mock_collection.insert_one.call_args[0][0]
    assert doc["level"] == "ERROR"
    assert doc["logger_name"] == "myapp"
    assert doc["module"] == "views"
    assert doc["timestamp"].tzinfo is not None


def test_mongodb_handler_emit_calls_handle_error_on_exception(log_record_factory):
    handler = MongoDBHandler()
    record = log_record_factory()

    with patch.object(handler, "get_collection", side_effect=RuntimeError("db down")):
        with patch.object(handler, "handleError") as mock_handle_error:
            handler.emit(record)
            mock_handle_error.assert_called_once_with(record)


def test_mongodb_connection_error_stores_attributes():
    reason = ConnectionRefusedError("refused")
    exc = MongoDBConnectionError("mongodb://host:27017", reason)

    assert exc.connection_string == "mongodb://host:27017"
    assert exc.reason is reason


def test_mongodb_connection_error_message_includes_connection_string():
    exc = MongoDBConnectionError("mongodb://host:27017", Exception("timeout"))
    assert "mongodb://host:27017" in str(exc)


@pytest.mark.parametrize("level", [logging.DEBUG, logging.INFO])
def test_mongodb_handler_emit_does_not_signal_for_non_alert_levels(
    log_record_factory,
    threshold_event_recorder,
    level,
):
    handler = MongoDBHandler()
    collection = MongoCollection()

    with patch.object(handler, "get_collection", return_value=collection):
        handler.emit(log_record_factory(level=level))

    assert threshold_event_recorder == []
    assert collection.count_queries == []


@override_settings(LOG_PANEL={"THRESHOLDS": {"WARNING": 3}})
def test_mongodb_handler_emit_does_not_signal_before_warning_threshold(
    log_record_factory,
    threshold_event_recorder,
):
    current_time = datetime(2024, 6, 15, 15, 0, tzinfo=UTC)
    handler = MongoDBHandler()
    collection = MongoCollection()
    collection.docs.append(
        {
            "timestamp": current_time - timedelta(minutes=10),
            "level": "WARNING",
            "logger_name": "myapp",
            "message": "existing warning",
            "module": "views",
            "pathname": "/app/views.py",
            "lineno": 12,
        }
    )

    with patch.object(handler, "get_collection", return_value=collection):
        handler.emit(
            set_record_created(
                log_record_factory(level=logging.WARNING),
                current_time,
            )
        )

    assert threshold_event_recorder == []


@override_settings(LOG_PANEL={"THRESHOLDS": {"WARNING": 2}})
def test_mongodb_handler_emit_signals_when_warning_threshold_is_reached(
    log_record_factory,
    threshold_event_recorder,
):
    current_time = datetime(2024, 6, 15, 15, 0, tzinfo=UTC)
    handler = MongoDBHandler()
    collection = MongoCollection()
    collection.docs.append(
        {
            "timestamp": current_time - timedelta(minutes=10),
            "level": "WARNING",
            "logger_name": "myapp",
            "message": "existing warning",
            "module": "views",
            "pathname": "/app/views.py",
            "lineno": 12,
        }
    )

    with patch.object(handler, "get_collection", return_value=collection):
        handler.emit(
            set_record_created(
                log_record_factory(level=logging.WARNING, msg="mongo warning"),
                current_time,
            )
        )

    assert len(threshold_event_recorder) == 1
    sender, event = threshold_event_recorder[0]
    assert sender is MongoDBHandler
    assert event.threshold_level == LogLevel.WARNING
    assert event.record_level == LogLevel.WARNING
    assert event.matching_count == 2
    assert event.message == "mongo warning"


@override_settings(LOG_PANEL={"THRESHOLDS": {"ERROR": 2}})
def test_mongodb_handler_emit_signals_when_error_threshold_is_reached(
    log_record_factory,
    threshold_event_recorder,
):
    current_time = datetime(2024, 6, 15, 15, 0, tzinfo=UTC)
    handler = MongoDBHandler()
    collection = MongoCollection()
    collection.docs.append(
        {
            "timestamp": current_time - timedelta(minutes=10),
            "level": "ERROR",
            "logger_name": "myapp",
            "message": "existing error",
            "module": "views",
            "pathname": "/app/views.py",
            "lineno": 12,
        }
    )

    with patch.object(handler, "get_collection", return_value=collection):
        handler.emit(
            set_record_created(
                log_record_factory(level=logging.ERROR, msg="mongo error"),
                current_time,
            )
        )

    assert len(threshold_event_recorder) == 1
    _, event = threshold_event_recorder[0]
    assert event.threshold_level == LogLevel.ERROR
    assert event.record_level == LogLevel.ERROR
    assert event.matching_count == 2


@override_settings(LOG_PANEL={"THRESHOLDS": {"CRITICAL": 2}})
def test_mongodb_handler_emit_critical_fires_its_own_threshold(
    log_record_factory,
    threshold_event_recorder,
):
    current_time = datetime(2024, 6, 15, 15, 0, tzinfo=UTC)
    handler = MongoDBHandler()
    collection = MongoCollection()
    collection.docs.append(
        {
            "timestamp": current_time - timedelta(minutes=10),
            "level": "CRITICAL",
            "logger_name": "myapp",
            "message": "existing critical",
            "module": "views",
            "pathname": "/app/views.py",
            "lineno": 12,
        }
    )

    with patch.object(handler, "get_collection", return_value=collection):
        handler.emit(
            set_record_created(
                log_record_factory(level=logging.CRITICAL, msg="mongo critical"),
                current_time,
            )
        )

    assert len(threshold_event_recorder) == 1
    _, event = threshold_event_recorder[0]
    assert event.threshold_level == LogLevel.CRITICAL
    assert event.record_level == LogLevel.CRITICAL
    assert event.matching_count == 2


@override_settings(LOG_PANEL={"THRESHOLDS": {"WARNING": 2}})
def test_mongodb_handler_emit_does_not_signal_once_threshold_is_already_exceeded(
    log_record_factory,
    threshold_event_recorder,
):
    current_time = datetime(2024, 6, 15, 15, 0, tzinfo=UTC)
    handler = MongoDBHandler()
    collection = MongoCollection()
    collection.docs.extend(
        [
            {
                "timestamp": current_time - timedelta(minutes=20),
                "level": "WARNING",
                "logger_name": "myapp",
                "message": "warning one",
                "module": "views",
                "pathname": "/app/views.py",
                "lineno": 12,
            },
            {
                "timestamp": current_time - timedelta(minutes=10),
                "level": "WARNING",
                "logger_name": "myapp",
                "message": "warning two",
                "module": "views",
                "pathname": "/app/views.py",
                "lineno": 14,
            },
        ]
    )

    with patch.object(handler, "get_collection", return_value=collection):
        handler.emit(
            set_record_created(log_record_factory(level=logging.WARNING), current_time)
        )

    assert threshold_event_recorder == []


@override_settings(LOG_PANEL={"THRESHOLDS": {"WARNING": 2}})
def test_mongodb_handler_emit_ignores_records_older_than_one_hour_for_threshold_count(
    log_record_factory,
    threshold_event_recorder,
):
    current_time = datetime(2024, 6, 15, 15, 0, tzinfo=UTC)
    handler = MongoDBHandler()
    collection = MongoCollection()
    collection.docs.append(
        {
            "timestamp": current_time - timedelta(hours=1, seconds=1),
            "level": "WARNING",
            "logger_name": "myapp",
            "message": "old warning",
            "module": "views",
            "pathname": "/app/views.py",
            "lineno": 12,
        }
    )

    with patch.object(handler, "get_collection", return_value=collection):
        handler.emit(
            set_record_created(log_record_factory(level=logging.WARNING), current_time)
        )

    assert threshold_event_recorder == []
    assert collection.count_queries[0]["timestamp"]["$gte"] == current_time - timedelta(
        hours=1
    )


def test_mongodb_handler_emit_does_not_signal_when_persistence_fails(
    log_record_factory,
    threshold_event_recorder,
):
    handler = MongoDBHandler()
    record = log_record_factory(level=logging.WARNING)
    collection = MagicMock()
    collection.insert_one.side_effect = RuntimeError("db down")

    with patch.object(handler, "get_collection", return_value=collection):
        with patch.object(handler, "handleError") as mock_handle_error:
            handler.emit(record)

    assert threshold_event_recorder == []
    mock_handle_error.assert_called_once_with(record)


@override_settings(LOG_PANEL={"THRESHOLDS": {"WARNING": 1}})
def test_mongodb_handler_emit_ignores_receiver_exceptions_for_threshold_signal(
    log_record_factory,
):
    handler = MongoDBHandler()
    collection = MongoCollection()
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
        with patch.object(handler, "get_collection", return_value=collection):
            handler.emit(record)
    finally:
        log_threshold_reached.disconnect(dispatch_uid=dispatch_uid)

    assert len(collection.docs) == 1


def test_mongodb_handler_emit_reentrant_call_is_silently_dropped(log_record_factory):
    handler = MongoDBHandler()
    collection = MongoCollection()
    call_count = 0

    def re_get_collection():
        nonlocal call_count
        call_count += 1
        handler.emit(log_record_factory(msg="nested log"))
        return collection

    with patch.object(handler, "get_collection", side_effect=re_get_collection):
        handler.emit(log_record_factory(msg="outer log"))

    assert call_count == 1
    assert len(collection.docs) == 1
    assert collection.docs[0]["message"] == "outer log"


@override_settings(LOG_PANEL={"CONNECTION_STRING": "mongodb://localhost:27017"})
def test_mongodb_handler_get_collection_caches_client():
    handler = MongoDBHandler()
    mock_pymongo = make_pymongo_mock()

    with patch.dict(
        sys.modules, {"pymongo": mock_pymongo, "pymongo.errors": mock_pymongo.errors}
    ):
        first = handler.get_collection()
        second = handler.get_collection()

    assert first is second
    mock_pymongo.MongoClient.assert_called_once()


@override_settings(LOG_PANEL={"CONNECTION_STRING": "mongodb://localhost:27017"})
def test_mongodb_handler_close_resets_cached_connection():
    handler = MongoDBHandler()
    mock_pymongo = make_pymongo_mock()

    with patch.dict(
        sys.modules, {"pymongo": mock_pymongo, "pymongo.errors": mock_pymongo.errors}
    ):
        handler.get_collection()
        handler.close()

        assert handler._client is None
        assert handler._collection is None

        handler.get_collection()

    assert mock_pymongo.MongoClient.call_count == 2


@override_settings(LOG_PANEL={"CONNECTION_STRING": "mongodb://localhost:27017"})
def test_mongodb_handler_indexes_created_once():
    handler = MongoDBHandler()
    mock_pymongo = make_pymongo_mock()

    with patch.dict(
        sys.modules, {"pymongo": mock_pymongo, "pymongo.errors": mock_pymongo.errors}
    ):
        handler.get_collection()
        handler.get_collection()

    mock_collection = mock_pymongo.MongoClient.return_value["log_panel"]["logs"]
    assert mock_collection.create_index.call_count == 3


@pytest.mark.django_db
def test_database_handler_emit_skips_reentrant_call(log_record_factory):
    handler = DatabaseHandler()
    handler._local.emitting = True
    try:
        handler.emit(log_record_factory())
    finally:
        handler._local.emitting = False
    assert Panel.objects.count() == 0


@pytest.mark.django_db
def test_database_handler_emit_silently_discards_programming_error(log_record_factory):
    from django.db import ProgrammingError

    handler = DatabaseHandler()
    record = log_record_factory()
    with patch("log_panel.models.Panel.objects") as mock_objects:
        mock_objects.create_from_record.side_effect = ProgrammingError("no table")
        with patch.object(handler, "handleError") as mock_handle_error:
            handler.emit(record)
    mock_handle_error.assert_not_called()
