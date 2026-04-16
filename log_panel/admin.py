from datetime import UTC, datetime
from math import ceil
from typing import Any

from django.contrib import admin
from django.http import HttpRequest
from django.template.response import TemplateResponse
from django.utils import timezone as django_timezone

from log_panel import conf
from log_panel.backends.base import LogsBackend
from log_panel.filters import CardListFilter, TableListFilter
from log_panel.models import Panel
from log_panel.types import RangeConfig


@admin.register(Panel)
class PanelAdmin(admin.ModelAdmin):
    """Read-only admin interface for browsing application logs.

    Provides two views:
    - **Cards view** (default): one card per logger with error/warning badges and a color-coded timeline strip across
    the selected time range.
    - **Table view**: paginated log entries for a single logger, filterable by level and message content.
    """

    def has_view_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        callback = conf.get_permission_callback()
        if (
            callback is None
            and hasattr(request, "user")
            and hasattr(request.user, "is_staff")
        ):
            return bool(request.user.is_active and request.user.is_staff)
        return callback(request)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def changelist_view(
        self, request: HttpRequest, extra_context: dict | None = None
    ) -> TemplateResponse:
        """Route to the cards or table view depending on the ``logger_name`` query param."""
        backend: LogsBackend | None = conf.get_backend()
        error: str | None = None
        logger_name: str = request.GET.get("logger_name", "")

        if logger_name:
            context = self._log_table_context(request, backend, logger_name, error)
            template = "admin/log_panel/panel/table.html"
        else:
            context = self._logger_cards_context(request, backend, error)
            template = "admin/log_panel/panel/cards.html"

        return TemplateResponse(request, template, context)

    def _logger_cards_context(
        self, request: HttpRequest, backend: LogsBackend | None, error: str | None
    ) -> dict:
        """Build context for the cards view."""
        ranges: dict[str, RangeConfig] = conf.get_ranges()
        selected_range: str = request.GET.get("range", "24h")

        if selected_range not in ranges:
            selected_range: str = next(iter(ranges))

        range_config: RangeConfig = ranges[selected_range]

        logger_rows: list[dict] = []
        if backend:
            try:
                now_utc: datetime = datetime.now(tz=UTC)
                app_timezone = django_timezone.get_default_timezone()
                logger_rows = backend.get_logger_cards(
                    now_utc=now_utc,
                    range_config=range_config,
                    app_timezone=app_timezone,
                )
            except Exception as exc:
                error = str(object=exc)

        range_label: str = range_config.label or selected_range

        card_filter = CardListFilter(request)
        logger_rows = card_filter.apply(logger_rows)

        return {
            **self.admin_site.each_context(request),
            "title": conf.get_setting(key="TITLE"),
            "opts": self.model._meta,
            "view": "cards",
            "logger_rows": logger_rows,
            "selected_range": selected_range,
            "selected_filter": card_filter.value,
            "range_label": range_label,
            "ranges": list(ranges.keys()),
            "level_colors": conf.get_level_colors(),
            "error": error,
        }

    def _log_table_context(
        self,
        request: HttpRequest,
        backend: LogsBackend | None,
        logger_name: str,
        error: str | None,
    ) -> dict:
        """Build context for the table view."""
        logs: list[dict] = []
        total: int = 0
        page_size: int = conf.get_setting(key="PAGE_SIZE")

        app_timezone = django_timezone.get_default_timezone()
        table_filter = TableListFilter(request, app_timezone)

        if backend:
            try:
                logs, total = backend.get_log_table(
                    logger_name=logger_name,
                    level=table_filter.level,
                    search=table_filter.search,
                    page=table_filter.page,
                    page_size=page_size,
                    app_timezone=app_timezone,
                    timestamp_from=table_filter.timestamp_from,
                    timestamp_to=table_filter.timestamp_to,
                )
            except Exception as exc:
                error = str(object=exc)

        total_pages: int = max(1, ceil(total / page_size))

        return {
            **self.admin_site.each_context(request),
            "title": f"{conf.get_setting(key='TITLE')} — {logger_name}",
            "opts": self.model._meta,
            "view": "table",
            "logs": logs,
            "page": table_filter.page,
            "total": total,
            "total_pages": total_pages,
            "has_prev": table_filter.page > 1,
            "has_next": table_filter.page < total_pages,
            "logger_name": logger_name,
            "level_filter": table_filter.level,
            "search": table_filter.search,
            "timestamp_from": table_filter.timestamp_from_str,
            "timestamp_to": table_filter.timestamp_to_str,
            "level_colors": conf.get_level_colors(),
            "error": error,
        }
