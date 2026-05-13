from abc import ABC, abstractmethod
from datetime import datetime, timedelta, tzinfo

from log_panel.types import LogLevel, RangeConfig, RangeUnit


class LogsBackend(ABC):
    @abstractmethod
    def get_logger_cards(
        self, now_utc: datetime, range_config: RangeConfig, app_timezone: tzinfo
    ) -> list[dict]:
        """
        Return per-logger aggregation rows for the cards view.

        Each row must contain:
        {
            'logger_name': str,
            'total': int,
            'total_errors': int,
            'total_warnings': int,
            'recent_errors': int,    # last hour
            'recent_warnings': int,  # last hour
            'last_seen': datetime,
            'timeline': [{'label': str, 'status': 'ok'|'warning'|'error'|'empty',
                          'timestamp_from': str, 'timestamp_to': str}, ...],
        }
        """

    @abstractmethod
    def query_logs(
        self,
        logger_names: list[str] | None,
        levels: list[str] | None,
        search: str,
        offset: int,
        limit: int | None,
        app_timezone: tzinfo,
        timestamp_from: datetime | None = None,
        timestamp_to: datetime | None = None,
    ) -> list[dict]:
        """
        Return log entries with flexible multi-value filters.

        Intended for use with :class:`log_panel.managers.LogReader` in
        non-admin views.  Unlike ``get_log_table``, this method accepts lists
        for both logger names and levels so that role-based visibility filters
        can restrict results across multiple loggers or a minimum severity.

        Each entry must contain at least:
        { 'timestamp': datetime (tz-aware, app timezone), 'level': str,
          'logger_name': str, 'module': str, 'message': str }
        """

    @abstractmethod
    def count_logs(
        self,
        logger_names: list[str] | None,
        levels: list[str] | None,
        search: str,
        timestamp_from: datetime | None = None,
        timestamp_to: datetime | None = None,
    ) -> int:
        """Return the total number of log entries matching the given filters."""

    @abstractmethod
    def get_log_table(
        self,
        logger_name: str,
        level: "LogLevel | str",
        search: str,
        page: int,
        page_size: int,
        app_timezone: tzinfo,
        timestamp_from: datetime | None = None,
        timestamp_to: datetime | None = None,
    ) -> tuple[list[dict], int]:
        """
        Return (log_entries, total_count) for the table view.

        Each entry must contain at least:
        { 'timestamp': datetime (tz-aware, app timezone), 'level': str,
          'module': str, 'message': str }
        """

    @staticmethod
    def get_local_now_and_slot_delta(
        now_utc: datetime,
        app_timezone: tzinfo,
        configured_unit: RangeUnit,
    ) -> tuple[datetime, timedelta]:
        """Return the current local slot boundary and its delta."""
        now_local: datetime = now_utc.astimezone(app_timezone)

        if configured_unit is RangeUnit.HOUR:
            now_bucket_local: datetime = now_local.replace(
                minute=0, second=0, microsecond=0
            )
            slot_delta: timedelta = timedelta(hours=1)
        elif configured_unit is RangeUnit.DAY:
            now_bucket_local: datetime = now_local.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            slot_delta: timedelta = timedelta(days=1)
        else:  # pragma: no cover
            raise ValueError(f"Unsupported range unit: {configured_unit!r}")

        return now_bucket_local, slot_delta
