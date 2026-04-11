from datetime import UTC, datetime
from math import ceil
from typing import Any

from django.contrib import admin
from django.http import HttpRequest
from django.template.response import TemplateResponse
from django.utils import timezone as django_timezone

from log_panel import conf
from log_panel.backends.base import LogsBackend
from log_panel.models import Panel
from log_panel.types import LogLevel, RangeConfig


@admin.register(Panel)
class PanelAdmin(admin.ModelAdmin):
    """Read-only admin interface for browsing application logs.

    Provides two views:
    - **Cards view** (default): one card per logger with error/warning badges and a colour-coded timeline strip across
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
                logger_rows: list = backend.get_logger_cards(
                    now_utc=now_utc,
                    range_config=range_config,
                    app_timezone=app_timezone,
                )
            except Exception as exc:
                error = str(object=exc)

        range_label: str = range_config.label or selected_range

        return {
            **self.admin_site.each_context(request),
            "title": conf.get_setting(key="TITLE"),
            "opts": self.model._meta,
            "view": "cards",
            "logger_rows": logger_rows,
            "selected_range": selected_range,
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
        level_filter: LogLevel | str = request.GET.get("level", "")
        search: str = request.GET.get("search", "")
        timestamp_from_str: str = request.GET.get("timestamp_from", "")
        timestamp_to_str: str = request.GET.get("timestamp_to", "")
        try:
            page: int = max(1, int(request.GET.get("page", 1)))
        except (ValueError, TypeError):
            page = 1

        page_size: int = conf.get_setting(key="PAGE_SIZE")

        if backend:
            try:
                app_timezone = django_timezone.get_default_timezone()
                timestamp_from: datetime | None = self._parse_timestamp(
                    timestamp_from_str, app_timezone
                )
                timestamp_to: datetime | None = self._parse_timestamp(
                    timestamp_to_str, app_timezone
                )
                logs, total = backend.get_log_table(
                    logger_name=logger_name,
                    level=level_filter,
                    search=search,
                    page=page,
                    page_size=page_size,
                    app_timezone=app_timezone,
                    timestamp_from=timestamp_from,
                    timestamp_to=timestamp_to,
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
            "page": page,
            "total": total,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "logger_name": logger_name,
            "level_filter": level_filter,
            "search": search,
            "timestamp_from": timestamp_from_str,
            "timestamp_to": timestamp_to_str,
            "level_colors": conf.get_level_colors(),
            "error": error,
        }

    @staticmethod
    def _parse_timestamp(value: str, app_timezone) -> datetime | None:
        """Parse a ``%Y-%m-%dT%H:%M`` string into a timezone-aware datetime, or None."""
        if not value:
            return None
        try:
            from django.utils.timezone import make_aware

            return make_aware(datetime.strptime(value, "%Y-%m-%dT%H:%M"), app_timezone)
        except ValueError:
            return None
