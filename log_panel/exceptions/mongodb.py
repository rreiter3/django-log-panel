class PyMongoNotInstalled(ImportError):
    """Raised when pymongo is not installed but a MongoDB feature is used.

    Install the optional dependency::

        pip install django-app-logs[mongodb]
        # or directly:
        pip install pymongo>=4.0
    """

    def __init__(self) -> None:
        super().__init__(
            "pymongo is required to use the MongoDB backend or MongoDBHandler. "
            'Install it with: pip install "django-app-logs[mongodb]" '
            'or: pip install "pymongo>=4.0"'
        )


class MongoDBConnectionError(ConnectionError):
    """Raised when a MongoDB client cannot reach the server within the timeout.

    Check that:
    - The connection string is correct (LOG_PANEL["CONNECTION_STRING"]).
    - The MongoDB server is running and reachable from this host.
    - No firewall or network policy is blocking the connection.
    """

    def __init__(self, connection_string: str, reason: Exception) -> None:
        self.connection_string: str = connection_string
        self.reason: Exception = reason
        super().__init__(
            f"Could not connect to MongoDB at {connection_string!r}. "
            f"Reason: {reason}. "
            f"Check that the server is running and {connection_string!r} is correct."
        )
