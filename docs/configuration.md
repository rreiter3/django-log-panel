# Configuration reference

`django-log-panel` is configured through the `LOG_PANEL` Django setting.

## Backend and storage settings

| Setting | Default | Description |
| --- | --- | --- |
| `BACKEND` | `None` | Dotted path to a custom backend class. Overrides auto-detection. |
| `DATABASE_ALIAS` | `None` | Database alias for log storage (SQL or MongoDB). The backend engine is detected from the `DATABASES` entry — set `ENGINE` to `"django_mongodb_backend"` for MongoDB or any Django SQL backend for SQL. |
| `MESSAGE_PREVIEW_LENGTH` | `16384` | Number of characters kept inline on the `Log` row before larger messages are moved into ordered chunks. |
| `MESSAGE_CHUNK_SIZE` | `262144` | Number of characters stored per large-message chunk. |
| `RETENTION_DAYS` | `90` | Retention window in days. Used by the `delete_old_logs` cleanup command. |

## Capture and alert settings

| Setting | Default | Description |
| --- | --- | --- |
| `ATTACH_ROOT_HANDLER` | `True` | Auto-attach the matching `log_panel` handler to the root logger at startup. |
| `LOG_LEVEL` | `"INFO"` | Minimum level for the auto-attached handler and root logger. Only used when `ATTACH_ROOT_HANDLER` is `True`. |
| `BUFFER_SIZE` | `None` | Enable batch writes by setting this to a positive integer. When set, the auto-attached handler becomes a `BufferedDatabaseHandler` that accumulates up to this many records before flushing with a single `bulk_create`. `None` (default) keeps the original per-record `DatabaseHandler`. |
| `BUFFER_FLUSH_INTERVAL` | `2.0` | Maximum age in seconds before the next log activity flushes the current buffer. Only used when `BUFFER_SIZE` is set. |
| `BUFFER_FLUSH_LEVEL` | `"WARNING"` | Records at or above this level trigger an immediate flush regardless of buffer size. Only used when `BUFFER_SIZE` is set. |
| `IGNORED_LOGGER_PREFIXES` | `("pymongo",)` | Logger namespaces skipped by `DatabaseHandler`, including child loggers. User values extend the default. |
| `IGNORED_LOGGER_NAMES` | `()` | Exact logger names skipped by `DatabaseHandler`. |
| `IGNORED_MESSAGE_SUBSTRINGS` | `()` | Message substrings skipped by `DatabaseHandler`. Useful when a project SQL logger emits noisy third-party queries. |
| `THRESHOLDS` | `{"WARNING": 1, "ERROR": 1, "CRITICAL": 1}` | Per-level alert thresholds for the `log_threshold_reached` signal. Omit a level to keep its default. Set a level to `None` to disable it. |

!!! note "Ignored loggers"
    `DatabaseHandler` silently skips records from `pymongo` and `pymongo.*` loggers by default. pymongo's background monitor thread emits DEBUG logs during connection setup, which would cause recursive writes back to MongoDB. Django database and SQL loggers are still captured.

    Add noisy application or third-party loggers through `LOG_PANEL`:

    ```python
    LOG_PANEL = {
        "IGNORED_LOGGER_PREFIXES": ("silk",),
        "IGNORED_LOGGER_NAMES": ("myapp.single_noisy_logger",),
    }
    ```

    Prefixes are namespace-aware: `silk` matches `silk` and `silk.middleware`, but not `silky`.

    For django-silk, SQL query logs will be heavy if `LOG_LEVEL` is `DEBUG`. Ignore the Silk table prefix in the message:

    ```python
    LOG_PANEL = {
        "IGNORED_MESSAGE_SUBSTRINGS": ("silk_",),
    }
    ```

    This skips logged queries for tables such as `"silk_request"` and `"silk_response"`.

## Admin UI and access settings

| Setting | Default | Description |
| --- | --- | --- |
| `TITLE` | `"Log Panel"` | Page title shown in the admin UI. |
| `PAGE_SIZE` | `10` | Rows per page in the detail table. |
| `RANGES` | `{"24h": ..., "30d": ..., "90d": ...}` | Timeline ranges shown on the dashboard cards. |
| `CACHE_TIMEOUT_SECONDS` | `30` | Django cache timeout for admin dashboard logger cards. Set to `None` to disable card caching. |
| `LEVEL_COLORS` | see below | Hex colors for log level badges in the table view. Merged with defaults, so you only need to override the levels you want to change. |
| `PERMISSION_CALLBACK` | `None` | Dotted path to a callable `(request) -> bool`. When unset, any active staff user may view the panel. |

## `LEVEL_COLORS`

Default colors for Python's standard log levels:

| Level | Default color |
| --- | --- |
| `NOTSET` | `#888` |
| `DEBUG` | `#888` |
| `INFO` | `#417690` |
| `WARNING` | `#c0a000` |
| `ERROR` | `#c47900` |
| `CRITICAL` | `#ba2121` |

Override individual levels or add custom ones:

```python
LOG_PANEL = {
    "LEVEL_COLORS": {
        "CRITICAL": "#9b00d3",
        "MY_AUDIT": "#0055aa",
    },
}
```

Any level without an entry falls back to gray.

Custom log levels are supported. If you register one with Python's `logging` module and add it to `LEVEL_COLORS`, it will appear in the admin filter dropdown.

```python
import logging

MY_AUDIT = 25
logging.addLevelName(MY_AUDIT, "MY_AUDIT")

LOG_PANEL = {
    "LEVEL_COLORS": {
        "MY_AUDIT": "#0055aa",
    },
}
```

`logger.exception()` still stores the record at `ERROR` level, not a separate `EXCEPTION` level.

## `PERMISSION_CALLBACK`

Use `PERMISSION_CALLBACK` when the default `is_staff` check is too broad.

```python
LOG_PANEL = {
    "PERMISSION_CALLBACK": "myapp.utils.can_view_logs",
}
```

The callback receives the current `HttpRequest` and must return `True` to grant access.

```python
def can_view_logs(request):
    return request.user.is_superuser
```

```python
def can_view_logs(request):
    return request.user.groups.filter(name="log-viewers").exists()
```

## Custom `RANGES`

Override the default dashboard ranges by providing `RangeConfig` instances or plain dictionaries.

```python
from datetime import timedelta

from log_panel.types import RangeConfig, RangeUnit

LOG_PANEL = {
    "RANGES": {
        "1h": RangeConfig(
            delta=timedelta(hours=1),
            unit=RangeUnit.HOUR,
            slots=12,
            format="%H:%M",
            label="Last hour",
        ),
        "7d": RangeConfig(
            delta=timedelta(days=7),
            unit=RangeUnit.DAY,
            slots=7,
            format="%b %d",
            label="Last 7 days",
        ),
    },
}
```

## See also

- [Backend setup](backends.md)
- [Advanced topics](advanced.md)
