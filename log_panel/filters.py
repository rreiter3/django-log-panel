from __future__ import annotations

from datetime import datetime
from typing import Literal

from django.contrib import messages
from django.http import HttpRequest

from log_panel.datetimes import to_database_datetime
from log_panel.types import CardFilter, LogLevel


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


class TableListFilter:
    """Applies the table-view filter from the request."""

    def __init__(self, request: HttpRequest, app_timezone) -> None:
        self.level: LogLevel | str = request.GET.get(key="level", default="")
        self.module: str = request.GET.get(key="module", default="")
        self.search: str = request.GET.get(key="search", default="")
        self.timestamp_from_str: str = request.GET.get(key="timestamp_from", default="")
        self.timestamp_to_str: str = request.GET.get(key="timestamp_to", default="")

        try:
            self.page: int = max(1, int(request.GET.get(key="page", default=1)))
        except (ValueError, TypeError):
            self.page = 1

        self.timestamp_from: datetime | None = self._parse_timestamp(
            value=self.timestamp_from_str, app_timezone=app_timezone
        )
        self.timestamp_to: datetime | None = self._parse_timestamp(
            value=self.timestamp_to_str, app_timezone=app_timezone
        )

    @staticmethod
    def _parse_timestamp(value: str, app_timezone) -> datetime | None:
        """Parse a ``%Y-%m-%dT%H:%M`` string into a database-compatible datetime."""
        if not value:
            return None
        try:
            return to_database_datetime(
                value=datetime.strptime(value, "%Y-%m-%dT%H:%M"),
                app_timezone=app_timezone,
            )
        except ValueError:
            return None
