from __future__ import annotations

from typing import Literal

from django.contrib import admin, messages
from django.http import HttpRequest

from log_panel.types import CardFilter, LogLevel


class LevelFilter(admin.SimpleListFilter):
    """Filter log entries by level without scanning the log table for distinct values."""

    title = "level"
    parameter_name = "level__exact"

    def lookups(self, request, model_admin):  # pragma: no cover
        return LogLevel.choices()

    def queryset(self, request, queryset):  # pragma: no cover
        if self.value():
            return queryset.filter(level=self.value())
        return queryset


class LoggerNameFilter(admin.SimpleListFilter):
    """Filter by logger name, reading choices from the small Logger table."""

    title = "logger name"
    parameter_name = "logger_name__exact"

    def lookups(self, request, model_admin):  # pragma: no cover
        from log_panel.models import Logger

        return [
            (name, name)
            for name in Logger.objects.values_list("name", flat=True).order_by("name")
        ]

    def queryset(self, request, queryset):  # pragma: no cover
        if self.value():
            return queryset.filter(logger_name=self.value())
        return queryset


class CardListFilter:
    """Applies the card-level filter from the request."""

    parameter_name: str = "filter"
    title: str = "Filters"

    def __init__(self, request: HttpRequest) -> None:
        filter: str = request.GET.get(key=self.parameter_name, default="")
        valid_values: set[str] = {f.value for f in CardFilter}

        if filter in valid_values:
            self.value: CardFilter = CardFilter(value=filter)
        else:
            self.value: Literal[CardFilter.ALL] = CardFilter.ALL
            if filter:
                messages.warning(
                    request,
                    message=f"Unknown filter '{filter}', fall back to 'All'.",
                )

    def apply(self, rows: list[dict]) -> list[dict]:
        """Return only the rows matching the selected filter."""
        if self.value is CardFilter.ERRORS:
            return [r for r in rows if r.get("total_errors", 0) > 0]
        if self.value is CardFilter.WARNINGS:
            return [r for r in rows if r.get("total_warnings", 0) > 0]
        return rows
