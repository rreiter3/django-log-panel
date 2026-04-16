# Configuration reference

`django-log-panel` is configured through the `LOG_PANEL` Django setting.

## Backend and storage settings

| Setting | Default | Description |
| --- | --- | --- |
| `BACKEND` | `None` | Dotted path to a custom backend class. Overrides auto-detection. |
| `CONNECTION_STRING` | `None` | MongoDB connection string. |
| `DB_NAME` | `"log_panel"` | MongoDB database name. |
| `COLLECTION` | `"logs"` | MongoDB collection name. |
| `DATABASE_ALIAS` | `None` | SQL database alias for log storage. |
| `TTL_DAYS` | `90` | Retention window in days. Used by MongoDB TTL indexes and by the SQL cleanup command. |
| `SERVER_SELECTION_TIMEOUT_MS` | `2000` | MongoDB server selection timeout in milliseconds for both the backend and handler. |
| `ALLOW_DISK_USE` | `False` | Pass `allowDiskUse=True` to MongoDB aggregation pipelines. Useful for very large collections and long reporting ranges. |

## Capture and alert settings

| Setting | Default | Description |
| --- | --- | --- |
| `ATTACH_ROOT_HANDLER` | `True` | Auto-attach the matching `log_panel` handler to the root logger at startup. |
| `LOG_LEVEL` | `"DEBUG"` | Minimum level for the auto-attached handler and root logger. Only used when `ATTACH_ROOT_HANDLER` is `True`. |
| `THRESHOLDS` | `{"WARNING": 1, "ERROR": 1, "CRITICAL": 1}` | Per-level alert thresholds for the `log_threshold_reached` signal. Omit a level to keep its default. Set a level to `None` to disable it. |

## Admin UI and access settings

| Setting | Default | Description |
| --- | --- | --- |
| `TITLE` | `"Log Panel"` | Page title shown in the admin UI. |
| `PAGE_SIZE` | `10` | Rows per page in the detail table. |
| `RANGES` | `{"24h": ..., "30d": ..., "90d": ...}` | Timeline ranges shown on the dashboard cards. |
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
