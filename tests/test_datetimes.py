from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import override_settings

from log_panel.datetimes import to_database_datetime, to_display_datetime

BUDAPEST = ZoneInfo("Europe/Budapest")


@override_settings(USE_TZ=False, TIME_ZONE="Europe/Budapest")
def test_to_database_datetime_strips_timezone_when_use_tz_false():
    result = to_database_datetime(datetime(2024, 6, 15, 12, 0, tzinfo=UTC))

    assert result == datetime(2024, 6, 15, 14, 0)
    assert result.tzinfo is None


@override_settings(USE_TZ=True, TIME_ZONE="Europe/Budapest")
def test_to_database_datetime_makes_naive_datetime_aware_when_use_tz_true():
    result = to_database_datetime(datetime(2024, 6, 15, 14, 0))

    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(hours=2)


def test_to_display_datetime_makes_naive_datetime_aware_in_app_timezone():
    result = to_display_datetime(datetime(2024, 6, 15, 14, 0), BUDAPEST)

    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(hours=2)
