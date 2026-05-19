from datetime import UTC, datetime

import pytest

from log_panel.management.commands.rebuild_log_cards import Command
from log_panel.models import Log, LogCard, LogTimelineBucket
from log_panel.types import RangeUnit


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2024, 6, 15, hour, minute, tzinfo=UTC)


@pytest.mark.django_db
def test_refresh_cards_replaces_existing_card_snapshot(panel_factory):
    panel_factory(logger_name="app", level="WARNING", timestamp=dt(hour=10))
    Log.objects.create(
        timestamp=dt(hour=11),
        level="ERROR",
        logger_name="app",
        message="failed",
        module="views",
        pathname="/app/views.py",
        line_number=42,
    )
    LogCard.objects.filter(logger__name="app").update(
        total=999,
        total_errors=999,
        total_warnings=999,
        last_seen=dt(hour=9),
    )

    refreshed_count = Command().refresh_cards()

    card = LogCard.objects.select_related("logger").get(logger__name="app")
    assert refreshed_count == 1
    assert card.total == 2
    assert card.total_errors == 1
    assert card.total_warnings == 1
    assert card.last_seen == dt(hour=11)


@pytest.mark.django_db
def test_refresh_timeline_buckets_replaces_existing_bucket_snapshots(panel_factory):
    panel_factory(logger_name="app", level="WARNING", timestamp=dt(hour=10, minute=15))
    Log.objects.create(
        timestamp=dt(hour=10, minute=45),
        level="ERROR",
        logger_name="app",
        message="failed",
        module="views",
        pathname="/app/views.py",
        line_number=42,
    )
    LogTimelineBucket.objects.filter(logger__name="app").update(
        log_count=999,
        error_count=999,
        warning_count=999,
    )

    buckets_count = Command().refresh_timeline_buckets()

    hourly = LogTimelineBucket.objects.get(
        logger__name="app",
        bucket=dt(hour=10),
        unit=RangeUnit.HOUR,
    )
    daily = LogTimelineBucket.objects.get(
        logger__name="app",
        bucket=dt(hour=0),
        unit=RangeUnit.DAY,
    )
    assert buckets_count == 2
    assert hourly.log_count == 2
    assert hourly.error_count == 1
    assert hourly.warning_count == 1
    assert daily.log_count == 2
    assert daily.error_count == 1
    assert daily.warning_count == 1
