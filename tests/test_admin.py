from unittest.mock import MagicMock, patch

import pytest
from django.contrib.admin import AdminSite
from django.test import RequestFactory, override_settings

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


def make_rows():
    """Return a mix of logger rows with varying error/warning counts."""
    return [
        {"logger_name": "app.clean", "total_errors": 0, "total_warnings": 0},
        {"logger_name": "app.warn", "total_errors": 0, "total_warnings": 3},
        {"logger_name": "app.err", "total_errors": 5, "total_warnings": 0},
        {"logger_name": "app.both", "total_errors": 2, "total_warnings": 1},
    ]


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


def test_has_view_permission_allows_active_staff_by_default(panel_admin, factory):
    request = factory.get("/")
    request.user = MagicMock(is_active=True, is_staff=True)
    assert panel_admin.has_view_permission(request) is True


def test_has_view_permission_denies_inactive_user_by_default(panel_admin, factory):
    request = factory.get("/")
    request.user = MagicMock(is_active=False, is_staff=True)
    assert panel_admin.has_view_permission(request) is False


def test_has_view_permission_denies_non_staff_by_default(panel_admin, factory):
    request = factory.get("/")
    request.user = MagicMock(is_active=True, is_staff=False)
    assert panel_admin.has_view_permission(request) is False


@pytest.mark.django_db
def test_has_view_permission_uses_callback_when_configured(panel_admin, factory):
    request = factory.get("/")
    request.user = MagicMock(is_active=False, is_staff=False)
    with override_settings(
        LOG_PANEL={"PERMISSION_CALLBACK": "tests.helpers.allow_all"}
    ):
        assert panel_admin.has_view_permission(request) is True


@pytest.mark.django_db
def test_has_view_permission_callback_can_deny(panel_admin, factory):
    request = factory.get("/")
    request.user = MagicMock(is_active=True, is_staff=True)
    with override_settings(LOG_PANEL={"PERMISSION_CALLBACK": "tests.helpers.deny_all"}):
        assert panel_admin.has_view_permission(request) is False


def test_logger_cards_context_includes_level_colors(panel_admin, factory):
    request = factory.get("/")
    ctx = panel_admin._logger_cards_context(request, None, None)
    assert "level_colors" in ctx
    assert "ERROR" in ctx["level_colors"]


def test_log_table_context_includes_level_colors(panel_admin, factory):
    request = factory.get("/")
    ctx = panel_admin._log_table_context(request, None, "myapp", None)
    assert "level_colors" in ctx
    assert "ERROR" in ctx["level_colors"]


@override_settings(LOG_PANEL={"LEVEL_COLORS": {"ERROR": "#ff0000"}})
def test_logger_cards_context_reflects_custom_level_colors(panel_admin, factory):
    request = factory.get("/")
    ctx = panel_admin._logger_cards_context(request, None, None)
    assert ctx["level_colors"]["ERROR"] == "#ff0000"
    assert ctx["level_colors"]["WARNING"] == "#c0a000"


@override_settings(LOG_PANEL={"LEVEL_COLORS": {"MY_AUDIT": "#0055aa"}})
def test_log_table_context_level_colors_includes_custom_level(panel_admin, factory):
    request = factory.get("/")
    ctx = panel_admin._log_table_context(request, None, "myapp", None)
    assert "MY_AUDIT" in ctx["level_colors"]


def test_card_list_filter_defaults_to_all(factory):
    from log_panel.filters import CardListFilter
    from log_panel.types import CardFilter

    request = factory.get("/")
    f = CardListFilter(request)
    assert f.value is CardFilter.ALL


def test_card_list_filter_accepts_errors(factory):
    from log_panel.filters import CardListFilter
    from log_panel.types import CardFilter

    request = factory.get("/", {"filter": "errors"})
    f = CardListFilter(request)
    assert f.value is CardFilter.ERRORS


def test_card_list_filter_accepts_warnings(factory):
    from log_panel.filters import CardListFilter
    from log_panel.types import CardFilter

    request = factory.get("/", {"filter": "warnings"})
    f = CardListFilter(request)
    assert f.value is CardFilter.WARNINGS


def test_card_list_filter_unknown_falls_back_with_message(factory):
    from django.contrib.messages.storage.fallback import FallbackStorage

    from log_panel.filters import CardListFilter
    from log_panel.types import CardFilter

    request = factory.get("/", {"filter": "bogus"})
    request.session = "session"
    request._messages = FallbackStorage(request)

    f = CardListFilter(request)
    assert f.value is CardFilter.ALL

    stored = list(request._messages)
    assert len(stored) == 1
    assert "bogus" in str(stored[0])


def test_card_list_filter_apply_all_returns_every_row(factory):
    from log_panel.filters import CardListFilter

    request = factory.get("/")
    f = CardListFilter(request)
    assert len(f.apply(make_rows())) == 4


def test_card_list_filter_apply_errors_keeps_only_error_rows(factory):
    from log_panel.filters import CardListFilter

    request = factory.get("/", {"filter": "errors"})
    f = CardListFilter(request)
    result = f.apply(make_rows())
    assert all(r["total_errors"] > 0 for r in result)
    assert {r["logger_name"] for r in result} == {"app.err", "app.both"}


def test_card_list_filter_apply_warnings_keeps_only_warning_rows(factory):
    from log_panel.filters import CardListFilter

    request = factory.get("/", {"filter": "warnings"})
    f = CardListFilter(request)
    result = f.apply(make_rows())
    assert all(r["total_warnings"] > 0 for r in result)
    assert {r["logger_name"] for r in result} == {"app.warn", "app.both"}


def test_cards_context_selected_filter_in_context(panel_admin, factory):
    from log_panel.types import CardFilter

    request = factory.get("/", {"filter": "errors"})
    ctx = panel_admin._logger_cards_context(request, None, None)
    assert ctx["selected_filter"] is CardFilter.ERRORS


def test_cards_context_filter_applied_to_rows(panel_admin, factory):
    backend = make_backend(rows=make_rows())
    request = factory.get("/", {"filter": "errors"})
    ctx = panel_admin._logger_cards_context(request, backend, None)
    assert all(r["total_errors"] > 0 for r in ctx["logger_rows"])
    assert {r["logger_name"] for r in ctx["logger_rows"]} == {"app.err", "app.both"}


def test_table_list_filter_defaults(factory):
    from log_panel.filters import TableListFilter

    request = factory.get("/")
    f = TableListFilter(request, app_timezone=_get_tz())
    assert f.level == ""
    assert f.search == ""
    assert f.page == 1
    assert f.timestamp_from is None
    assert f.timestamp_to is None


def test_table_list_filter_reads_level_and_search(factory):
    from log_panel.filters import TableListFilter

    request = factory.get("/", {"level": "ERROR", "search": "db"})
    f = TableListFilter(request, app_timezone=_get_tz())
    assert f.level == "ERROR"
    assert f.search == "db"


def test_table_list_filter_valid_page(factory):
    from log_panel.filters import TableListFilter

    request = factory.get("/", {"page": "3"})
    f = TableListFilter(request, app_timezone=_get_tz())
    assert f.page == 3


def test_table_list_filter_invalid_page_defaults_to_1(factory):
    from log_panel.filters import TableListFilter

    request = factory.get("/", {"page": "abc"})
    f = TableListFilter(request, app_timezone=_get_tz())
    assert f.page == 1


def test_table_list_filter_page_zero_clamped_to_1(factory):
    from log_panel.filters import TableListFilter

    request = factory.get("/", {"page": "0"})
    f = TableListFilter(request, app_timezone=_get_tz())
    assert f.page == 1


def test_table_list_filter_parses_timestamps(factory):
    from log_panel.filters import TableListFilter

    request = factory.get(
        "/",
        {"timestamp_from": "2024-06-15T10:00", "timestamp_to": "2024-06-15T14:00"},
    )
    f = TableListFilter(request, app_timezone=_get_tz())
    assert f.timestamp_from is not None
    assert f.timestamp_to is not None
    assert f.timestamp_from.hour == 10
    assert f.timestamp_to.hour == 14
    assert f.timestamp_from_str == "2024-06-15T10:00"
    assert f.timestamp_to_str == "2024-06-15T14:00"


def test_table_list_filter_invalid_timestamp_returns_none(factory):
    from log_panel.filters import TableListFilter

    request = factory.get("/", {"timestamp_from": "not-a-date"})
    f = TableListFilter(request, app_timezone=_get_tz())
    assert f.timestamp_from is None


def _get_tz():
    from django.utils import timezone as django_timezone

    return django_timezone.get_default_timezone()
