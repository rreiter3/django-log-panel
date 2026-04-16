# Advanced topics

## Admin UI

The admin UI is designed for browsing logger health first and raw entries second.

- The landing page shows one card per logger.
- Each card shows total errors, total warnings, recent issues from the last hour, and a timeline strip.
- Available ranges come from `LOG_PANEL["RANGES"]`.
- Clicking a logger opens a paginated table view.
- The table view supports level filtering and free-text message search.
- Timestamps are rendered in Django's configured default timezone.

Admin URL:

```text
/admin/log_panel/panel/
```

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

By default, both handlers write each record immediately. That is safest, but one database write per log call can become a bottleneck on busy applications.

Python's standard `logging.handlers.MemoryHandler` can buffer records and flush them when:

- the buffer reaches `capacity`
- a record at or above `flushLevel` is emitted

Example using the SQL handler as the flush target:

```python
LOGGING = {
    "handlers": {
        "log_panel_db": {
            "class": "log_panel.handlers.DatabaseHandler",
        },
        "log_panel": {
            "class": "logging.handlers.MemoryHandler",
            "capacity": 50,
            "flushLevel": "ERROR",
            "target": "log_panel_db",
        },
    },
    "root": {
        "handlers": ["log_panel"],
        "level": "INFO",
    },
}
```

For MongoDB, use the same pattern but make the target handler a `log_panel.handlers.MongoDBHandler`.

| Setting | Effect |
| --- | --- |
| Lower `capacity` | Smaller exposure window and more frequent writes. |
| Higher `capacity` | Better throughput, but more records at risk on a hard crash. |
| `flushLevel="ERROR"` | Errors flush immediately. |
| `flushLevel="CRITICAL"` | Only critical records bypass buffering. |

`MemoryHandler` flushes on `close()`, so normal shutdown does not lose buffered records. Only abrupt process termination can lose records that have not been flushed yet.

## SQL retention cleanup

The `delete_old_logs` management command deletes SQL `Panel` rows older than the retention window. It is only relevant for the SQL backend.

```bash
python manage.py delete_old_logs [--days DAYS] [--batch-size BATCH_SIZE] [--dry-run]
```

| Option | Default | Description |
| --- | --- | --- |
| `--days` | `LOG_PANEL["TTL_DAYS"]` | Override the retention window for this run. |
| `--batch-size` | `1000` | Number of rows to delete per batch. Smaller batches reduce lock and I/O spikes. |
| `--dry-run` | not set | Print how many rows would be deleted without deleting them. |

## See also

- [Backend setup](backends.md)
- [Configuration reference](configuration.md)
