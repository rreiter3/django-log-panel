import logging

import pytest
from django.utils import timezone

from log_panel.models import Panel


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
    """Return a callable that creates Panel instances."""

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
        return Panel.objects.create(**defaults)

    return make_panel
