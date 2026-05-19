import logging

import pytest
from django.utils import timezone

from log_panel.conf import reset_backend_cache
from log_panel.models import Log, LogCard, LogTimelineBucket
from log_panel.types import ERROR_LEVELS, LogLevel


@pytest.fixture(autouse=True)
def _reset_backend_cache():
    """Ensure each test starts with a fresh backend instance."""
    reset_backend_cache()
    yield
    reset_backend_cache()


@pytest.fixture
def log_record_factory():
    """Return a callable that creates logging.LogRecord instances."""

    def make_log_record(**kwargs):
        defaults = {
            "name": "myapp",
            "level": logging.ERROR,
            "msg": "something broke",
            "module": "views",
            "pathname": "/app/views.py",
            "lineno": 55,
        }
        defaults.update(kwargs)
        record = logging.LogRecord(
            name=defaults["name"],
            level=defaults["level"],
            pathname=defaults["pathname"],
            lineno=defaults["lineno"],
            msg=defaults["msg"],
            args=(),
            exc_info=None,
        )
        record.module = defaults["module"]
        return record

    return make_log_record


@pytest.fixture
def panel_factory(db):
    """Return a callable that creates Log instances and maintains pre-computed models."""

    def make_panel(**kwargs):
        defaults = {
            "timestamp": timezone.now(),
            "level": "INFO",
            "logger_name": "myapp",
            "message": "test message",
            "module": "views",
            "pathname": "/app/views.py",
            "line_number": 42,
        }
        defaults.update(kwargs)
        log = Log.objects.create(**defaults)

        LogCard.objects.upsert(
            logger_name=defaults["logger_name"],
            total_delta=1,
            error_delta=1 if defaults["level"] in ERROR_LEVELS else 0,
            warning_delta=1 if defaults["level"] == LogLevel.WARNING else 0,
            last_seen=defaults["timestamp"],
        )
        LogTimelineBucket.objects.upsert(
            logger_name=defaults["logger_name"],
            timestamp=defaults["timestamp"],
            level=defaults["level"],
        )

        return log

    return make_panel
