from collections import defaultdict
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import log_panel.backends.mongodb as mongodb_mod
from log_panel.backends.base import LogsBackend
from log_panel.backends.mongodb import MongoDBBackend
from log_panel.exceptions.mongodb import MongoDBConnectionError
from log_panel.types import RangeConfig, RangeUnit, SlotStatus

BERLIN = ZoneInfo("Europe/Berlin")
NOW_UTC = datetime(2024, 6, 15, 14, 0, 0, tzinfo=UTC)
ONE_HOUR_AGO = NOW_UTC - timedelta(hours=1)

HOUR_RANGE = RangeConfig(
    delta=timedelta(hours=24),
    unit=RangeUnit.HOUR,
    slots=24,
    format="%H:00",
)
DAY_RANGE = RangeConfig(
    delta=timedelta(days=30),
    unit=RangeUnit.DAY,
    slots=30,
    format="%b %d",
)


@pytest.fixture
def backend():
    return MongoDBBackend(connection_string="mongodb://localhost:27017")


def test_build_slots_returns_correct_count():
    now_bucket_local = NOW_UTC.replace(minute=0, second=0, microsecond=0)
    utc_naive, labels, slots_local = MongoDBBackend._build_slots(
        now_bucket_local=now_bucket_local,
        slot_delta=timedelta(hours=1),
        range_config=HOUR_RANGE,
    )
    assert len(utc_naive) == 24
    assert len(labels) == 24
    assert len(slots_local) == 24


def test_build_slots_oldest_first():
    now_bucket_local = NOW_UTC.replace(minute=0, second=0, microsecond=0)
    utc_naive, _, _slots_local = MongoDBBackend._build_slots(
        now_bucket_local=now_bucket_local,
        slot_delta=timedelta(hours=1),
        range_config=HOUR_RANGE,
    )
    assert utc_naive[0] < utc_naive[-1]


def test_build_slots_last_slot_is_current_bucket():
    now_bucket_local = NOW_UTC.replace(minute=0, second=0, microsecond=0)
    utc_naive, _, _slots_local = MongoDBBackend._build_slots(
        now_bucket_local=now_bucket_local,
        slot_delta=timedelta(hours=1),
        range_config=HOUR_RANGE,
    )
    expected_last = now_bucket_local.astimezone(UTC).replace(tzinfo=None)
    assert utc_naive[-1] == expected_last


def test_build_slots_naive_utc():
    now_bucket_local = NOW_UTC.replace(minute=0, second=0, microsecond=0)
    utc_naive, _, _slots_local = MongoDBBackend._build_slots(
        now_bucket_local=now_bucket_local,
        slot_delta=timedelta(hours=1),
        range_config=HOUR_RANGE,
    )
    for slot in utc_naive:
        assert slot.tzinfo is None


def test_build_slots_labels_match_format():
    now_bucket_local = NOW_UTC.replace(minute=0, second=0, microsecond=0)
    _, labels, _slots_local = MongoDBBackend._build_slots(
        now_bucket_local=now_bucket_local,
        slot_delta=timedelta(hours=1),
        range_config=HOUR_RANGE,
    )
    for label in labels:
        assert label.endswith(":00"), f"Unexpected label: {label}"


def test_build_slots_day_unit_uses_midnight_boundaries():
    now_bucket_local = NOW_UTC.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_naive, labels, _slots_local = MongoDBBackend._build_slots(
        now_bucket_local=now_bucket_local,
        slot_delta=timedelta(days=1),
        range_config=DAY_RANGE,
    )
    assert len(utc_naive) == 30
    for slot in utc_naive:
        assert slot.hour == 0
        assert slot.minute == 0


def test_build_cards_pipeline_has_two_stages():
    pipeline = MongoDBBackend._build_cards_pipeline(ONE_HOUR_AGO)
    assert len(pipeline) == 2


def test_build_cards_pipeline_first_stage_is_group():
    pipeline = MongoDBBackend._build_cards_pipeline(ONE_HOUR_AGO)
    assert "$group" in pipeline[0]


def test_build_cards_pipeline_second_stage_sorts_by_last_seen_desc():
    pipeline = MongoDBBackend._build_cards_pipeline(ONE_HOUR_AGO)
    assert pipeline[1] == {"$sort": {"last_seen": -1}}


def test_build_cards_pipeline_group_includes_required_fields():
    pipeline = MongoDBBackend._build_cards_pipeline(ONE_HOUR_AGO)
    group = pipeline[0]["$group"]
    for field in (
        "total",
        "total_errors",
        "total_warnings",
        "recent_errors",
        "recent_warnings",
        "last_seen",
    ):
        assert field in group, f"Missing field: {field}"


def test_build_cards_pipeline_recent_cutoff_is_embedded():
    pipeline = MongoDBBackend._build_cards_pipeline(ONE_HOUR_AGO)
    recent_errors_cond = pipeline[0]["$group"]["recent_errors"]["$sum"]["$cond"]
    and_clauses = recent_errors_cond[0]["$and"]
    gte_clause = next(c for c in and_clauses if "$gte" in c)
    assert gte_clause["$gte"][1] == ONE_HOUR_AGO


def test_build_timeline_pipeline_has_two_stages():
    cutoff = NOW_UTC - timedelta(hours=24)
    pipeline = MongoDBBackend._build_timeline_pipeline(
        cutoff=cutoff, unit_value="hour", app_timezone_name="UTC"
    )
    assert len(pipeline) == 2


def test_build_timeline_pipeline_first_stage_matches_on_timestamp():
    cutoff = NOW_UTC - timedelta(hours=24)
    pipeline = MongoDBBackend._build_timeline_pipeline(
        cutoff=cutoff, unit_value="hour", app_timezone_name="UTC"
    )
    assert "$match" in pipeline[0]
    assert pipeline[0]["$match"]["timestamp"]["$gte"] == cutoff


def test_build_timeline_pipeline_uses_unit_value():
    cutoff = NOW_UTC - timedelta(days=30)
    pipeline = MongoDBBackend._build_timeline_pipeline(
        cutoff=cutoff, unit_value="day", app_timezone_name="UTC"
    )
    group = pipeline[1]["$group"]
    assert group["_id"]["bucket"]["$dateTrunc"]["unit"] == "day"


def test_build_timeline_pipeline_uses_timezone_name():
    cutoff = NOW_UTC - timedelta(hours=24)
    pipeline = MongoDBBackend._build_timeline_pipeline(
        cutoff=cutoff, unit_value="hour", app_timezone_name="Europe/Berlin"
    )
    group = pipeline[1]["$group"]
    assert group["_id"]["bucket"]["$dateTrunc"]["timezone"] == "Europe/Berlin"


def test_build_timeline_pipeline_group_includes_has_error_and_has_warning():
    cutoff = NOW_UTC - timedelta(hours=24)
    pipeline = MongoDBBackend._build_timeline_pipeline(
        cutoff=cutoff, unit_value="hour", app_timezone_name="UTC"
    )
    group = pipeline[1]["$group"]
    assert "has_error" in group
    assert "has_warning" in group


def _make_collection_mock(entries: list[dict]) -> MagicMock:
    mock = MagicMock()
    mock.aggregate.return_value = iter(entries)
    return mock


def test_aggregate_timeline_error_status():
    bucket = datetime(2024, 6, 15, 14, 0, 0)
    collection = _make_collection_mock(
        [
            {
                "_id": {"logger": "myapp", "bucket": bucket},
                "has_error": 1,
                "has_warning": 0,
            }
        ]
    )
    result = MongoDBBackend._aggregate_timeline(collection, pipeline=[])
    assert result["myapp"][bucket] == SlotStatus.ERROR


def test_aggregate_timeline_warning_status():
    bucket = datetime(2024, 6, 15, 14, 0, 0)
    collection = _make_collection_mock(
        [
            {
                "_id": {"logger": "myapp", "bucket": bucket},
                "has_error": 0,
                "has_warning": 1,
            }
        ]
    )
    result = MongoDBBackend._aggregate_timeline(collection, pipeline=[])
    assert result["myapp"][bucket] == SlotStatus.WARNING


def test_aggregate_timeline_ok_status():
    bucket = datetime(2024, 6, 15, 14, 0, 0)
    collection = _make_collection_mock(
        [
            {
                "_id": {"logger": "myapp", "bucket": bucket},
                "has_error": 0,
                "has_warning": 0,
            }
        ]
    )
    result = MongoDBBackend._aggregate_timeline(collection, pipeline=[])
    assert result["myapp"][bucket] == SlotStatus.OK


def test_aggregate_timeline_strips_timezone_from_bucket():
    bucket_aware = datetime(2024, 6, 15, 14, 0, 0, tzinfo=UTC)
    bucket_naive = bucket_aware.replace(tzinfo=None)
    collection = _make_collection_mock(
        [
            {
                "_id": {"logger": "myapp", "bucket": bucket_aware},
                "has_error": 1,
                "has_warning": 0,
            }
        ]
    )
    result = MongoDBBackend._aggregate_timeline(collection, pipeline=[])
    assert bucket_naive in result["myapp"]
    assert bucket_aware not in result["myapp"]


def test_aggregate_timeline_groups_multiple_loggers():
    bucket = datetime(2024, 6, 15, 14, 0, 0)
    collection = _make_collection_mock(
        [
            {
                "_id": {"logger": "app_a", "bucket": bucket},
                "has_error": 1,
                "has_warning": 0,
            },
            {
                "_id": {"logger": "app_b", "bucket": bucket},
                "has_error": 0,
                "has_warning": 1,
            },
        ]
    )
    result = MongoDBBackend._aggregate_timeline(collection, pipeline=[])
    assert result["app_a"][bucket] == SlotStatus.ERROR
    assert result["app_b"][bucket] == SlotStatus.WARNING


def _make_card_doc(
    logger_name="myapp",
    total=5,
    errors=2,
    warnings=1,
    recent_errors=1,
    recent_warnings=0,
):
    return {
        "_id": logger_name,
        "total": total,
        "total_errors": errors,
        "total_warnings": warnings,
        "recent_errors": recent_errors,
        "recent_warnings": recent_warnings,
        "last_seen": NOW_UTC,
    }


def test_assemble_rows_empty_cursor_returns_empty_list():
    rows = MongoDBBackend._assemble_rows(
        cards_cursor=[],
        timeline_by_logger=defaultdict(dict),
        slot_labels=["14:00"],
        slots_utc_naive=[datetime(2024, 6, 15, 14, 0, 0)],
        slots_count=1,
        slots_local=[datetime(2024, 6, 15, 14, 0, 0)],
        slot_delta=timedelta(hours=1),
    )
    assert rows == []


def test_assemble_rows_row_structure():
    rows = MongoDBBackend._assemble_rows(
        cards_cursor=[_make_card_doc()],
        timeline_by_logger=defaultdict(dict),
        slot_labels=["14:00"],
        slots_utc_naive=[datetime(2024, 6, 15, 14, 0, 0)],
        slots_count=1,
        slots_local=[datetime(2024, 6, 15, 14, 0, 0)],
        slot_delta=timedelta(hours=1),
    )
    row = rows[0]
    for key in (
        "logger_name",
        "total",
        "total_errors",
        "total_warnings",
        "recent_errors",
        "recent_warnings",
        "last_seen",
        "timeline",
    ):
        assert key in row, f"Missing key: {key}"


def test_assemble_rows_slot_status_empty_when_bucket_not_in_timeline():
    rows = MongoDBBackend._assemble_rows(
        cards_cursor=[_make_card_doc()],
        timeline_by_logger=defaultdict(dict),
        slot_labels=["14:00"],
        slots_utc_naive=[datetime(2024, 6, 15, 14, 0, 0)],
        slots_count=1,
        slots_local=[datetime(2024, 6, 15, 14, 0, 0)],
        slot_delta=timedelta(hours=1),
    )
    assert rows[0]["timeline"][0]["status"] == SlotStatus.EMPTY


def test_assemble_rows_slot_status_from_timeline():
    bucket = datetime(2024, 6, 15, 14, 0, 0)
    timeline: defaultdict = defaultdict(dict, {"myapp": {bucket: SlotStatus.ERROR}})
    rows = MongoDBBackend._assemble_rows(
        cards_cursor=[_make_card_doc("myapp")],
        timeline_by_logger=timeline,
        slot_labels=["14:00"],
        slots_utc_naive=[bucket],
        slots_count=1,
        slots_local=[bucket],
        slot_delta=timedelta(hours=1),
    )
    assert rows[0]["timeline"][0]["status"] == SlotStatus.ERROR


def test_assemble_rows_slot_labels_are_preserved():
    rows = MongoDBBackend._assemble_rows(
        cards_cursor=[_make_card_doc()],
        timeline_by_logger=defaultdict(dict),
        slot_labels=["13:00", "14:00"],
        slots_utc_naive=[
            datetime(2024, 6, 15, 13, 0, 0),
            datetime(2024, 6, 15, 14, 0, 0),
        ],
        slots_count=2,
        slots_local=[datetime(2024, 6, 15, 13, 0, 0), datetime(2024, 6, 15, 14, 0, 0)],
        slot_delta=timedelta(hours=1),
    )
    assert rows[0]["timeline"][0]["label"] == "13:00"
    assert rows[0]["timeline"][1]["label"] == "14:00"


def test_assemble_rows_maps_card_fields():
    rows = MongoDBBackend._assemble_rows(
        cards_cursor=[
            _make_card_doc(
                "billing",
                total=10,
                errors=3,
                warnings=2,
                recent_errors=1,
                recent_warnings=0,
            )
        ],
        timeline_by_logger=defaultdict(dict),
        slot_labels=["14:00"],
        slots_utc_naive=[datetime(2024, 6, 15, 14, 0, 0)],
        slots_count=1,
        slots_local=[datetime(2024, 6, 15, 14, 0, 0)],
        slot_delta=timedelta(hours=1),
    )
    row = rows[0]
    assert row["logger_name"] == "billing"
    assert row["total"] == 10
    assert row["total_errors"] == 3
    assert row["total_warnings"] == 2
    assert row["recent_errors"] == 1
    assert row["recent_warnings"] == 0


def test_get_log_table_builds_query_with_level_filter(backend):
    mock_collection = MagicMock()
    mock_collection.count_documents.return_value = 0
    mock_collection.find.return_value.sort.return_value.skip.return_value.limit.return_value = []

    with patch.object(backend, "get_collection", return_value=mock_collection):
        backend.get_log_table(
            logger_name="myapp",
            level="ERROR",
            search="",
            page=1,
            page_size=10,
            app_timezone=UTC,
        )

    query = mock_collection.count_documents.call_args[0][0]
    assert query["level"] == "ERROR"


def test_get_log_table_builds_query_with_regex_search(backend):
    mock_collection = MagicMock()
    mock_collection.count_documents.return_value = 0
    mock_collection.find.return_value.sort.return_value.skip.return_value.limit.return_value = []

    with patch.object(backend, "get_collection", return_value=mock_collection):
        backend.get_log_table(
            logger_name="myapp",
            level="",
            search="database",
            page=1,
            page_size=10,
            app_timezone=UTC,
        )

    query = mock_collection.count_documents.call_args[0][0]
    assert query["message"] == {"$regex": "database", "$options": "i"}


def test_get_log_table_omits_level_filter_when_empty(backend):
    mock_collection = MagicMock()
    mock_collection.count_documents.return_value = 0
    mock_collection.find.return_value.sort.return_value.skip.return_value.limit.return_value = []

    with patch.object(backend, "get_collection", return_value=mock_collection):
        backend.get_log_table(
            logger_name="myapp",
            level="",
            search="",
            page=1,
            page_size=10,
            app_timezone=UTC,
        )

    query = mock_collection.count_documents.call_args[0][0]
    assert "level" not in query
    assert "message" not in query


def test_get_log_table_converts_timestamp_to_app_timezone(backend):
    ts_naive_utc = datetime(2024, 6, 15, 12, 0, 0)
    mock_doc = {
        "_id": "abc123",
        "timestamp": ts_naive_utc,
        "level": "INFO",
        "logger_name": "myapp",
        "message": "hello",
        "module": "views",
        "pathname": "/app/views.py",
        "line_number": 1,
    }
    mock_collection = MagicMock()
    mock_collection.count_documents.return_value = 1
    mock_collection.find.return_value.sort.return_value.skip.return_value.limit.return_value = [
        mock_doc
    ]

    with patch.object(backend, "get_collection", return_value=mock_collection):
        logs, _ = backend.get_log_table(
            logger_name="myapp",
            level="",
            search="",
            page=1,
            page_size=10,
            app_timezone=BERLIN,
        )

    result_ts = logs[0]["timestamp"]
    assert result_ts.tzinfo == BERLIN
    assert result_ts.hour == 14


def test_get_log_table_paginates_via_skip_and_limit(backend):
    mock_collection = MagicMock()
    mock_collection.count_documents.return_value = 10
    mock_collection.find.return_value.sort.return_value.skip.return_value.limit.return_value = []

    with patch.object(backend, "get_collection", return_value=mock_collection):
        backend.get_log_table(
            logger_name="myapp",
            level="",
            search="",
            page=3,
            page_size=5,
            app_timezone=UTC,
        )

    chain = mock_collection.find.return_value.sort.return_value
    chain.skip.assert_called_once_with(10)
    chain.skip.return_value.limit.assert_called_once_with(5)


def test_get_local_now_and_slot_delta_day_unit():
    bucket, delta = LogsBackend.get_local_now_and_slot_delta(
        now_utc=NOW_UTC,
        app_timezone=UTC,
        configured_unit=RangeUnit.DAY,
    )
    assert delta == timedelta(days=1)
    assert bucket.hour == 0
    assert bucket.minute == 0


def test_get_local_now_and_slot_delta_invalid_unit_raises():
    with pytest.raises(ValueError, match="Unsupported range unit"):
        LogsBackend.get_local_now_and_slot_delta(
            now_utc=NOW_UTC,
            app_timezone=UTC,
            configured_unit="invalid",  # ty: ignore[invalid-argument-type]
        )


def _make_pymongo_mock():
    mock_pymongo = MagicMock()
    mock_pymongo.ASCENDING = 1
    mock_pymongo.errors.ServerSelectionTimeoutError = type(
        "ServerSelectionTimeoutError", (Exception,), {}
    )
    return mock_pymongo


def test_mongodb_backend_get_collection_returns_collection():
    backend = MongoDBBackend(
        connection_string="mongodb://localhost:27017",
        db_name="mydb",
        collection="mylogs",
    )
    mock_client = MagicMock()

    with patch("log_panel.backends.mongodb.MongoClient", return_value=mock_client):
        collection = backend.get_collection()

    assert collection is mock_client["mydb"]["mylogs"]


def test_mongodb_backend_get_collection_raises_after_retries():
    backend = MongoDBBackend(connection_string="mongodb://bad-host:27017")
    mock_client = MagicMock()
    mock_client.admin.command.side_effect = mongodb_mod.ServerSelectionTimeoutError(
        "timeout"
    )

    with patch("log_panel.backends.mongodb.MongoClient", return_value=mock_client):
        with patch("time.sleep") as mock_sleep:
            with pytest.raises(MongoDBConnectionError):
                backend.get_collection()

    assert mock_client.admin.command.call_count == 5
    assert mock_sleep.call_count == 4
    delays = [call.args[0] for call in mock_sleep.call_args_list]
    assert delays == [0.5, 1.0, 2.0, 4.0]


def test_mongodb_backend_get_collection_succeeds_on_retry():
    backend = MongoDBBackend(connection_string="mongodb://localhost:27017")
    mock_client = MagicMock()

    timeout_exc = mongodb_mod.ServerSelectionTimeoutError("timeout")
    mock_client.admin.command.side_effect = [timeout_exc, timeout_exc, {"ok": 1.0}]

    with patch("log_panel.backends.mongodb.MongoClient", return_value=mock_client):
        with patch("time.sleep"):
            collection = backend.get_collection()

    assert mock_client.admin.command.call_count == 3
    assert collection is mock_client["log_panel"]["logs"]


def test_get_logger_cards_returns_assembled_rows():
    backend = MongoDBBackend(connection_string="mongodb://localhost:27017")
    mock_collection = MagicMock()

    bucket = datetime(2024, 6, 15, 14, 0, 0)
    mock_collection.aggregate.side_effect = [
        iter(
            [
                {
                    "_id": {"logger": "myapp", "bucket": bucket},
                    "has_error": 1,
                    "has_warning": 0,
                }
            ]
        ),
        iter(
            [
                {
                    "_id": "myapp",
                    "total": 3,
                    "total_errors": 1,
                    "total_warnings": 0,
                    "recent_errors": 1,
                    "recent_warnings": 0,
                    "last_seen": NOW_UTC,
                }
            ]
        ),
    ]

    with patch.object(backend, "get_collection", return_value=mock_collection):
        rows = backend.get_logger_cards(
            now_utc=NOW_UTC,
            range_config=HOUR_RANGE,
            app_timezone=UTC,
        )

    assert len(rows) == 1
    row = rows[0]
    assert row["logger_name"] == "myapp"
    assert row["total"] == 3
    assert len(row["timeline"]) == 24


def test_get_logger_cards_empty_collection_returns_empty_list():
    backend = MongoDBBackend(connection_string="mongodb://localhost:27017")
    mock_collection = MagicMock()
    mock_collection.aggregate.side_effect = [iter([]), iter([])]

    with patch.object(backend, "get_collection", return_value=mock_collection):
        rows = backend.get_logger_cards(
            now_utc=NOW_UTC,
            range_config=HOUR_RANGE,
            app_timezone=UTC,
        )

    assert rows == []


def test_aggregate_timeline_stays_ok_when_error_count_below_threshold():
    bucket = datetime(2024, 6, 15, 14, 0, 0)
    collection = _make_collection_mock(
        [
            {
                "_id": {"logger": "myapp", "bucket": bucket},
                "has_error": 2,
                "has_warning": 0,
            }
        ]
    )
    result = MongoDBBackend._aggregate_timeline(
        collection, pipeline=[], error_threshold=3
    )
    assert result["myapp"][bucket] == SlotStatus.OK


def test_aggregate_timeline_is_error_when_error_count_meets_threshold():
    bucket = datetime(2024, 6, 15, 14, 0, 0)
    collection = _make_collection_mock(
        [
            {
                "_id": {"logger": "myapp", "bucket": bucket},
                "has_error": 3,
                "has_warning": 0,
            }
        ]
    )
    result = MongoDBBackend._aggregate_timeline(
        collection, pipeline=[], error_threshold=3
    )
    assert result["myapp"][bucket] == SlotStatus.ERROR


def test_aggregate_timeline_stays_ok_when_warning_count_below_threshold():
    bucket = datetime(2024, 6, 15, 14, 0, 0)
    collection = _make_collection_mock(
        [
            {
                "_id": {"logger": "myapp", "bucket": bucket},
                "has_error": 0,
                "has_warning": 2,
            }
        ]
    )
    result = MongoDBBackend._aggregate_timeline(
        collection, pipeline=[], warning_threshold=3
    )
    assert result["myapp"][bucket] == SlotStatus.OK


def test_aggregate_timeline_is_warning_when_warning_count_meets_threshold():
    bucket = datetime(2024, 6, 15, 14, 0, 0)
    collection = _make_collection_mock(
        [
            {
                "_id": {"logger": "myapp", "bucket": bucket},
                "has_error": 0,
                "has_warning": 3,
            }
        ]
    )
    result = MongoDBBackend._aggregate_timeline(
        collection, pipeline=[], warning_threshold=3
    )
    assert result["myapp"][bucket] == SlotStatus.WARNING


def test_assemble_rows_slot_has_timestamp_from_and_to():
    bucket = datetime(2024, 6, 15, 14, 0, 0)
    rows = MongoDBBackend._assemble_rows(
        cards_cursor=[_make_card_doc()],
        timeline_by_logger=defaultdict(dict),
        slot_labels=["14:00"],
        slots_utc_naive=[bucket],
        slots_count=1,
        slots_local=[bucket],
        slot_delta=timedelta(hours=1),
    )
    slot = rows[0]["timeline"][0]
    assert slot["timestamp_from"] == "2024-06-15T14:00"
    assert slot["timestamp_to"] == "2024-06-15T15:00"


def test_get_log_table_passes_timestamp_filter_to_query(backend):
    mock_collection = MagicMock()
    mock_collection.count_documents.return_value = 0
    mock_collection.find.return_value.sort.return_value.skip.return_value.limit.return_value = []

    ts_from = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    ts_to = datetime(2024, 6, 15, 14, 0, 0, tzinfo=UTC)

    with patch.object(backend, "get_collection", return_value=mock_collection):
        backend.get_log_table(
            logger_name="myapp",
            level="",
            search="",
            page=1,
            page_size=10,
            app_timezone=UTC,
            timestamp_from=ts_from,
            timestamp_to=ts_to,
        )

    query = mock_collection.count_documents.call_args[0][0]
    assert "$gte" in query["timestamp"]
    assert "$lt" in query["timestamp"]
    assert query["timestamp"]["$gte"] == datetime(2024, 6, 15, 12, 0, 0)
    assert query["timestamp"]["$lt"] == datetime(2024, 6, 15, 14, 0, 0)


def test_get_collection_caches_client(backend):
    mock_client = MagicMock()

    with patch(
        "log_panel.backends.mongodb.MongoClient", return_value=mock_client
    ) as mock_cls:
        first = backend.get_collection()
        second = backend.get_collection()

    assert first is second
    mock_cls.assert_called_once()


def test_get_collection_reconnects_after_fork(backend):
    first_client = MagicMock()
    second_client = MagicMock()

    with patch(
        "log_panel.backends.mongodb.MongoClient",
        side_effect=[first_client, second_client],
    ):
        with patch(
            "log_panel.backends.mongodb.os.getpid", side_effect=[1000, 1000, 2001, 2001]
        ):
            backend.get_collection()
            backend.get_collection()
            backend.get_collection()

    assert backend._pid == 2001
    first_client.close.assert_not_called()


def test_build_log_query_empty_with_no_filters(backend):
    assert backend._build_log_query(None, None, "", None, None) == {}


def test_build_log_query_adds_logger_names_filter(backend):
    query = backend._build_log_query(["orders", "machines"], None, "", None, None)
    assert query["logger_name"] == {"$in": ["orders", "machines"]}


def test_build_log_query_adds_levels_filter(backend):
    query = backend._build_log_query(None, ["WARNING", "ERROR"], "", None, None)
    assert query["level"] == {"$in": ["WARNING", "ERROR"]}


def test_build_log_query_adds_search_filter(backend):
    query = backend._build_log_query(None, None, "timeout", None, None)
    assert query["message"] == {"$regex": "timeout", "$options": "i"}


def test_build_log_query_adds_timestamp_from(backend):
    ts = datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)
    query = backend._build_log_query(None, None, "", ts, None)
    assert "$gte" in query["timestamp"]
    assert "$lt" not in query["timestamp"]


def test_build_log_query_adds_timestamp_to(backend):
    ts = datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)
    query = backend._build_log_query(None, None, "", None, ts)
    assert "$lt" in query["timestamp"]
    assert "$gte" not in query["timestamp"]


def test_build_log_query_adds_both_timestamp_bounds(backend):
    ts_from = datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)
    ts_to = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    query = backend._build_log_query(None, None, "", ts_from, ts_to)
    assert "$gte" in query["timestamp"]
    assert "$lt" in query["timestamp"]


def test_query_logs_calls_find_with_correct_query(backend):
    mock_collection = MagicMock()
    mock_collection.find.return_value.sort.return_value.skip.return_value = []

    with patch.object(backend, "get_collection", return_value=mock_collection):
        backend.query_logs(["orders"], None, "", 0, None, UTC)

    called_query = mock_collection.find.call_args[0][0]
    assert called_query["logger_name"] == {"$in": ["orders"]}


def test_query_logs_applies_offset(backend):
    mock_collection = MagicMock()
    mock_collection.find.return_value.sort.return_value.skip.return_value = []

    with patch.object(backend, "get_collection", return_value=mock_collection):
        backend.query_logs(None, None, "", 5, None, UTC)

    mock_collection.find.return_value.sort.return_value.skip.assert_called_once_with(5)


def test_query_logs_applies_limit_when_set(backend):
    mock_collection = MagicMock()
    mock_collection.find.return_value.sort.return_value.skip.return_value.limit.return_value = []

    with patch.object(backend, "get_collection", return_value=mock_collection):
        backend.query_logs(None, None, "", 0, 10, UTC)

    mock_collection.find.return_value.sort.return_value.skip.return_value.limit.assert_called_once_with(
        10
    )


def test_query_logs_skips_limit_when_none(backend):
    mock_collection = MagicMock()
    skip_result = MagicMock()
    skip_result.__iter__ = MagicMock(return_value=iter([]))
    mock_collection.find.return_value.sort.return_value.skip.return_value = skip_result

    with patch.object(backend, "get_collection", return_value=mock_collection):
        backend.query_logs(None, None, "", 0, None, UTC)

    skip_result.limit.assert_not_called()


def test_count_logs_calls_count_documents_with_empty_query(backend):
    mock_collection = MagicMock()
    mock_collection.count_documents.return_value = 0

    with patch.object(backend, "get_collection", return_value=mock_collection):
        result = backend.count_logs(None, None, "")

    mock_collection.count_documents.assert_called_once_with({})
    assert result == 0


def test_count_logs_passes_filters_to_query(backend):
    mock_collection = MagicMock()
    mock_collection.count_documents.return_value = 3

    with patch.object(backend, "get_collection", return_value=mock_collection):
        result = backend.count_logs(["orders"], ["WARNING", "ERROR"], "")

    called_query = mock_collection.count_documents.call_args[0][0]
    assert called_query["logger_name"] == {"$in": ["orders"]}
    assert called_query["level"] == {"$in": ["WARNING", "ERROR"]}
    assert result == 3
