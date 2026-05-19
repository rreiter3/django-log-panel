from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.admin import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.cache import cache
from django.test import RequestFactory, override_settings

from log_panel import conf
from log_panel.admin import LogAdmin
from log_panel.filters import CardListFilter, LevelFilter, LoggerNameFilter
from log_panel.models import Log, LogMessageChunk
from log_panel.types import CardFilter, RangeConfig, RangeUnit


@pytest.fixture
def panel_admin():
    admin = LogAdmin(Log, AdminSite())
    admin.admin_site.each_context = lambda req: {}  # ty: ignore[invalid-assignment]
    return admin


@pytest.fixture
def factory():
    return RequestFactory()


@pytest.fixture(autouse=True)
def clear_cards_cache():
    cache.clear()
    yield
    cache.clear()


def make_rows():
    """Return a mix of logger rows with varying error/warning counts."""
    return [
        {"logger_name": "app.clean", "total_errors": 0, "total_warnings": 0},
        {"logger_name": "app.warn", "total_errors": 0, "total_warnings": 3},
        {"logger_name": "app.err", "total_errors": 5, "total_warnings": 0},
        {"logger_name": "app.both", "total_errors": 2, "total_warnings": 1},
    ]


def make_backend(rows=None, total_cards=None):
    backend = MagicMock()
    r = rows or []
    tc = total_cards if total_cards is not None else len(r)
    backend.get_logger_cards.return_value = (r, tc)
    return backend


@pytest.mark.django_db
def test_changelist_view_without_params_renders_cards_view(panel_admin, factory):
    request = factory.get("/admin/log_panel/log/")
    with patch("log_panel.admin.conf.get_backend", return_value=None):
        response = panel_admin.changelist_view(request)
    assert response.context_data["view"] == "cards"


@pytest.mark.django_db
def test_changelist_view_with_cards_params_renders_cards_view(panel_admin, factory):
    request = factory.get("/admin/log_panel/log/", {"range": "7d", "filter": "errors"})
    with patch("log_panel.admin.conf.get_backend", return_value=None):
        response = panel_admin.changelist_view(request)
    assert response.context_data["view"] == "cards"


def test_list_display_configured(panel_admin):
    assert panel_admin.list_display == (
        "timestamp",
        "level",
        "logger_name",
        "module",
        "short_message",
    )


def test_list_filter_configured(panel_admin):
    assert panel_admin.list_filter == (LevelFilter, LoggerNameFilter)


def test_search_fields_configured(panel_admin):
    assert panel_admin.search_fields == ("message", "message_chunks__text")


def test_ordering_configured(panel_admin):
    assert panel_admin.ordering == ("-timestamp",)


@pytest.mark.django_db
def test_short_message_plain(panel_admin, panel_factory):
    log = panel_factory(message="simple text", message_chunked=False)
    assert panel_admin.short_message(log) == "simple text"


@pytest.mark.django_db
def test_short_message_chunked_contains_link(panel_admin, panel_factory):
    log = panel_factory(
        message="preview",
        message_chunked=True,
        message_size=5000,
    )
    with patch(
        "log_panel.admin.reverse",
        return_value=f"/admin/log_panel/log/{log.pk}/message/",
    ):
        result = panel_admin.short_message(log)
    assert "preview" in result
    assert "full message" in result
    assert "5000 chars" in result
    assert str(log.pk) in result


def test_get_urls_includes_message_view(panel_admin):
    urls = panel_admin.get_urls()
    assert any(url.name == "log_panel_log_message" for url in urls)


@pytest.mark.django_db
def test_message_view_renders_full_chunked_message(
    panel_admin,
    factory,
    panel_factory,
):
    log = panel_factory(
        logger_name="myapp",
        message="preview",
        message_chunked=True,
    )
    LogMessageChunk.objects.create(log=log, index=0, text="full ")
    LogMessageChunk.objects.create(log=log, index=1, text="message")
    request = factory.get(f"/admin/log_panel/log/{log.pk}/message/")

    response = panel_admin.message_view(request, object_id=str(log.pk))

    assert response.template_name == "admin/log_panel/panel/message.html"
    assert response.context_data["log"] == log
    assert response.context_data["log_message"] == "full message"
    assert response.context_data["level_colors"] == conf.get_level_colors()


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


def test_logger_cards_context_uses_cache_when_timeout_configured(panel_admin, factory):
    backend = make_backend(rows=[{"logger_name": "myapp"}])
    request = factory.get("/")

    def get_setting(key):
        if key == "CACHE_TIMEOUT_SECONDS":
            return 30
        return conf.DEFAULTS[key]

    with patch("log_panel.admin.conf.get_setting", side_effect=get_setting):
        with patch("log_panel.admin.cache.get") as mock_get:
            mock_get.return_value = ([{"logger_name": "cached"}], 1)
            ctx = panel_admin._logger_cards_context(
                request, backend=backend, error=None
            )

    assert ctx["logger_rows"] == [{"logger_name": "cached"}]
    backend.get_logger_cards.assert_not_called()
    mock_get.assert_called_once()


def test_logger_cards_context_bypasses_cache_when_timeout_is_none(panel_admin, factory):
    backend = make_backend(rows=[{"logger_name": "myapp"}])
    request = factory.get("/")

    def get_setting(key):
        if key == "CACHE_TIMEOUT_SECONDS":
            return None
        return conf.DEFAULTS[key]

    with patch("log_panel.admin.conf.get_setting", side_effect=get_setting):
        with patch("log_panel.admin.cache.get") as mock_get:
            ctx = panel_admin._logger_cards_context(
                request, backend=backend, error=None
            )

    assert ctx["logger_rows"] == [{"logger_name": "myapp"}]
    backend.get_logger_cards.assert_called_once()
    mock_get.assert_not_called()


def test_logger_cards_context_backend_exception_sets_error(panel_admin, factory):
    backend = MagicMock()
    backend.get_logger_cards.side_effect = RuntimeError("connection failed")
    request = factory.get("/")
    ctx = panel_admin._logger_cards_context(request, backend=backend, error=None)
    assert ctx["logger_rows"] == []
    assert "connection failed" in ctx["error"]


def test_logger_cards_context_includes_level_colors(panel_admin, factory):
    request = factory.get("/")
    ctx = panel_admin._logger_cards_context(request, None, None)
    assert "level_colors" in ctx
    assert "ERROR" in ctx["level_colors"]


@override_settings(LOG_PANEL={"LEVEL_COLORS": {"ERROR": "#ff0000"}})
def test_logger_cards_context_reflects_custom_level_colors(panel_admin, factory):
    request = factory.get("/")
    ctx = panel_admin._logger_cards_context(request, None, None)
    assert ctx["level_colors"]["ERROR"] == "#ff0000"
    assert ctx["level_colors"]["WARNING"] == "#c0a000"


def test_cards_context_selected_filter_in_context(panel_admin, factory):
    request = factory.get("/", {"filter": "errors"})
    ctx = panel_admin._logger_cards_context(request, None, None)
    assert ctx["selected_filter"] is CardFilter.ERRORS


def test_cards_context_filter_applied_to_rows(panel_admin, factory):
    backend = make_backend(
        rows=[
            {"logger_name": "app.err", "total_errors": 5, "total_warnings": 0},
            {"logger_name": "app.both", "total_errors": 2, "total_warnings": 1},
        ]
    )
    request = factory.get("/", {"filter": "errors"})
    ctx = panel_admin._logger_cards_context(request, backend, None)
    _, kwargs = backend.get_logger_cards.call_args
    assert kwargs["card_filter"] is CardFilter.ERRORS
    assert {r["logger_name"] for r in ctx["logger_rows"]} == {"app.err", "app.both"}


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


def test_card_list_filter_defaults_to_all(factory):
    request = factory.get("/")
    f = CardListFilter(request)
    assert f.value is CardFilter.ALL


def test_card_list_filter_accepts_errors(factory):
    request = factory.get("/", {"filter": "errors"})
    f = CardListFilter(request)
    assert f.value is CardFilter.ERRORS


def test_card_list_filter_accepts_warnings(factory):
    request = factory.get("/", {"filter": "warnings"})
    f = CardListFilter(request)
    assert f.value is CardFilter.WARNINGS


def test_card_list_filter_unknown_falls_back_with_message(factory):
    request = factory.get("/", {"filter": "bogus"})
    request.session = "session"
    request._messages = FallbackStorage(request)

    f = CardListFilter(request)
    assert f.value is CardFilter.ALL

    stored = list(request._messages)
    assert len(stored) == 1
    assert "bogus" in str(stored[0])


def test_card_list_filter_apply_all_returns_every_row(factory):
    request = factory.get("/")
    f = CardListFilter(request)
    assert len(f.apply(make_rows())) == 4


def test_card_list_filter_apply_errors_keeps_only_error_rows(factory):
    request = factory.get("/", {"filter": "errors"})
    f = CardListFilter(request)
    result = f.apply(make_rows())
    assert all(r["total_errors"] > 0 for r in result)
    assert {r["logger_name"] for r in result} == {"app.err", "app.both"}


def test_card_list_filter_apply_warnings_keeps_only_warning_rows(factory):
    request = factory.get("/", {"filter": "warnings"})
    f = CardListFilter(request)
    result = f.apply(make_rows())
    assert all(r["total_warnings"] > 0 for r in result)
    assert {r["logger_name"] for r in result} == {"app.warn", "app.both"}
