from __future__ import annotations

from datetime import UTC, datetime, tzinfo

from django.conf import settings
from django.utils import timezone as django_timezone


def to_database_datetime(
    value: datetime, app_timezone: tzinfo | None = None
) -> datetime:
    """Return a datetime compatible with the project's DateTimeField settings."""
    timezone = app_timezone or django_timezone.get_default_timezone()
    if settings.USE_TZ:
        if django_timezone.is_naive(value):
            return django_timezone.make_aware(value, timezone)
        return value
    if django_timezone.is_aware(value):
        return django_timezone.make_naive(value, timezone)
    return value


def to_display_datetime(value: datetime, app_timezone: tzinfo) -> datetime:
    """Return a timezone-aware datetime in the admin display timezone."""
    if django_timezone.is_naive(value):
        return django_timezone.make_aware(value, app_timezone)
    return value.astimezone(app_timezone)


def from_record_timestamp(timestamp: float) -> datetime:
    """Return a log-record timestamp in the format expected by DateTimeField."""
    return to_database_datetime(
        value=datetime.fromtimestamp(timestamp=timestamp, tz=UTC)
    )
