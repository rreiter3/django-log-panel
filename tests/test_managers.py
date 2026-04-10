from datetime import UTC, datetime

import pytest

from log_panel.models import Panel


@pytest.mark.django_db
def test_create_from_record_returns_panel_instance():
    ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
    result = Panel.objects.create_from_record(
        timestamp=ts,
        level="ERROR",
        logger_name="myapp.views",
        message="Something went wrong",
        module="views",
        pathname="/app/views.py",
        line_number=99,
    )
    assert isinstance(result, Panel)


@pytest.mark.django_db
def test_create_from_record_persists_all_fields():
    ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
    Panel.objects.create_from_record(
        timestamp=ts,
        level="ERROR",
        logger_name="myapp.views",
        message="Something went wrong",
        module="views",
        pathname="/app/views.py",
        line_number=99,
    )

    panel = Panel.objects.get()
    assert panel.timestamp == ts
    assert panel.level == "ERROR"
    assert panel.logger_name == "myapp.views"
    assert panel.message == "Something went wrong"
    assert panel.module == "views"
    assert panel.pathname == "/app/views.py"
    assert panel.line_number == 99
