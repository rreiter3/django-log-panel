from datetime import UTC, datetime


def allow_all(request):
    return True


def deny_all(request):
    return False


def dt(year=2024, month=6, day=15, hour=14, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)
