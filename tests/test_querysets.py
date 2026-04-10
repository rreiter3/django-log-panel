from datetime import UTC, datetime, timedelta

import pytest

from log_panel.models import Panel
from log_panel.types import RangeConfig, RangeUnit


def dt(year=2024, month=6, day=15, hour=14, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


NOW = dt()
ONE_HOUR_AGO = NOW - timedelta(hours=1)
TWO_HOURS_AGO = NOW - timedelta(hours=2)
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


@pytest.mark.django_db
def test_with_total_counts_all_entries_per_logger(panel_factory):
    panel_factory(logger_name="app_a")
    panel_factory(logger_name="app_a")
    panel_factory(logger_name="app_a")
    panel_factory(logger_name="app_b")
    panel_factory(logger_name="app_b")

    rows = {
        r["logger_name"]: r["total"]
        for r in Panel.objects.values("logger_name").with_total()  # ty: ignore[unresolved-attribute]
    }
    assert rows["app_a"] == 3
    assert rows["app_b"] == 2


@pytest.mark.django_db
def test_with_total_errors_counts_error_and_critical(panel_factory):
    panel_factory(level="ERROR")
    panel_factory(level="CRITICAL")
    panel_factory(level="INFO")

    row = Panel.objects.values("logger_name").with_total_errors().get()  # ty: ignore[unresolved-attribute]
    assert row["total_errors"] == 2


@pytest.mark.django_db
def test_with_total_errors_excludes_warning(panel_factory):
    panel_factory(level="WARNING")
    panel_factory(level="INFO")

    row = Panel.objects.values("logger_name").with_total_errors().get()  # ty: ignore[unresolved-attribute]
    assert row["total_errors"] == 0


@pytest.mark.django_db
def test_with_total_warnings_counts_only_warnings(panel_factory):
    panel_factory(level="WARNING")
    panel_factory(level="WARNING")
    panel_factory(level="ERROR")

    row = Panel.objects.values("logger_name").with_total_warnings().get()  # ty: ignore[unresolved-attribute]
    assert row["total_warnings"] == 2


@pytest.mark.django_db
def test_with_recent_errors_includes_entries_after_cutoff(panel_factory):
    panel_factory(level="ERROR", timestamp=TWO_HOURS_AGO)
    panel_factory(level="ERROR", timestamp=NOW)

    row = (
        Panel.objects.values("logger_name")  # ty: ignore[unresolved-attribute]
        .with_recent_errors(one_hour_ago=ONE_HOUR_AGO)
        .get()
    )
    assert row["recent_errors"] == 1


@pytest.mark.django_db
def test_with_recent_errors_excludes_entries_before_cutoff(panel_factory):
    panel_factory(level="ERROR", timestamp=TWO_HOURS_AGO)

    row = (
        Panel.objects.values("logger_name")  # ty: ignore[unresolved-attribute]
        .with_recent_errors(one_hour_ago=ONE_HOUR_AGO)
        .get()
    )
    assert row["recent_errors"] == 0


@pytest.mark.django_db
def test_with_recent_warnings_includes_entries_after_cutoff(panel_factory):
    panel_factory(level="WARNING", timestamp=TWO_HOURS_AGO)
    panel_factory(level="WARNING", timestamp=NOW)

    row = (
        Panel.objects.values("logger_name")  # ty: ignore[unresolved-attribute]
        .with_recent_warnings(one_hour_ago=ONE_HOUR_AGO)
        .get()
    )
    assert row["recent_warnings"] == 1


@pytest.mark.django_db
def test_with_last_seen_returns_max_timestamp(panel_factory):
    early = dt(hour=10)
    late = dt(hour=14)
    panel_factory(timestamp=early)
    panel_factory(timestamp=late)

    row = Panel.objects.values("logger_name").with_last_seen().get()  # ty: ignore[unresolved-attribute]
    assert row["last_seen"] == late


@pytest.mark.django_db
def test_cards_aggregation_groups_by_logger_name(panel_factory):
    panel_factory(logger_name="app_a")
    panel_factory(logger_name="app_b")

    rows = list(Panel.objects.all().cards_aggregation(one_hour_ago=ONE_HOUR_AGO))  # ty: ignore[unresolved-attribute]
    names = {r["logger_name"] for r in rows}
    assert names == {"app_a", "app_b"}


@pytest.mark.django_db
def test_cards_aggregation_ordered_by_last_seen_descending(panel_factory):
    panel_factory(logger_name="app_a", timestamp=dt(hour=10))
    panel_factory(logger_name="app_b", timestamp=dt(hour=14))

    rows = list(Panel.objects.all().cards_aggregation(one_hour_ago=ONE_HOUR_AGO))  # ty: ignore[unresolved-attribute]
    assert rows[0]["logger_name"] == "app_b"
    assert rows[1]["logger_name"] == "app_a"


@pytest.mark.django_db
def test_cards_aggregation_includes_all_annotations(panel_factory):
    panel_factory()

    row = Panel.objects.all().cards_aggregation(one_hour_ago=ONE_HOUR_AGO).get()  # ty: ignore[unresolved-attribute]
    for key in (
        "logger_name",
        "total",
        "total_errors",
        "total_warnings",
        "recent_errors",
        "recent_warnings",
        "last_seen",
    ):
        assert key in row, f"Missing key: {key}"


@pytest.mark.django_db
def test_with_has_error_nonzero_when_error_exists(panel_factory):
    panel_factory(level="ERROR")

    row = Panel.objects.values("logger_name").with_has_error().get()  # ty: ignore[unresolved-attribute]
    assert row["has_error"] > 0


@pytest.mark.django_db
def test_with_has_error_zero_when_no_errors(panel_factory):
    panel_factory(level="INFO")
    panel_factory(level="WARNING")

    row = Panel.objects.values("logger_name").with_has_error().get()  # ty: ignore[unresolved-attribute]
    assert row["has_error"] == 0


@pytest.mark.django_db
def test_with_has_warning_nonzero_when_warning_exists(panel_factory):
    panel_factory(level="WARNING")

    row = Panel.objects.values("logger_name").with_has_warning().get()  # ty: ignore[unresolved-attribute]
    assert row["has_warning"] > 0


@pytest.mark.django_db
def test_with_has_warning_zero_when_no_warnings(panel_factory):
    panel_factory(level="INFO")
    panel_factory(level="ERROR")

    row = Panel.objects.values("logger_name").with_has_warning().get()  # ty: ignore[unresolved-attribute]
    assert row["has_warning"] == 0


@pytest.mark.django_db
def test_timeline_aggregation_filters_by_cutoff(panel_factory):
    cutoff = dt(hour=10)
    panel_factory(timestamp=dt(hour=9))
    panel_factory(timestamp=dt(hour=11))

    rows = list(
        Panel.objects.all().timeline_aggregation(  # ty: ignore[unresolved-attribute]
            cutoff=cutoff,
            range_config=HOUR_RANGE,
            app_timezone=UTC,
        )
    )
    assert len(rows) == 1


@pytest.mark.django_db
def test_timeline_aggregation_groups_by_bucket_and_logger(panel_factory):
    panel_factory(timestamp=dt(hour=14, minute=10))
    panel_factory(timestamp=dt(hour=14, minute=45))

    rows = list(
        Panel.objects.all().timeline_aggregation(  # ty: ignore[unresolved-attribute]
            cutoff=dt(hour=0),
            range_config=HOUR_RANGE,
            app_timezone=UTC,
        )
    )
    assert len(rows) == 1


@pytest.mark.django_db
def test_timeline_aggregation_hour_unit_truncates_to_hour(panel_factory):
    panel_factory(timestamp=dt(hour=14, minute=37))

    row = (
        Panel.objects.all()
        .timeline_aggregation(  # ty: ignore[unresolved-attribute]
            cutoff=dt(hour=0),
            range_config=HOUR_RANGE,
            app_timezone=UTC,
        )
        .get()
    )
    assert row["bucket"] == dt(hour=14, minute=0)


@pytest.mark.django_db
def test_timeline_aggregation_day_unit_truncates_to_day(panel_factory):
    panel_factory(timestamp=dt(hour=14, minute=37))

    row = (
        Panel.objects.all()
        .timeline_aggregation(  # ty: ignore[unresolved-attribute]
            cutoff=datetime(2024, 6, 1, tzinfo=UTC),
            range_config=DAY_RANGE,
            app_timezone=UTC,
        )
        .get()
    )
    assert row["bucket"] == datetime(2024, 6, 15, 0, 0, tzinfo=UTC)
