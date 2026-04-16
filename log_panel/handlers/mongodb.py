import threading
from datetime import UTC, datetime
from functools import partial
from logging import Formatter, Handler, LogRecord
from typing import Any

from log_panel.alerts import maybe_emit_threshold_signal


class MongoDBHandler(Handler):
    """Write log records to a MongoDB collection.

    The connection URI is read from ``LOG_PANEL['CONNECTION_STRING']``.

    Configure in Django LOGGING::

        'handlers': {
            'log_panel': {
                'class': 'log_panel.handlers.MongoDBHandler',
            }
        }
    """

    _local = threading.local()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._client: Any = None
        self._collection: Any = None
        self._indexes_ensured: bool = False

    def get_collection(self) -> Any:
        """Return a cached PyMongo Collection, connecting lazily on first call.

        Raises:
            PyMongoNotInstalled: If the pymongo package is not installed.
            MongoDBConnectionError: If the server is unreachable within the timeout.
            ValueError: If no connection string is configured.
        """
        if self._collection is not None:
            return self._collection

        from log_panel.conf import get_setting
        from log_panel.exceptions.mongodb import (
            MongoDBConnectionError,
            PyMongoNotInstalled,
        )

        try:
            from pymongo import ASCENDING, MongoClient
            from pymongo.errors import ServerSelectionTimeoutError
        except ImportError as exc:
            raise PyMongoNotInstalled() from exc

        conn_str: str | None = get_setting(key="CONNECTION_STRING")
        if not conn_str:
            raise ValueError(
                'log_panel.handlers.MongoDBHandler requires LOG_PANEL["CONNECTION_STRING"]'
            )

        db_name: str = get_setting(key="DB_NAME")
        collection_name: str = get_setting(key="COLLECTION")
        timeout_ms: int = get_setting(key="SERVER_SELECTION_TIMEOUT_MS")

        try:
            client = MongoClient(conn_str, serverSelectionTimeoutMS=timeout_ms)
            client.admin.command("ping")
        except ServerSelectionTimeoutError as exc:
            raise MongoDBConnectionError(conn_str, exc) from exc

        self._client = client
        self._collection = client[db_name][collection_name]

        if not self._indexes_ensured:
            ttl_days: int = get_setting(key="TTL_DAYS")
            self._collection.create_index(
                [("timestamp", ASCENDING)],
                expireAfterSeconds=ttl_days * 24 * 3600,
                name="ttl_index",
            )
            self._collection.create_index(
                [
                    ("timestamp", ASCENDING),
                    ("logger_name", ASCENDING),
                    ("level", ASCENDING),
                ],
                name="timestamp_logger_level_idx",
            )
            self._collection.create_index(
                [("logger_name", ASCENDING), ("timestamp", -1)],
                name="logger_name_timestamp_idx",
            )
            self._indexes_ensured = True

        return self._collection

    def close(self) -> None:
        """Close the cached MongoClient and release resources."""
        if self._client is not None:
            self._client.close()
            self._client = None
            self._collection = None
        super().close()

    @staticmethod
    def count_matching_records(
        collection: Any,
        logger_name: str,
        levels: tuple[str, ...],
        window_start: datetime,
        window_end: datetime,
    ) -> int:
        return collection.count_documents(
            {
                "logger_name": logger_name,
                "level": {"$in": list(levels)},
                "timestamp": {"$gte": window_start, "$lte": window_end},
            }
        )

    def emit(self, record: LogRecord) -> None:
        """Format and insert a log record document into MongoDB."""
        if getattr(self._local, "emitting", False):
            return
        self._local.emitting = True
        try:
            collection: Any = self.get_collection()
            doc: dict = {
                "timestamp": datetime.fromtimestamp(timestamp=record.created, tz=UTC),
                "level": record.levelname,
                "logger_name": record.name,
                "message": record.getMessage()
                + (
                    "\n" + Formatter().formatException(ei=record.exc_info)
                    if record.exc_info
                    else ""
                ),
                "module": record.module,
                "pathname": record.pathname,
                "lineno": record.lineno,
            }
            collection.insert_one(doc)
            maybe_emit_threshold_signal(
                sender=self.__class__,
                logger_name=doc["logger_name"],
                record_level=doc["level"],
                timestamp=doc["timestamp"],
                message=doc["message"],
                module=doc["module"],
                pathname=doc["pathname"],
                line_number=doc["lineno"],
                count_matching_records=partial(
                    self.count_matching_records, collection, doc["logger_name"]
                ),
            )
        except Exception:
            self.handleError(record)
        finally:
            self._local.emitting = False
