from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.test import override_settings

from log_panel.backends.sql import SqlBackend
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


@pytest.fixture
def backend():
    return SqlBackend()


@pytest.mark.django_db
def test_get_log_table_returns_entries_for_logger(panel_factory, backend):
    panel_factory(logger_name="app_a")
    panel_factory(logger_name="app_a")
    panel_factory(logger_name="app_a")
    panel_factory(logger_name="app_b")
    panel_factory(logger_name="app_b")

    logs, total = backend.get_log_table(
        logger_name="app_a", level="", search="", page=1, page_size=10, app_timezone=UTC
    )
    assert len(logs) == 3
    assert total == 3


@pytest.mark.django_db
def test_get_log_table_returns_correct_total(panel_factory, backend):
    for _ in range(7):
        panel_factory(logger_name="myapp")

    _, total = backend.get_log_table(
        logger_name="myapp", level="", search="", page=1, page_size=3, app_timezone=UTC
    )
    assert total == 7


@pytest.mark.django_db
def test_get_log_table_filters_by_level(panel_factory, backend):
    panel_factory(level="ERROR")
    panel_factory(level="ERROR")
    panel_factory(level="INFO")

    logs, total = backend.get_log_table(
        logger_name="myapp",
        level="ERROR",
        search="",
        page=1,
        page_size=10,
        app_timezone=UTC,
    )
    assert total == 2
    assert all(log["level"] == "ERROR" for log in logs)


@pytest.mark.django_db
def test_get_log_table_filters_by_search(panel_factory, backend):
    panel_factory(message="database connection error")
    panel_factory(message="user login successful")

    logs, total = backend.get_log_table(
        logger_name="myapp",
        level="",
        search="database",
        page=1,
        page_size=10,
        app_timezone=UTC,
    )
    assert total == 1
    assert logs[0]["message"] == "database connection error"


@pytest.mark.django_db
def test_get_log_table_paginates_correctly(panel_factory, backend):
    for i in range(5):
        panel_factory(
            timestamp=datetime(2024, 6, 15, 10, i, tzinfo=UTC),
            message=f"message {i}",
        )

    logs, total = backend.get_log_table(
        logger_name="myapp", level="", search="", page=2, page_size=2, app_timezone=UTC
    )
    assert total == 5
    assert len(logs) == 2


@pytest.mark.django_db
def test_get_log_table_empty_level_returns_all_levels(panel_factory, backend):
    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        panel_factory(level=level)

    _, total = backend.get_log_table(
        logger_name="myapp", level="", search="", page=1, page_size=10, app_timezone=UTC
    )
    assert total == 5


@pytest.mark.django_db
def test_get_log_table_converts_timestamp_to_app_timezone(panel_factory, backend):
    ts_utc = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    panel_factory(timestamp=ts_utc)

    logs, _ = backend.get_log_table(
        logger_name="myapp",
        level="",
        search="",
        page=1,
        page_size=10,
        app_timezone=BUDAPEST,
    )
    result_ts = logs[0]["timestamp"]
    assert result_ts.tzinfo == BUDAPEST
    assert result_ts.hour == 14


@pytest.mark.django_db
def test_get_log_table_returns_expected_fields(panel_factory, backend):
    panel_factory()

    logs, _ = backend.get_log_table(
        logger_name="myapp", level="", search="", page=1, page_size=10, app_timezone=UTC
    )
    row = logs[0]
    for field in (
        "_id",
        "timestamp",
        "level",
        "logger_name",
        "message",
        "module",
        "pathname",
        "line_number",
    ):
        assert field in row, f"Missing field: {field}"


@pytest.mark.django_db
def test_get_logger_cards_returns_one_row_per_logger(panel_factory, backend):
    panel_factory(logger_name="app_a")
    panel_factory(logger_name="app_b")

    rows = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    names = {r["logger_name"] for r in rows}
    assert names == {"app_a", "app_b"}


@pytest.mark.django_db
def test_get_logger_cards_row_contains_required_keys(panel_factory, backend):
    panel_factory()

    rows = backend.get_logger_cards(
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

    rows = backend.get_logger_cards(
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
    rows = backend.get_logger_cards(
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
    rows = backend.get_logger_cards(
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
    rows = backend.get_logger_cards(
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
    rows = backend.get_logger_cards(
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

    rows = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    last_slot = rows[0]["timeline"][-1]
    assert last_slot["status"] == "error"


@pytest.mark.django_db
def test_get_logger_cards_slot_labels_match_format(panel_factory, backend):
    panel_factory(timestamp=datetime(2024, 6, 15, 14, 5, tzinfo=UTC))

    rows = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    for slot in rows[0]["timeline"]:
        assert slot["label"].endswith(":00"), f"Unexpected label: {slot['label']}"


@pytest.mark.django_db
def test_get_logger_cards_empty_db_returns_empty_list(backend):
    rows = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    assert rows == []


@pytest.mark.django_db
@override_settings(LOG_PANEL={"DATABASE_ALIAS": "default", "THRESHOLDS": {"ERROR": 3}})
def test_slot_stays_ok_when_error_count_below_error_threshold(panel_factory, backend):
    ts = datetime(2024, 6, 15, 14, 5, tzinfo=UTC)
    panel_factory(level="ERROR", timestamp=ts)
    panel_factory(level="ERROR", timestamp=ts)  # 2 errors, threshold is 3

    rows = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    assert rows[0]["timeline"][-1]["status"] == "ok"


@pytest.mark.django_db
@override_settings(LOG_PANEL={"DATABASE_ALIAS": "default", "THRESHOLDS": {"ERROR": 3}})
def test_slot_is_error_when_error_count_meets_error_threshold(panel_factory, backend):
    ts = datetime(2024, 6, 15, 14, 5, tzinfo=UTC)
    for _ in range(3):
        panel_factory(level="ERROR", timestamp=ts)

    rows = backend.get_logger_cards(
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
    panel_factory(level="WARNING", timestamp=ts)  # 2 warnings, threshold is 3

    rows = backend.get_logger_cards(
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

    rows = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    assert rows[0]["timeline"][-1]["status"] == "warning"


@pytest.mark.django_db
def test_get_logger_cards_slot_has_timestamp_from_and_to(panel_factory, backend):
    panel_factory(timestamp=datetime(2024, 6, 15, 14, 5, tzinfo=UTC))

    rows = backend.get_logger_cards(
        now_utc=NOW_UTC, range_config=HOUR_RANGE, app_timezone=UTC
    )
    last_slot = rows[0]["timeline"][-1]
    assert "timestamp_from" in last_slot
    assert "timestamp_to" in last_slot
    assert last_slot["timestamp_from"] == "2024-06-15T14:00"
    assert last_slot["timestamp_to"] == "2024-06-15T15:00"


@pytest.mark.django_db
def test_get_log_table_filters_by_timestamp_from(panel_factory, backend):
    panel_factory(timestamp=datetime(2024, 6, 15, 10, 0, tzinfo=UTC))
    panel_factory(timestamp=datetime(2024, 6, 15, 12, 0, tzinfo=UTC))

    logs, total = backend.get_log_table(
        logger_name="myapp",
        level="",
        search="",
        page=1,
        page_size=10,
        app_timezone=UTC,
        timestamp_from=datetime(2024, 6, 15, 11, 0, tzinfo=UTC),
    )
    assert total == 1
    assert logs[0]["timestamp"].hour == 12


@pytest.mark.django_db
def test_get_log_table_filters_by_timestamp_to(panel_factory, backend):
    panel_factory(timestamp=datetime(2024, 6, 15, 10, 0, tzinfo=UTC))
    panel_factory(timestamp=datetime(2024, 6, 15, 12, 0, tzinfo=UTC))

    logs, total = backend.get_log_table(
        logger_name="myapp",
        level="",
        search="",
        page=1,
        page_size=10,
        app_timezone=UTC,
        timestamp_to=datetime(2024, 6, 15, 11, 0, tzinfo=UTC),
    )
    assert total == 1
    assert logs[0]["timestamp"].hour == 10
