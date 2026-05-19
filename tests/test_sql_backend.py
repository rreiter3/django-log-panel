from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.test import override_settings

from log_panel.backends.sql import OrmBackend
from log_panel.models import Log
from log_panel.types import RangeConfig, RangeUnit

BUDAPEST = ZoneInfo("Europe/Budapest")
NOW_UTC = datetime(2024, 6, 15, 14, 0, 0, tzinfo=UTC)
HOUR_RANGE = RangeConfig(
    delta=timedelta(hours=24),
    unit=RangeUnit.HOUR,
    slots=24,
    format="%H:00",
    label="Last 24 hours",
)
DAY_RANGE = RangeConfig(
    delta=timedelta(days=7),
    unit=RangeUnit.DAY,
    slots=7,
    format="%m-%d",
    label="Last 7 days",
)


@pytest.fixture
def backend():
    return OrmBackend()


@pytest.mark.django_db
@override_settings(
    LOG_PANEL={
        "MESSAGE_PREVIEW_LENGTH": 10,
        "MESSAGE_CHUNK_SIZE": 12,
    }
)
def test_query_logs_returns_full_chunked_message(backend):
    message = "preview text with full payload"
    Log.objects.create_from_record(
        timestamp=NOW_UTC,
        level="INFO",
        logger_name="myapp",
        message=message,
        module="views",
        pathname="/app/views.py",
        line_number=42,
    )

    logs = backend.query_logs(
        logger_names=["myapp"],
        levels=None,
        search="full",
        offset=0,
        limit=10,
        app_timezone=UTC,
    )

    assert logs[0]["message"] == message
    assert logs[0]["message_preview"] == "preview te"
    assert logs[0]["message_chunked"] is True


@pytest.mark.django_db
def test_get_logger_cards_returns_one_row_per_logger(panel_factory, backend):
    panel_factory(logger_name="app_a")
    panel_factory(logger_name="app_b")

    rows, total = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC, page_size=100
    )
    names = {r["logger_name"] for r in rows}
    assert names == {"app_a", "app_b"}
    assert total == 2


@pytest.mark.django_db
def test_get_logger_cards_row_contains_required_keys(panel_factory, backend):
    panel_factory()

    rows, _ = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
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


@pytest.mark.django_db
def test_get_logger_cards_timeline_has_correct_slot_count(panel_factory, backend):
    panel_factory()

    rows, _ = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    assert len(rows[0]["timeline"]) == 24


@pytest.mark.django_db
def test_get_logger_cards_slot_status_is_error_when_error_in_bucket(
    panel_factory, backend
):
    panel_factory(
        level="ERROR",
        timestamp=datetime(2024, 6, 15, 14, 5, tzinfo=UTC),
    )
    rows, _ = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    last_slot = rows[0]["timeline"][-1]
    assert last_slot["status"] == "error"


@pytest.mark.django_db
def test_get_logger_cards_slot_status_is_warning_when_only_warning_in_bucket(
    panel_factory, backend
):
    panel_factory(
        level="WARNING",
        timestamp=datetime(2024, 6, 15, 14, 5, tzinfo=UTC),
    )
    rows, _ = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    last_slot = rows[0]["timeline"][-1]
    assert last_slot["status"] == "warning"


@pytest.mark.django_db
def test_get_logger_cards_slot_status_is_ok_when_only_info_in_bucket(
    panel_factory, backend
):
    panel_factory(
        level="INFO",
        timestamp=datetime(2024, 6, 15, 14, 5, tzinfo=UTC),
    )
    rows, _ = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    last_slot = rows[0]["timeline"][-1]
    assert last_slot["status"] == "ok"


@pytest.mark.django_db
def test_get_logger_cards_slot_status_is_empty_for_empty_bucket(panel_factory, backend):
    panel_factory(
        level="INFO",
        timestamp=datetime(2024, 6, 15, 14, 5, tzinfo=UTC),
    )
    rows, _ = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    first_slot = rows[0]["timeline"][0]
    assert first_slot["status"] == "empty"


@pytest.mark.django_db
def test_get_logger_cards_error_takes_priority_over_warning_in_same_bucket(
    panel_factory, backend
):
    ts = datetime(2024, 6, 15, 14, 5, tzinfo=UTC)
    panel_factory(level="ERROR", timestamp=ts)
    panel_factory(level="WARNING", timestamp=ts)

    rows, _ = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    last_slot = rows[0]["timeline"][-1]
    assert last_slot["status"] == "error"


@pytest.mark.django_db
def test_get_logger_cards_slot_labels_match_format(panel_factory, backend):
    panel_factory(timestamp=datetime(2024, 6, 15, 14, 5, tzinfo=UTC))

    rows, _ = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    for slot in rows[0]["timeline"]:
        assert slot["label"].endswith(":00"), f"Unexpected label: {slot['label']}"


@pytest.mark.django_db
def test_get_logger_cards_empty_db_returns_empty_list(backend):
    rows, total = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    assert rows == []
    assert total == 0


@pytest.mark.django_db
@override_settings(USE_TZ=False, TIME_ZONE="Europe/Budapest")
def test_get_logger_cards_uses_naive_database_filter_datetimes(backend):
    captured = {}

    class QuerySetStub:
        def filter(self, **kwargs):
            if "logger_name__in" in kwargs:
                return self
            return self

        def recent_aggregation(self, *, one_hour_ago):
            captured["one_hour_ago"] = one_hour_ago
            return []

        def timeline_aggregation(self, *, cutoff, range_config, app_timezone):
            captured["cutoff"] = cutoff
            return []

    with patch.object(backend, "get_queryset", return_value=QuerySetStub()):
        rows, total = backend.get_logger_cards(
            now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=BUDAPEST
        )

    assert rows == []


@pytest.mark.django_db
@override_settings(LOG_PANEL={"DATABASE_ALIAS": "default", "THRESHOLDS": {"ERROR": 3}})
def test_slot_stays_ok_when_error_count_below_error_threshold(panel_factory, backend):
    ts = datetime(2024, 6, 15, 14, 5, tzinfo=UTC)
    panel_factory(level="ERROR", timestamp=ts)
    panel_factory(level="ERROR", timestamp=ts)

    rows, _ = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    assert rows[0]["timeline"][-1]["status"] == "ok"


@pytest.mark.django_db
@override_settings(LOG_PANEL={"DATABASE_ALIAS": "default", "THRESHOLDS": {"ERROR": 3}})
def test_slot_is_error_when_error_count_meets_error_threshold(panel_factory, backend):
    ts = datetime(2024, 6, 15, 14, 5, tzinfo=UTC)
    for _ in range(3):
        panel_factory(level="ERROR", timestamp=ts)

    rows, _ = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    assert rows[0]["timeline"][-1]["status"] == "error"


@pytest.mark.django_db
@override_settings(
    LOG_PANEL={"DATABASE_ALIAS": "default", "THRESHOLDS": {"WARNING": 3}}
)
def test_slot_stays_ok_when_warning_count_below_warning_threshold(
    panel_factory, backend
):
    ts = datetime(2024, 6, 15, 14, 5, tzinfo=UTC)
    panel_factory(level="WARNING", timestamp=ts)
    panel_factory(level="WARNING", timestamp=ts)

    rows, _ = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    assert rows[0]["timeline"][-1]["status"] == "ok"


@pytest.mark.django_db
@override_settings(
    LOG_PANEL={"DATABASE_ALIAS": "default", "THRESHOLDS": {"WARNING": 3}}
)
def test_slot_is_warning_when_warning_count_meets_warning_threshold(
    panel_factory, backend
):
    ts = datetime(2024, 6, 15, 14, 5, tzinfo=UTC)
    for _ in range(3):
        panel_factory(level="WARNING", timestamp=ts)

    rows, _ = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    assert rows[0]["timeline"][-1]["status"] == "warning"


@pytest.mark.django_db
def test_get_logger_cards_slot_has_timestamp_from_and_to(panel_factory, backend):
    panel_factory(timestamp=datetime(2024, 6, 15, 14, 5, tzinfo=UTC))

    rows, _ = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    last_slot = rows[0]["timeline"][-1]
    assert "timestamp_from" in last_slot
    assert "timestamp_to" in last_slot
    assert last_slot["timestamp_from"] == "2024-06-15T14:00"
    assert last_slot["timestamp_to"] == "2024-06-15T15:00"


@pytest.mark.django_db
def test_query_logs_returns_all_when_no_filters(panel_factory, backend):
    for _ in range(3):
        panel_factory()
    logs = backend.query_logs(
        logger_names=None,
        levels=None,
        search="",
        offset=0,
        limit=None,
        app_timezone=UTC,
    )
    assert len(logs) == 3


@pytest.mark.django_db
def test_query_logs_none_logger_names_returns_all_loggers(panel_factory, backend):
    panel_factory(logger_name="app_a")
    panel_factory(logger_name="app_b")
    logs = backend.query_logs(
        logger_names=None,
        levels=None,
        search="",
        offset=0,
        limit=None,
        app_timezone=UTC,
    )
    assert len(logs) == 2


@pytest.mark.django_db
def test_query_logs_filters_single_logger_name(panel_factory, backend):
    panel_factory(logger_name="orders")
    panel_factory(logger_name="orders")
    panel_factory(logger_name="auth")
    logs = backend.query_logs(
        logger_names=["orders"],
        levels=None,
        search="",
        offset=0,
        limit=None,
        app_timezone=UTC,
    )
    assert len(logs) == 2
    assert all(log["logger_name"] == "orders" for log in logs)


@pytest.mark.django_db
def test_query_logs_filters_multiple_logger_names(panel_factory, backend):
    panel_factory(logger_name="orders")
    panel_factory(logger_name="machines")
    panel_factory(logger_name="auth")
    logs = backend.query_logs(
        logger_names=["orders", "machines"],
        levels=None,
        search="",
        offset=0,
        limit=None,
        app_timezone=UTC,
    )
    assert len(logs) == 2


@pytest.mark.django_db
def test_query_logs_filters_by_levels(panel_factory, backend):
    panel_factory(level="DEBUG")
    panel_factory(level="WARNING")
    panel_factory(level="ERROR")
    logs = backend.query_logs(
        logger_names=None,
        levels=["WARNING", "ERROR"],
        search="",
        offset=0,
        limit=None,
        app_timezone=UTC,
    )
    assert len(logs) == 2
    assert all(log["level"] in {"WARNING", "ERROR"} for log in logs)


@pytest.mark.django_db
def test_query_logs_none_levels_returns_all_levels(panel_factory, backend):
    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        panel_factory(level=level)
    logs = backend.query_logs(
        logger_names=None,
        levels=None,
        search="",
        offset=0,
        limit=None,
        app_timezone=UTC,
    )
    assert len(logs) == 5


@pytest.mark.django_db
def test_query_logs_filters_by_search(panel_factory, backend):
    panel_factory(message="disk full")
    panel_factory(message="user login ok")
    logs = backend.query_logs(
        logger_names=None,
        levels=None,
        search="disk",
        offset=0,
        limit=None,
        app_timezone=UTC,
    )
    assert len(logs) == 1
    assert logs[0]["message"] == "disk full"


@pytest.mark.django_db
def test_query_logs_offset_skips_entries(panel_factory, backend):
    for _ in range(5):
        panel_factory()
    logs = backend.query_logs(
        logger_names=None,
        levels=None,
        search="",
        offset=3,
        limit=None,
        app_timezone=UTC,
    )
    assert len(logs) == 2


@pytest.mark.django_db
def test_query_logs_limit_caps_results(panel_factory, backend):
    for _ in range(5):
        panel_factory()
    logs = backend.query_logs(
        logger_names=None, levels=None, search="", offset=0, limit=2, app_timezone=UTC
    )
    assert len(logs) == 2


@pytest.mark.django_db
def test_query_logs_returns_expected_fields(panel_factory, backend):
    panel_factory()
    logs = backend.query_logs(
        logger_names=None,
        levels=None,
        search="",
        offset=0,
        limit=None,
        app_timezone=UTC,
    )
    for field in (
        "id",
        "timestamp",
        "level",
        "logger_name",
        "message",
        "module",
        "pathname",
        "line_number",
    ):
        assert field in logs[0], f"Missing field: {field}"


@pytest.mark.django_db
def test_query_logs_combines_logger_names_and_levels(panel_factory, backend):
    panel_factory(logger_name="orders", level="WARNING")
    panel_factory(logger_name="orders", level="DEBUG")
    panel_factory(logger_name="auth", level="WARNING")
    logs = backend.query_logs(
        logger_names=["orders"],
        levels=["WARNING", "ERROR", "CRITICAL"],
        search="",
        offset=0,
        limit=None,
        app_timezone=UTC,
    )
    assert len(logs) == 1
    assert logs[0]["logger_name"] == "orders"
    assert logs[0]["level"] == "WARNING"


@pytest.mark.django_db
def test_count_logs_returns_total_with_no_filters(panel_factory, backend):
    for _ in range(4):
        panel_factory()
    assert backend.count_logs(logger_names=None, levels=None, search="") == 4


@pytest.mark.django_db
def test_count_logs_filters_by_logger_names(panel_factory, backend):
    panel_factory(logger_name="orders")
    panel_factory(logger_name="orders")
    panel_factory(logger_name="auth")
    assert backend.count_logs(logger_names=["orders"], levels=None, search="") == 2


@pytest.mark.django_db
def test_count_logs_filters_by_levels(panel_factory, backend):
    panel_factory(level="DEBUG")
    panel_factory(level="WARNING")
    panel_factory(level="ERROR")
    assert (
        backend.count_logs(logger_names=None, levels=["WARNING", "ERROR"], search="")
        == 2
    )


@pytest.mark.django_db
def test_count_logs_filters_by_search(panel_factory, backend):
    panel_factory(message="disk full")
    panel_factory(message="user login ok")
    assert backend.count_logs(logger_names=None, levels=None, search="disk") == 1


@pytest.mark.django_db
def test_count_logs_combines_filters(panel_factory, backend):
    panel_factory(logger_name="orders", level="WARNING")
    panel_factory(logger_name="orders", level="DEBUG")
    panel_factory(logger_name="auth", level="WARNING")
    assert (
        backend.count_logs(
            logger_names=["orders"], levels=["WARNING", "ERROR", "CRITICAL"], search=""
        )
        == 1
    )


@pytest.mark.django_db
def test_query_logs_filters_by_timestamp_from(panel_factory, backend):
    panel_factory(timestamp=datetime(2024, 6, 15, 10, 0, tzinfo=UTC))
    panel_factory(timestamp=datetime(2024, 6, 15, 12, 0, tzinfo=UTC))

    logs = backend.query_logs(
        logger_names=None,
        levels=None,
        search="",
        offset=0,
        limit=None,
        app_timezone=UTC,
        timestamp_from=datetime(2024, 6, 15, 11, 0, tzinfo=UTC),
    )
    assert len(logs) == 1
    assert logs[0]["timestamp"].hour == 12


@pytest.mark.django_db
def test_query_logs_filters_by_timestamp_to(panel_factory, backend):
    panel_factory(timestamp=datetime(2024, 6, 15, 10, 0, tzinfo=UTC))
    panel_factory(timestamp=datetime(2024, 6, 15, 12, 0, tzinfo=UTC))

    logs = backend.query_logs(
        logger_names=None,
        levels=None,
        search="",
        offset=0,
        limit=None,
        app_timezone=UTC,
        timestamp_to=datetime(2024, 6, 15, 11, 0, tzinfo=UTC),
    )
    assert len(logs) == 1
    assert logs[0]["timestamp"].hour == 10


@pytest.mark.django_db
def test_count_logs_filters_by_timestamp_from(panel_factory, backend):
    panel_factory(timestamp=datetime(2024, 6, 15, 10, 0, tzinfo=UTC))
    panel_factory(timestamp=datetime(2024, 6, 15, 12, 0, tzinfo=UTC))

    assert (
        backend.count_logs(
            logger_names=None,
            levels=None,
            search="",
            timestamp_from=datetime(2024, 6, 15, 11, 0, tzinfo=UTC),
        )
        == 1
    )


@pytest.mark.django_db
def test_count_logs_filters_by_timestamp_to(panel_factory, backend):
    panel_factory(timestamp=datetime(2024, 6, 15, 10, 0, tzinfo=UTC))
    panel_factory(timestamp=datetime(2024, 6, 15, 12, 0, tzinfo=UTC))

    assert (
        backend.count_logs(
            logger_names=None,
            levels=None,
            search="",
            timestamp_to=datetime(2024, 6, 15, 11, 0, tzinfo=UTC),
        )
        == 1
    )


@pytest.mark.django_db
def test_get_logger_cards_handles_naive_bucket(panel_factory, backend):
    panel_factory(
        level="ERROR",
        timestamp=datetime(2024, 6, 15, 14, 5, tzinfo=UTC),
    )

    from log_panel.models import LogTimelineBucket

    for b in LogTimelineBucket.objects.all():
        LogTimelineBucket.objects.filter(pk=b.pk).update(
            bucket=b.bucket.replace(tzinfo=None)
        )

    rows, _ = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )

    assert len(rows) == 1
    last_slot = rows[0]["timeline"][-1]
    assert last_slot["status"] == "error"


@pytest.mark.django_db
def test_get_logger_cards_with_day_range_unit(panel_factory, backend):
    panel_factory(
        level="ERROR",
        timestamp=datetime(2024, 6, 15, 14, 5, tzinfo=UTC),
    )

    rows, _ = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=DAY_RANGE, app_timezone=UTC
    )
    assert len(rows) == 1
    assert len(rows[0]["timeline"]) == 7
    last_slot = rows[0]["timeline"][-1]
    assert last_slot["status"] == "error"
