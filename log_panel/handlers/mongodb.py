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

    def get_collection(self) -> Any:
        """Return a PyMongo Collection, connecting and creating indexes on each call.

        Raises:
            PyMongoNotInstalled: If the pymongo package is not installed.
            MongoDBConnectionError: If the server is unreachable within the timeout.
            ValueError: If no connection string is configured.
        """
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
        if not conn_str or not (isinstance(conn_str, str) and conn_str.strip()):
            from django.core.exceptions import ImproperlyConfigured

            raise ImproperlyConfigured(
                'log_panel.handlers.MongoDBHandler requires LOG_PANEL["CONNECTION_STRING"] '
                "to be a valid MongoDB connection URI."
            )
        conn_str = conn_str.strip()

        db_name: str = get_setting(key="DB_NAME")
        collection_name: str = get_setting(key="COLLECTION")
        ttl_days: int = get_setting(key="TTL_DAYS")
        timeout_ms: int = get_setting(key="SERVER_SELECTION_TIMEOUT_MS")

        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                client = MongoClient(conn_str, serverSelectionTimeoutMS=timeout_ms)
                client.admin.command("ping")
                break
            except ServerSelectionTimeoutError as exc:
                last_exc = exc
                if attempt < 4:
                    import time

                    time.sleep(0.5 * 2**attempt)
        else:
            assert last_exc is not None
            raise MongoDBConnectionError(conn_str, last_exc) from last_exc

        collection = client[db_name][collection_name]
        collection.create_index(
            [("timestamp", ASCENDING)],
            expireAfterSeconds=ttl_days * 24 * 3600,
            name="ttl_index",
        )
        collection.create_index(
            [
                ("timestamp", ASCENDING),
                ("logger_name", ASCENDING),
                ("level", ASCENDING),
            ],
            name="timestamp_logger_level_idx",
        )
        collection.create_index(
            [("logger_name", ASCENDING), ("timestamp", -1)],
            name="logger_name_timestamp_idx",
        )
        return collection

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
