from __future__ import annotations

from datetime import datetime

from django.contrib import messages
from django.http import HttpRequest

from log_panel.types import CardFilter, LogLevel


class CardListFilter:
    """Applies the card-level filter from the request."""

    parameter_name: str = "filter"
    title: str = "Filters"

    def __init__(self, request: HttpRequest) -> None:
        filter: str = request.GET.get(self.parameter_name, "")
        valid_values = {f.value for f in CardFilter}

        if filter in valid_values:
            self.value: CardFilter = CardFilter(filter)
        else:
            self.value = CardFilter.ALL
            if filter:
                messages.warning(
                    request,
                    f"Unknown filter '{filter}', fall back to 'All'.",
                )

    def apply(self, rows: list[dict]) -> list[dict]:
        """Return only the rows matching the selected filter."""
        if self.value is CardFilter.ERRORS:
            return [r for r in rows if r.get("total_errors", 0) > 0]
        if self.value is CardFilter.WARNINGS:
            return [r for r in rows if r.get("total_warnings", 0) > 0]
        return rows


class TableListFilter:
    """Applies the table-view filter from the request."""

    def __init__(self, request: HttpRequest, app_timezone) -> None:
        self.level: LogLevel | str = request.GET.get("level", "")
        self.search: str = request.GET.get("search", "")
        self.timestamp_from_str: str = request.GET.get("timestamp_from", "")
        self.timestamp_to_str: str = request.GET.get("timestamp_to", "")

        try:
            self.page: int = max(1, int(request.GET.get("page", 1)))
        except (ValueError, TypeError):
            self.page = 1

        self.timestamp_from: datetime | None = self._parse_timestamp(
            self.timestamp_from_str, app_timezone
        )
        self.timestamp_to: datetime | None = self._parse_timestamp(
            self.timestamp_to_str, app_timezone
        )

    @staticmethod
    def _parse_timestamp(value: str, app_timezone) -> datetime | None:
        """Parse a ``%Y-%m-%dT%H:%M`` string into a timezone-aware datetime, or *None*."""
        if not value:
            return None
        try:
            from django.utils.timezone import make_aware

            return make_aware(datetime.strptime(value, "%Y-%m-%dT%H:%M"), app_timezone)
        except ValueError:
            return None
