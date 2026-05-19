from datetime import UTC, datetime
from math import ceil
from typing import Any

from django.contrib import admin
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone as django_timezone
from django.utils.html import format_html

from log_panel import conf
from log_panel.backends.base import LogsBackend
from log_panel.filters import CardListFilter, LevelFilter, LoggerNameFilter
from log_panel.models import Log
from log_panel.types import RangeConfig

CARDS_PARAMS: frozenset[str] = frozenset({"range", "filter", "cards_page"})


@admin.register(Log)
class LogAdmin(admin.ModelAdmin):
    """
    Read-only admin interface for browsing application logs.

    Provides two views:
    - Cards view (default): one card per logger with error/warning badges and a
      color-coded timeline strip across the selected time range.
    - Table view: standard Django changelist with filters and search, shown when
      any admin filter or search parameter is present.
    """

    list_display = ("timestamp", "level", "logger_name", "module", "short_message")
    list_filter = (LevelFilter, LoggerNameFilter)
    search_fields = ("message", "message_chunks__text")
    ordering = ("-timestamp",)
    show_full_result_count = False

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
        return False  # pragma: no cover

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False  # pragma: no cover

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False  # pragma: no cover

    def changelist_view(
        self, request: HttpRequest, extra_context: dict | None = None
    ) -> HttpResponse:
        """Show the cards dashboard unless Django admin filters/search are active."""
        if set(request.GET.keys()).issubset(CARDS_PARAMS):
            backend: LogsBackend | None = conf.get_backend()
            context: dict = self._logger_cards_context(request, backend, error=None)
            return TemplateResponse(
                request, "admin/log_panel/panel/cards.html", context
            )

        self.list_per_page: Any = conf.get_setting(
            key="TABLE_PAGE_SIZE"
        )  # pragma: no cover
        return super().changelist_view(request, extra_context)  # pragma: no cover

    def get_urls(self) -> list:
        """Add a read-only full-message view for chunked log payloads."""
        return [
            path(
                "<path:object_id>/message/",
                self.admin_site.admin_view(self.message_view),
                name="log_panel_log_message",
            ),
            *super().get_urls(),
        ]

    def message_view(self, request: HttpRequest, object_id: str) -> TemplateResponse:
        """Render the complete log message for a single log entry."""
        log: Log = get_object_or_404(
            Log.objects.prefetch_related("message_chunks"),
            pk=object_id,
        )
        return TemplateResponse(
            request,
            "admin/log_panel/panel/message.html",
            {
                **self.admin_site.each_context(request),
                "title": f"{conf.get_setting(key='TITLE')} — {log.logger_name}",
                "opts": self.model._meta,
                "log": log,
                "log_message": log.get_full_message(),
                "level_colors": conf.get_level_colors(),
            },
        )

    @admin.display(description="Message")
    def short_message(self, obj: Log) -> str:
        """Render the message preview with a link to the full payload for chunked entries."""
        if obj.message_chunked:
            url: str = reverse(viewname="admin:log_panel_log_message", args=[obj.pk])
            return format_html(
                '{} <a href="{}">&#91;full message ({} chars)&#93;</a>',
                obj.message,
                url,
                obj.message_size,
            )
        return obj.message

    def _logger_cards_context(
        self, request: HttpRequest, backend: LogsBackend | None, error: str | None
    ) -> dict:
        """Build context for the cards view."""
        ranges: dict[str, RangeConfig] = conf.get_ranges()
        selected_range: str = request.GET.get("range", "24h")

        if selected_range not in ranges:
            selected_range: str = next(iter(ranges))

        range_config: RangeConfig = ranges[selected_range]

        card_filter = CardListFilter(request)
        cards_page_size: int = conf.get_cards_page_size()

        try:
            cards_page: int = max(1, int(request.GET.get("cards_page", 1)))
        except (ValueError, TypeError):  # pragma: no cover
            cards_page = 1

        logger_rows: list[dict] = []
        total_cards: int = 0
        if backend:
            try:
                now_utc: datetime = datetime.now(tz=UTC)
                app_timezone = django_timezone.get_default_timezone()
                timeout: int | None = conf.get_setting("CACHE_TIMEOUT_SECONDS")
                cache_key: str = (
                    f"log_panel:cards:{selected_range}:{card_filter.value}:{cards_page}"
                )
                if timeout is not None:
                    cached = cache.get(cache_key)
                    if cached is not None:
                        logger_rows, total_cards = cached
                    else:
                        logger_rows, total_cards = backend.get_logger_cards(
                            now_utc=now_utc,
                            range_config=range_config,
                            app_timezone=app_timezone,
                            page=cards_page,
                            page_size=cards_page_size,
                            card_filter=card_filter.value,
                        )
                        cache.set(cache_key, (logger_rows, total_cards), timeout)
                else:
                    logger_rows, total_cards = backend.get_logger_cards(
                        now_utc=now_utc,
                        range_config=range_config,
                        app_timezone=app_timezone,
                        page=cards_page,
                        page_size=cards_page_size,
                        card_filter=card_filter.value,
                    )
            except Exception as exc:
                error = str(object=exc)

        range_label: str = range_config.label or selected_range
        total_card_pages: int = max(1, ceil(total_cards / cards_page_size))

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
            "cards_page": cards_page,
            "total_cards": total_cards,
            "total_card_pages": total_card_pages,
            "has_prev_cards": cards_page > 1,
            "has_next_cards": cards_page < total_card_pages,
        }
