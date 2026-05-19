# Advanced topics

## Admin UI

The admin UI is designed for browsing logger health first and raw entries second.

- The landing page shows one card per logger, paginated by `CARDS_PAGE_SIZE` (default 20).
- Each card shows total errors, total warnings, recent issues from the last hour, and a timeline strip.
- Available ranges come from `LOG_PANEL["RANGES"]`.
- Clicking a logger or timeline slot opens the standard Django changelist filtered by logger name (and optional timestamp range).
- The changelist uses Django's built-in search, pagination, and sidebar filters for level and logger name.
- Timestamps are rendered in Django's configured default timezone.

Admin URL:

```text
/admin/log_panel/log/
```

## Pre-computed models

Card counters and timeline data are maintained incrementally as logs are written, so the dashboard loads in constant time regardless of total log volume.

| Model | Purpose |
| --- | --- |
| `Logger` | Normalised logger identity (unique name). |
| `LogCard` | Per-logger totals, error/warning counts, and last-seen timestamp. |
| `LogTimelineBucket` | Per-logger, per-hour and per-day log/error/warning counts for timeline slots. |

All three models are routed to the same database as `Log` via `LogsRouter`.

Timeline buckets are pre-computed at **hourly** and **daily** granularity. Custom `RANGES` always use one of these two units (`RangeUnit.HOUR` or `RangeUnit.DAY`), so all user-defined ranges are served from pre-computed data.

The `rebuild_log_cards` management command recomputes all pre-computed rows from scratch:

```bash
python manage.py rebuild_log_cards
```

The `delete_old_logs` command calls `rebuild_log_cards` automatically after deleting old entries.

## Writing logs

Any standard Python logger can write into `log_panel` as long as your Django logging configuration routes records into the active `log_panel` handler.

```python
import logging

logger = logging.getLogger(__name__)

logger.info("Info log")
logger.warning("Warning log")
logger.error("Error log")
logger.critical("Critical log")
```

Named loggers work the same way:

```python
import logging

sql_logger = logging.getLogger("myapp.sql")
sql_logger.debug("Manual SQL diagnostic message")
```

## Threshold alerts

`django-log-panel` emits the Django signal `log_panel.signals.log_threshold_reached` when a logger crosses a configured count threshold.

### How it works

After each record is written, the handler counts how many records at the configured level the same logger has emitted in the last rolling hour. The signal is dispatched only when the count exactly reaches the configured threshold.

Configure thresholds per log level through `LOG_PANEL["THRESHOLDS"]`:

```python
LOG_PANEL = {
    "THRESHOLDS": {
        "CRITICAL": 1,
        "ERROR": 10,
        "WARNING": None,
    }
}
```

### `ThresholdAlertEvent`

| Field | Type | Description |
| --- | --- | --- |
| `logger_name` | `str` | Logger that crossed the threshold. |
| `threshold_level` | `LogLevel` | Configured level that was evaluated. |
| `record_level` | `LogLevel` | Level of the triggering record. |
| `threshold` | `int` | Configured threshold value. |
| `matching_count` | `int` | Count within the rolling window. |
| `timestamp` | `datetime` | Timestamp of the triggering record. |
| `window_start` | `datetime` | Inclusive start of the rolling one-hour window. |
| `window_end` | `datetime` | End of the rolling one-hour window. |
| `message` | `str` | Triggering record message. |
| `module` | `str` | Module where the record was emitted. |
| `pathname` | `str` | Source file path. |
| `line_number` | `int` | Source line number. |

### Receiver example

```python
from django.dispatch import receiver

from log_panel.signals import ThresholdAlertEvent, log_threshold_reached


@receiver(log_threshold_reached)
def on_threshold_reached(sender, event: ThresholdAlertEvent, **kwargs):
    ...
```

### Notes

- The signal uses `send_robust`, so exceptions in receivers do not break log writing.
- When you wrap a `log_panel` handler in `logging.handlers.MemoryHandler`, the signal fires when the buffered record is flushed into the real handler.

## High-volume logging

By default `DatabaseHandler` writes each record immediately — one database round-trip per log call. That is the safest option but can become a bottleneck when `LOG_LEVEL` is `DEBUG` or request volume is high.

### BufferedDatabaseHandler

`BufferedDatabaseHandler` accumulates records in memory and writes them in a single `bulk_create` call. Enable it by setting `BUFFER_SIZE` in your `LOG_PANEL` configuration:

```python
LOG_PANEL = {
    "BUFFER_SIZE": 50,
    "BUFFER_FLUSH_INTERVAL": 2.0,
    "BUFFER_FLUSH_LEVEL": "WARNING",
}
```

When `ATTACH_ROOT_HANDLER` is `True` (the default), the auto-attached handler is automatically upgraded to `BufferedDatabaseHandler` when `BUFFER_SIZE` is set. You can also attach it manually through Django's `LOGGING` configuration:

```python
LOGGING = {
    "handlers": {
        "log_panel": {
            "class": "log_panel.handlers.BufferedDatabaseHandler",
        },
    },
    "root": {
        "handlers": ["log_panel"],
        "level": "INFO",
    },
}
```

Flushes happen when any of these conditions are met:

| Trigger | Controlled by |
| --- | --- |
| Buffer reaches `BUFFER_SIZE` records | `LOG_PANEL['BUFFER_SIZE']` |
| A record at or above `BUFFER_FLUSH_LEVEL` is emitted | `LOG_PANEL['BUFFER_FLUSH_LEVEL']` |
| A later log arrives after the flush interval elapsed | `LOG_PANEL['BUFFER_FLUSH_INTERVAL']` |
| `flush()` or `close()` is called explicitly | — |

`close()` is called automatically during normal Django / uvicorn shutdown, so buffered records are not lost on a graceful stop. Only abrupt termination (SIGKILL, OOM) can drop records that have not been flushed yet.

Threshold signals (`log_threshold_reached`) still fire per-record, but only after the batch is committed. Records at or above `BUFFER_FLUSH_LEVEL` (default `WARNING`) bypass buffering and flush immediately, so alerts for important levels are never delayed.

## Retention cleanup

The `delete_old_logs` management command deletes `Panel` rows older than the retention window. It works with both SQL and MongoDB backends.

```bash
python manage.py delete_old_logs [--days DAYS] [--batch-size BATCH_SIZE] [--dry-run]
```

| Option | Default | Description |
| --- | --- | --- |
| `--days` | `LOG_PANEL["RETENTION_DAYS"]` | Override the retention window for this run. |
| `--batch-size` | `1000` | Number of rows to delete per batch. Smaller batches reduce lock and I/O spikes. |
| `--dry-run` | not set | Print how many rows would be deleted without deleting them. |

## See also

- [Backend setup](backends.md)
- [Configuration reference](configuration.md)
