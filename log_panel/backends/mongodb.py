from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from log_panel import conf
from log_panel.backends.base import LogsBackend
from log_panel.exceptions.mongodb import MongoDBConnectionError, PyMongoNotInstalled
from log_panel.types import LogLevel, RangeConfig, SlotStatus

try:
    from pymongo import MongoClient
    from pymongo.errors import ServerSelectionTimeoutError
except ImportError as exc:  # pragma: no cover
    raise PyMongoNotInstalled() from exc  # pragma: no cover


MAX_CONNECTION_RETRIES: int = 5
RETRY_BASE_DELAY: float = 0.5


class MongoDBBackend(LogsBackend):
    """Read log data from a MongoDB collection using aggregation pipelines."""

    def __init__(
        self,
        connection_string: str,
        db_name: str = "log_panel",
        collection: str = "logs",
        server_selection_timeout_ms: int = 2000,
        allow_disk_use: bool = False,
    ) -> None:
        """Initialise the backend.

        Args:
            connection_string: MongoDB connection URI.
            db_name: MongoDB database name.
            collection: MongoDB collection name.
            server_selection_timeout_ms: Milliseconds to wait for a server response
                before raising ``MongoDBConnectionError``. Controlled via
                ``LOG_PANEL["SERVER_SELECTION_TIMEOUT_MS"]`` (default: ``2000``).
            allow_disk_use: Pass ``allowDiskUse=True`` to aggregation pipelines.
                Useful for large collections where in-memory sort/group limits are
                hit. Controlled via ``LOG_PANEL["ALLOW_DISK_USE"]`` (default: ``False``).
        """
        self.connection_string: str = connection_string
        self.db_name: str = db_name
        self.collection_name: str = collection
        self.server_selection_timeout_ms: int = server_selection_timeout_ms
        self.allow_disk_use: bool = allow_disk_use
        self._client: MongoClient | None = None
        self._collection: Any = None

    def get_collection(self) -> Any:
        """Return a cached PyMongo Collection object for the configured database/collection.

        The client is created lazily on the first call and reused.

        Retries the connection up to ``MAX_CONNECTION_RETRIES`` times with
        exponential backoff before giving up.

        Raises:
            PyMongoNotInstalled: If the pymongo package is not installed.
            MongoDBConnectionError: If the server is unreachable after all retries.
        """
        if self._collection is not None:
            return self._collection

        import time

        last_exc: ServerSelectionTimeoutError | None = None
        for attempt in range(MAX_CONNECTION_RETRIES):
            try:
                client: MongoClient = MongoClient(
                    self.connection_string,
                    serverSelectionTimeoutMS=self.server_selection_timeout_ms,
                )
                client.admin.command("ping")
                self._client = client
                self._collection = client[self.db_name][self.collection_name]
                return self._collection
            except ServerSelectionTimeoutError as exc:
                last_exc = exc
                if attempt < MAX_CONNECTION_RETRIES - 1:
                    time.sleep(RETRY_BASE_DELAY * 2**attempt)

        assert last_exc is not None
        raise MongoDBConnectionError(self.connection_string, last_exc) from last_exc

    def get_logger_cards(
        self, now_utc: datetime, range_config: RangeConfig, app_timezone: tzinfo
    ) -> list[dict]:
        """Orchestrate per-logger stat aggregation and timeline assembly.

        Delegates to private helpers for pipeline construction, timeline
        aggregation, and row assembly so each step can be understood and
        tested independently.
        """
        collection: Any = self.get_collection()
        app_timezone_name: str = str(app_timezone)

        one_hour_ago: datetime = now_utc - timedelta(hours=1)
        cutoff: datetime = now_utc - range_config.delta
        now_bucket_local, slot_delta = self.get_local_now_and_slot_delta(
            now_utc=now_utc,
            app_timezone=app_timezone,
            configured_unit=range_config.unit,
        )

        slots_utc_naive, slot_labels, slots_local = self._build_slots(
            now_bucket_local=now_bucket_local,
            slot_delta=slot_delta,
            range_config=range_config,
        )

        pipeline: list = self._build_timeline_pipeline(
            cutoff=cutoff,
            unit_value=range_config.unit.value,
            app_timezone_name=app_timezone_name,
        )
        pipeline_one_hour_ago: list = self._build_cards_pipeline(one_hour_ago)

        thresholds: dict[str, int | None] = conf.get_thresholds()
        error_threshold: int = thresholds.get("ERROR") or 1
        warning_threshold: int = thresholds.get("WARNING") or 1

        timeline: dict = self._aggregate_timeline(
            collection=collection,
            pipeline=pipeline,
            allow_disk_use=self.allow_disk_use,
            error_threshold=error_threshold,
            warning_threshold=warning_threshold,
        )

        return self._assemble_rows(
            cards_cursor=collection.aggregate(
                pipeline_one_hour_ago, allowDiskUse=self.allow_disk_use
            ),
            timeline_by_logger=timeline,
            slot_labels=slot_labels,
            slots_utc_naive=slots_utc_naive,
            slots_count=range_config.slots,
            slots_local=slots_local,
            slot_delta=slot_delta,
        )

    def _build_log_query(
        self,
        logger_names: list[str] | None,
        levels: list[str] | None,
        search: str,
        timestamp_from: datetime | None,
        timestamp_to: datetime | None,
    ) -> dict:
        query: dict = {}
        if logger_names is not None:
            query["logger_name"] = {"$in": logger_names}
        if levels is not None:
            query["level"] = {"$in": levels}
        if search:
            query["message"] = {"$regex": search, "$options": "i"}
        ts_filter: dict = {}
        if timestamp_from:
            ts_filter["$gte"] = timestamp_from.astimezone(UTC).replace(tzinfo=None)
        if timestamp_to:
            ts_filter["$lt"] = timestamp_to.astimezone(UTC).replace(tzinfo=None)
        if ts_filter:
            query["timestamp"] = ts_filter
        return query

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
        collection: Any = self.get_collection()
        query: dict = self._build_log_query(
            logger_names, levels, search, timestamp_from, timestamp_to
        )
        cursor: Any = collection.find(query).sort("timestamp", -1).skip(offset)
        if limit is not None:
            cursor = cursor.limit(limit)
        return [
            {
                **doc,
                "_id": str(doc["_id"]),
                "timestamp": doc["timestamp"]
                .replace(tzinfo=UTC)
                .astimezone(app_timezone),
            }
            for doc in cursor
        ]

    def count_logs(
        self,
        logger_names: list[str] | None,
        levels: list[str] | None,
        search: str,
        timestamp_from: datetime | None = None,
        timestamp_to: datetime | None = None,
    ) -> int:
        collection: Any = self.get_collection()
        query: dict = self._build_log_query(
            logger_names, levels, search, timestamp_from, timestamp_to
        )
        return collection.count_documents(query)

    def get_log_table(
        self,
        logger_name: str,
        level: LogLevel | str,
        search: str,
        page: int,
        page_size: int,
        app_timezone: tzinfo,
        timestamp_from: datetime | None = None,
        timestamp_to: datetime | None = None,
    ) -> tuple[list[dict], int]:
        """Query individual log entries with optional level and regex message filters."""
        collection: Any = self.get_collection()

        query: dict = {"logger_name": logger_name}
        if level:
            query["level"] = level
        if search:
            query["message"] = {"$regex": search, "$options": "i"}
        ts_filter: dict = {}
        if timestamp_from:
            ts_filter["$gte"] = timestamp_from.astimezone(UTC).replace(tzinfo=None)
        if timestamp_to:
            ts_filter["$lt"] = timestamp_to.astimezone(UTC).replace(tzinfo=None)
        if ts_filter:
            query["timestamp"] = ts_filter

        total: int = collection.count_documents(query)
        skip: int = (page - 1) * page_size
        cursor: Any = (
            collection.find(query).sort("timestamp", -1).skip(skip).limit(page_size)
        )

        logs: list[dict] = [
            {
                **doc,
                "_id": str(doc["_id"]),
                "timestamp": doc["timestamp"]
                .replace(tzinfo=UTC)
                .astimezone(app_timezone),
            }
            for doc in cursor
        ]
        return logs, total

    @staticmethod
    def _build_slots(
        now_bucket_local: datetime,
        slot_delta: timedelta,
        range_config: RangeConfig,
    ) -> tuple[list[datetime], list[str], list[datetime]]:
        """Build parallel lists of slot boundaries (naive UTC), display labels, and local datetimes.

        Naive UTC values match the ``$dateTrunc`` output so they can be used
        directly as dict keys when indexing ``timeline_by_logger``.
        Local datetimes are used to build ISO timestamp strings for slot links.
        """
        slots_utc_naive: list[datetime] = []
        slot_labels: list[str] = []
        slots_local: list[datetime] = []
        for i in range(range_config.slots - 1, -1, -1):
            slot_local = now_bucket_local - slot_delta * i
            slots_utc_naive.append(slot_local.astimezone(tz=UTC).replace(tzinfo=None))
            slot_labels.append(slot_local.strftime(range_config.format))
            slots_local.append(slot_local)
        return slots_utc_naive, slot_labels, slots_local

    @staticmethod
    def _build_cards_pipeline(one_hour_ago: datetime) -> list[dict]:
        """Return the $group pipeline for all-time and last-hour per-logger counts."""
        return [
            {
                "$group": {
                    "_id": "$logger_name",
                    "total": {"$sum": 1},
                    "total_errors": {
                        "$sum": {
                            "$cond": [{"$in": ["$level", ["ERROR", "CRITICAL"]]}, 1, 0]
                        }
                    },
                    "total_warnings": {
                        "$sum": {"$cond": [{"$eq": ["$level", "WARNING"]}, 1, 0]}
                    },
                    "recent_errors": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$in": ["$level", ["ERROR", "CRITICAL"]]},
                                        {"$gte": ["$timestamp", one_hour_ago]},
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                    "recent_warnings": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$eq": ["$level", "WARNING"]},
                                        {"$gte": ["$timestamp", one_hour_ago]},
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                    "last_seen": {"$max": "$timestamp"},
                }
            },
            {"$sort": {"last_seen": -1}},
        ]

    @staticmethod
    def _build_timeline_pipeline(
        cutoff: datetime, unit_value: str, app_timezone_name: str
    ) -> list[dict]:
        """Return the $dateTrunc pipeline for bucketed error/warning presence per logger."""
        return [
            {"$match": {"timestamp": {"$gte": cutoff}}},
            {
                "$group": {
                    "_id": {
                        "logger": "$logger_name",
                        "bucket": {
                            "$dateTrunc": {
                                "date": "$timestamp",
                                "unit": unit_value,
                                "timezone": app_timezone_name,
                            }
                        },
                    },
                    "has_error": {
                        "$sum": {
                            "$cond": [{"$in": ["$level", ["ERROR", "CRITICAL"]]}, 1, 0]
                        }
                    },
                    "has_warning": {
                        "$sum": {"$cond": [{"$eq": ["$level", "WARNING"]}, 1, 0]}
                    },
                }
            },
        ]

    @staticmethod
    def _aggregate_timeline(
        collection: Any,
        pipeline: list[dict],
        allow_disk_use: bool = False,
        error_threshold: int = 1,
        warning_threshold: int = 1,
    ) -> dict[str, dict[datetime, SlotStatus]]:
        """Run the timeline pipeline and return ``{logger: {naive_utc_bucket: status}}``."""
        timeline_by_logger: dict[str, dict[datetime, SlotStatus]] = defaultdict(dict)
        for entry in collection.aggregate(pipeline, allowDiskUse=allow_disk_use):
            logger: str = entry["_id"]["logger"]
            bucket: datetime = entry["_id"]["bucket"]
            if bucket.tzinfo is not None:
                bucket: Any = bucket.replace(tzinfo=None)
            status: SlotStatus = (
                SlotStatus.ERROR
                if entry["has_error"] >= error_threshold
                else (
                    SlotStatus.WARNING
                    if entry["has_warning"] >= warning_threshold
                    else SlotStatus.OK
                )
            )
            timeline_by_logger[logger][bucket] = status
        return timeline_by_logger

    @staticmethod
    def _assemble_rows(
        cards_cursor: Any,
        timeline_by_logger: dict[str, dict[datetime, SlotStatus]],
        slot_labels: list[str],
        slots_utc_naive: list[datetime],
        slots_count: int,
        slots_local: list[datetime],
        slot_delta: timedelta,
    ) -> list[dict]:
        """Combine cards aggregation results with timeline slots into final row dicts."""
        rows: list[dict] = []
        for doc in cards_cursor:
            logger_name: str = doc["_id"]
            slots: list[dict[str, str]] = [
                {
                    "label": slot_labels[i],
                    "status": timeline_by_logger[logger_name].get(
                        slots_utc_naive[i], SlotStatus.EMPTY
                    ),
                    "timestamp_from": slots_local[i].strftime("%Y-%m-%dT%H:%M"),
                    "timestamp_to": (slots_local[i] + slot_delta).strftime(
                        "%Y-%m-%dT%H:%M"
                    ),
                }
                for i in range(slots_count)
            ]
            rows.append(
                {
                    "logger_name": logger_name,
                    "total": doc["total"],
                    "total_errors": doc["total_errors"],
                    "total_warnings": doc["total_warnings"],
                    "recent_errors": doc["recent_errors"],
                    "recent_warnings": doc["recent_warnings"],
                    "last_seen": doc["last_seen"],
                    "timeline": slots,
                }
            )
        return rows
