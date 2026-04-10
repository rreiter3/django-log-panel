from unittest.mock import MagicMock, patch

import pytest
from django.contrib.admin import AdminSite
from django.test import RequestFactory

from log_panel.admin import PanelAdmin
from log_panel.models import Panel


@pytest.fixture
def panel_admin():
    admin = PanelAdmin(Panel, AdminSite())
    admin.admin_site.each_context = lambda req: {}  # ty: ignore[invalid-assignment]
    return admin


@pytest.fixture
def factory():
    return RequestFactory()


def make_backend(rows=None, logs=None, total=0):
    backend = MagicMock()
    backend.get_logger_cards.return_value = rows or []
    backend.get_log_table.return_value = (logs or [], total)
    return backend


@pytest.mark.django_db
def test_changelist_view_without_logger_name_renders_cards_view(panel_admin, factory):
    request = factory.get("/admin/log_panel/panel/")
    with patch("log_panel.admin.conf.get_backend", return_value=None):
        response = panel_admin.changelist_view(request)
    assert response.context_data["view"] == "cards"


@pytest.mark.django_db
def test_changelist_view_with_logger_name_renders_table_view(panel_admin, factory):
    request = factory.get("/admin/log_panel/panel/", {"logger_name": "myapp"})
    with patch("log_panel.admin.conf.get_backend", return_value=None):
        response = panel_admin.changelist_view(request)
    assert response.context_data["view"] == "table"


def test_logger_cards_context_defaults_to_24h_range(panel_admin, factory):
    request = factory.get("/")
    ctx = panel_admin._logger_cards_context(request, backend=None, error=None)
    assert ctx["selected_range"] == "24h"


def test_logger_cards_context_accepts_valid_range(panel_admin, factory):
    request = factory.get("/", {"range": "30d"})
    ctx = panel_admin._logger_cards_context(request, backend=None, error=None)
    assert ctx["selected_range"] == "30d"


def test_logger_cards_context_invalid_range_falls_back_to_first_key(
    panel_admin, factory
):
    request = factory.get("/", {"range": "999y"})
    ctx = panel_admin._logger_cards_context(request, backend=None, error=None)
    assert ctx["selected_range"] == "24h"


def test_logger_cards_context_uses_range_config_label(panel_admin, factory):
    request = factory.get("/", {"range": "24h"})
    ctx = panel_admin._logger_cards_context(request, backend=None, error=None)
    assert ctx["range_label"] == "Last 24 hours"


def test_logger_cards_context_falls_back_to_range_key_when_no_label(
    panel_admin, factory
):
    from datetime import timedelta

    from log_panel.types import RangeConfig, RangeUnit

    no_label_range = RangeConfig(
        delta=timedelta(hours=1),
        unit=RangeUnit.HOUR,
        slots=1,
        format="%H:00",
        label=None,
    )
    request = factory.get("/", {"range": "1h"})
    with patch(
        "log_panel.admin.conf.get_ranges",
        return_value={"1h": no_label_range},
    ):
        ctx = panel_admin._logger_cards_context(request, backend=None, error=None)
    assert ctx["range_label"] == "1h"


def test_logger_cards_context_no_backend_returns_empty_rows(panel_admin, factory):
    request = factory.get("/")
    ctx = panel_admin._logger_cards_context(request, backend=None, error=None)
    assert ctx["logger_rows"] == []
    assert ctx["error"] is None


def test_logger_cards_context_calls_backend_and_returns_rows(panel_admin, factory):
    backend = make_backend(rows=[{"logger_name": "myapp"}])
    request = factory.get("/")
    ctx = panel_admin._logger_cards_context(request, backend=backend, error=None)
    assert len(ctx["logger_rows"]) == 1
    assert ctx["error"] is None


def test_logger_cards_context_backend_exception_sets_error(panel_admin, factory):
    backend = MagicMock()
    backend.get_logger_cards.side_effect = RuntimeError("connection failed")
    request = factory.get("/")
    ctx = panel_admin._logger_cards_context(request, backend=backend, error=None)
    assert ctx["logger_rows"] == []
    assert "connection failed" in ctx["error"]


def test_log_table_context_defaults_page_to_1(panel_admin, factory):
    request = factory.get("/")
    ctx = panel_admin._log_table_context(request, None, "myapp", None)
    assert ctx["page"] == 1


def test_log_table_context_invalid_page_string_defaults_to_1(panel_admin, factory):
    request = factory.get("/", {"page": "abc"})
    ctx = panel_admin._log_table_context(request, None, "myapp", None)
    assert ctx["page"] == 1


def test_log_table_context_page_zero_clamped_to_1(panel_admin, factory):
    request = factory.get("/", {"page": "0"})
    ctx = panel_admin._log_table_context(request, None, "myapp", None)
    assert ctx["page"] == 1


def test_log_table_context_valid_page_number_used(panel_admin, factory):
    request = factory.get("/", {"page": "3"})
    ctx = panel_admin._log_table_context(request, None, "myapp", None)
    assert ctx["page"] == 3


def test_log_table_context_total_pages_rounds_up(panel_admin, factory):
    backend = make_backend(total=11)
    request = factory.get("/")
    with patch("log_panel.admin.conf.get_setting", return_value=5):
        ctx = panel_admin._log_table_context(request, backend, "myapp", None)
    assert ctx["total_pages"] == 3


def test_log_table_context_total_pages_minimum_1_when_empty(panel_admin, factory):
    request = factory.get("/")
    ctx = panel_admin._log_table_context(request, None, "myapp", None)
    assert ctx["total_pages"] == 1


def test_log_table_context_has_prev_false_on_first_page(panel_admin, factory):
    backend = make_backend(total=20)
    request = factory.get("/", {"page": "1"})
    ctx = panel_admin._log_table_context(request, backend, "myapp", None)
    assert ctx["has_prev"] is False


def test_log_table_context_has_next_false_on_last_page(panel_admin, factory):
    backend = make_backend(total=5)
    request = factory.get("/", {"page": "1"})
    ctx = panel_admin._log_table_context(request, backend, "myapp", None)
    assert ctx["has_next"] is False


def test_log_table_context_has_prev_and_has_next_on_middle_page(panel_admin, factory):
    backend = make_backend(total=30)
    request = factory.get("/", {"page": "2"})
    ctx = panel_admin._log_table_context(request, backend, "myapp", None)
    assert ctx["has_prev"] is True
    assert ctx["has_next"] is True


def test_log_table_context_no_backend_returns_empty_logs(panel_admin, factory):
    request = factory.get("/")
    ctx = panel_admin._log_table_context(request, None, "myapp", None)
    assert ctx["logs"] == []
    assert ctx["total"] == 0
    assert ctx["error"] is None


def test_log_table_context_backend_exception_sets_error(panel_admin, factory):
    backend = MagicMock()
    backend.get_log_table.side_effect = RuntimeError("query failed")
    request = factory.get("/")
    ctx = panel_admin._log_table_context(request, backend, "myapp", None)
    assert ctx["logs"] == []
    assert "query failed" in ctx["error"]


def test_log_table_context_passes_filters_to_backend(panel_admin, factory):
    backend = make_backend()
    request = factory.get("/", {"level": "ERROR", "search": "db"})
    panel_admin._log_table_context(request, backend, "myapp", None)
    _, kwargs = backend.get_log_table.call_args
    assert kwargs["level"] == "ERROR"
    assert kwargs["search"] == "db"
    assert kwargs["logger_name"] == "myapp"


def test_log_table_context_passes_timestamp_params_to_backend(panel_admin, factory):
    backend = make_backend()
    request = factory.get(
        "/",
        {"timestamp_from": "2024-06-15T10:00", "timestamp_to": "2024-06-15T14:00"},
    )
    panel_admin._log_table_context(request, backend, "myapp", None)
    _, kwargs = backend.get_log_table.call_args
    assert kwargs["timestamp_from"] is not None
    assert kwargs["timestamp_to"] is not None
    assert kwargs["timestamp_from"].hour == 10
    assert kwargs["timestamp_to"].hour == 14


def test_log_table_context_timestamp_strings_included_in_context(panel_admin, factory):
    backend = make_backend()
    request = factory.get(
        "/",
        {"timestamp_from": "2024-06-15T10:00", "timestamp_to": "2024-06-15T14:00"},
    )
    ctx = panel_admin._log_table_context(request, backend, "myapp", None)
    assert ctx["timestamp_from"] == "2024-06-15T10:00"
    assert ctx["timestamp_to"] == "2024-06-15T14:00"


def test_log_table_context_invalid_timestamp_passes_none_to_backend(
    panel_admin, factory
):
    backend = make_backend()
    request = factory.get("/", {"timestamp_from": "not-a-date"})
    panel_admin._log_table_context(request, backend, "myapp", None)
    _, kwargs = backend.get_log_table.call_args
    assert kwargs["timestamp_from"] is None


def test_log_table_context_empty_timestamps_pass_none_to_backend(panel_admin, factory):
    backend = make_backend()
    request = factory.get("/")
    panel_admin._log_table_context(request, backend, "myapp", None)
    _, kwargs = backend.get_log_table.call_args
    assert kwargs["timestamp_from"] is None
    assert kwargs["timestamp_to"] is None
