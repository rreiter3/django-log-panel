# Backend setup

Use this guide when you want the full setup details for MongoDB or SQL storage, including manual `LOGGING` configuration.

## Backend resolution

The admin UI reads log data through `log_panel.conf.get_backend()`. Resolution order is:

1. `LOG_PANEL["BACKEND"]` if you provide a custom backend class path.
2. SQL backend when `LOG_PANEL["DATABASE_ALIAS"]` is set.
3. MongoDB backend when `LOG_PANEL["CONNECTION_STRING"]` is set.
4. No backend, and the admin shows an unconfigured state.

`LOG_PANEL` controls how the admin reads logs. Handlers in Django `LOGGING` control where records are written.

## Shared capture behavior

By default, `log_panel` attaches a handler to the root logger during app startup:

- `ATTACH_ROOT_HANDLER = True` enables automatic setup.
- `LOG_LEVEL` sets the level for the auto-attached handler and the root logger.
- If a matching `DatabaseHandler` or `MongoDBHandler` is already attached to the root logger, auto-attach is skipped and a warning is emitted.
- Stored fields come directly from the log record. `LOGGING` formatters do not change the structured fields written by `log_panel`.
- Exception tracebacks are appended automatically when `exc_info` is present.

Set `ATTACH_ROOT_HANDLER = False` when you want to manage handlers yourself in Django `LOGGING`.

If you configure both `CONNECTION_STRING` and `DATABASE_ALIAS`, do it intentionally. The admin read backend prefers SQL, while automatic handler attachment prefers MongoDB when a connection string is present.

## MongoDB backend

Use MongoDB when you want append-only logging with automatic TTL cleanup.

This backend requires two things:

- the `mongodb` extra so `pymongo` is installed
- a running, reachable MongoDB instance that matches `LOG_PANEL["CONNECTION_STRING"]`

### Minimal config

```python
LOG_PANEL = {
    "CONNECTION_STRING": "mongodb://localhost:27017",
    "DB_NAME": "myapp_logs",
    "COLLECTION": "logs",
    "TTL_DAYS": 90,
}
```

### Notes

- Install the package with the `mongodb` extra so `pymongo` is available.
- The package does not start MongoDB for you. `CONNECTION_STRING` must point at a real MongoDB server or cluster.
- `MongoDBHandler` creates three indexes on first write:
  - a TTL index on `timestamp`
  - a compound index on `(timestamp, logger_name, level)`
  - a compound index on `(logger_name, timestamp DESC)`
- MongoDB cleanup is handled by the TTL index. No Django cleanup command is required.
- MongoDB does not require a SQL logging database. If you already use `LogsRouter`, its `allow_migrate()` method returns `False` for `log_panel` in MongoDB-only mode, so the SQL table is not created.
- For very large collections and long time ranges, set `LOG_PANEL["ALLOW_DISK_USE"] = True` to allow MongoDB aggregation pipelines to spill to disk.

### Manual `LOGGING` example

```python
LOG_PANEL = {
    "CONNECTION_STRING": "mongodb://localhost:27017",
    "ATTACH_ROOT_HANDLER": False,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "log_panel": {
            "class": "log_panel.handlers.MongoDBHandler",
        },
    },
    "root": {
        "handlers": ["log_panel"],
        "level": "INFO",
    },
}
```

## SQL backend

Use SQL when logs need to live in a relational database that Django can manage.

### Minimal config

```python
DATABASES["logs"] = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": "myapp_logs",
    "USER": "...",
    "PASSWORD": "...",
    "HOST": "...",
    "PORT": "...",
}

DATABASE_ROUTERS = [
    "log_panel.routers.LogsRouter",
]

LOG_PANEL = {
    "DATABASE_ALIAS": "logs",
    "TTL_DAYS": 90,
}
```

### Migration

`LogsRouter` keeps the `Panel` model on the configured logging database, but Django still targets `default` unless you tell it otherwise:

```bash
python manage.py migrate log_panel --database=logs
```

If your logging alias is `default`, the normal migration flow is enough.

### Notes

- `LogsRouter` is required when `DATABASE_ALIAS` is set. `LogPanelConfig.ready()` raises `ImproperlyConfigured` if it is missing.
- Logs are stored through the `Panel` model in the configured database alias.
- SQL retention cleanup is not automatic. Use the `delete_old_logs` command on a schedule.

### Manual `LOGGING` example

```python
LOG_PANEL = {
    "DATABASE_ALIAS": "logs",
    "ATTACH_ROOT_HANDLER": False,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "log_panel": {
            "class": "log_panel.handlers.DatabaseHandler",
        },
    },
    "root": {
        "handlers": ["log_panel"],
        "level": "INFO",
    },
}
```

## See also

- [Configuration reference](configuration.md)
- [Advanced topics](advanced.md)
- [Development workflow](development.md)
