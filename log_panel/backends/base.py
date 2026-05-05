from abc import ABC, abstractmethod
from datetime import datetime, timedelta, tzinfo

from log_panel.types import LogLevel, RangeConfig, RangeUnit


class LogsBackend(ABC):
    @abstractmethod
    def get_logger_cards(
        self, now_utc: datetime, range_config: RangeConfig, app_timezone: tzinfo
    ) -> list[dict]:
        """Return per-logger aggregation rows for the cards view.

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

        Args:
            now_utc: Current UTC datetime (tz-aware).
            cfg: Range configuration used for bucketing and slot labels.
            app_timezone: Project timezone used for slot bucketing and display labels.
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
        """Return log entries with flexible multi-value filters.

        Intended for use with :class:`log_panel.managers.LogManager` in
        non-admin views.  Unlike ``get_log_table``, this method accepts lists
        for both logger names and levels so that role-based visibility filters
        can restrict results across multiple loggers or a minimum severity.

        Each entry must contain at least:
        { 'timestamp': datetime (tz-aware, app timezone), 'level': str,
          'logger_name': str, 'module': str, 'message': str }

        Args:
            logger_names: Restrict to these logger names. ``None`` means all loggers.
            levels: Restrict to these level strings. ``None`` means all levels.
            search: Case-insensitive message substring filter, or empty string to skip.
            offset: Number of entries to skip (0-based).
            limit: Maximum number of entries to return. ``None`` returns all.
            app_timezone: Project timezone for timestamp conversion.
            timestamp_from: Optional inclusive lower bound for log timestamps.
            timestamp_to: Optional exclusive upper bound for log timestamps.
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
        """Return the total number of log entries matching the given filters.

        Args:
            logger_names: Restrict to these logger names. ``None`` means all loggers.
            levels: Restrict to these level strings. ``None`` means all levels.
            search: Case-insensitive message substring filter, or empty string to skip.
            timestamp_from: Optional inclusive lower bound for log timestamps.
            timestamp_to: Optional exclusive upper bound for log timestamps.
        """

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
        """Return (log_entries, total_count) for the table view.

        Each entry must contain at least:
        { 'timestamp': datetime (tz-aware, app timezone), 'level': str,
          'module': str, 'message': str }

        Args:
            logger_name: Filter to this logger.
            level: One of the ``LogLevel`` literals (``"DEBUG"``, ``"INFO"``, etc.) or
                empty string to return all levels.
            search: Message substring/regex filter, or empty string to skip.
            page: 1-based page number.
            page_size: Number of entries per page.
            app_timezone: Project timezone for timestamp conversion.
            timestamp_from: Optional inclusive lower bound for log timestamps.
            timestamp_to: Optional exclusive upper bound for log timestamps.
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
        else:
            raise ValueError(f"Unsupported range unit: {configured_unit!r}")

        return now_bucket_local, slot_delta
