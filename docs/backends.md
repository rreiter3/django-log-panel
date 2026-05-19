# Backend setup

Use this guide when you want the full setup details for MongoDB or SQL storage, including manual `LOGGING` configuration.

## Backend resolution

The admin UI reads log data through `log_panel.conf.get_backend()`. Resolution order is:

1. `LOG_PANEL["BACKEND"]` if you provide a custom backend class path.
2. ORM backend when `LOG_PANEL["DATABASE_ALIAS"]` is set.
3. No backend, and the admin shows an unconfigured state.

`LOG_PANEL` controls how the admin reads logs. Handlers in Django `LOGGING` control where records are written.

## Shared capture behavior

By default, `log_panel` attaches a `DatabaseHandler` to the root logger during app startup:

- `ATTACH_ROOT_HANDLER = True` enables automatic setup.
- `LOG_LEVEL` sets the level for the auto-attached handler and the root logger.
- If a `DatabaseHandler` is already attached to the root logger, auto-attach is skipped and a warning is emitted.
- Stored fields come directly from the log record. `LOGGING` formatters do not change the structured fields written by `log_panel`.
- Exception tracebacks are appended automatically when `exc_info` is present.
- Messages are stored in full by default. Large messages keep an inline preview on the log row and store the complete text in ordered chunks.
- Recursive log-storage writes are skipped by the handler's recursion guard.

Set `ATTACH_ROOT_HANDLER = False` when you want to manage handlers yourself in Django `LOGGING`.

## MongoDB backend

Use MongoDB when you want append-only logging with flexible document storage.

This backend requires two things:

- the `django-mongodb-backend` package is installed
- a running, reachable MongoDB instance

### Minimal config

You can pass a full MongoDB connection URI in `HOST`:

```python
DATABASES["logs"] = {
    "ENGINE": "django_mongodb_backend",
    "HOST": "mongodb://localhost:27017",
    "NAME": "myapp_logs",
}

DATABASE_ROUTERS = [
    "log_panel.routers.LogsRouter",
]

LOG_PANEL = {
    "DATABASE_ALIAS": "logs",
    "RETENTION_DAYS": 90,
}
```

### Connection options

`HOST` accepts any standard [MongoDB connection string](https://www.mongodb.com/docs/manual/reference/connection-string/), including `mongodb+srv://` URIs, authentication credentials, and replica set addresses:

```python
DATABASES["logs"] = {
    "ENGINE": "django_mongodb_backend",
    "HOST": "mongodb+srv://cluster0.example.mongodb.net",
    "NAME": "myapp_logs",
    "USER": "log_writer",
    "PASSWORD": "...",
    "PORT": 27017,
    "OPTIONS": {
        "retryWrites": "true",
        "w": "majority",
        "tls": "true",
    },
}
```

- `HOST` — connection URI. Omit or set `"localhost"` for a local instance.
- `USER` / `PASSWORD` — required when authentication is enabled.
- `PORT` — optional, defaults to `27017`.
- `OPTIONS` — passed directly to PyMongo's `MongoClient`.

If you provide credentials or options in the `HOST` URI **and** as separate keys, the separate keys take precedence.

For a replica set or sharded cluster, include all hosts in `HOST`:

```python
"HOST": "mongodb://mongos0.example.com:27017,mongos1.example.com:27017"
```

### Migration

`LogsRouter` keeps the `Log` model on the configured logging database, but Django still targets `default` unless you tell it otherwise:

```bash
python manage.py migrate log_panel --database=logs
```

The router reserves the logging alias for `log_panel` models, so Django's system checks do not validate unrelated project apps such as `auth` or `admin` against MongoDB.

If your logging alias is `default`, the normal migration flow is enough.

### Notes

- Install the package with the `mongodb` extra so `django-mongodb-backend` is available.
- The package does not start MongoDB for you. The `HOST` in `DATABASES` must point at a real MongoDB server or cluster.
- `django-mongodb-backend` version must match your Django version (e.g. 5.2.x for Django 5.2, 6.0.x for Django 6.0). The `mongodb` extra allows any compatible release.
- MongoDB cleanup is not automatic. Use the `delete_old_logs` management command on a schedule.
- `LogsRouter` is required when `DATABASE_ALIAS` is set. `LogPanelConfig.ready()` raises `ImproperlyConfigured` if it is missing.
- Logs are stored through the `Panel` model via the Django ORM — the same model and handler used by the SQL backend.
- Compound indexes on `(timestamp, logger_name, level)` and `(logger_name, -timestamp)` are created via the model Meta for query performance.

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
    "RETENTION_DAYS": 90,
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

## Custom backend

To build your own backend, subclass `log_panel.backends.base.LogsBackend` and implement the abstract methods:

```python
from log_panel.backends.base import LogsBackend


class MyBackend(LogsBackend):
    def get_logger_cards(self, now_utc, range_config, app_timezone, page=1, page_size=5, card_filter=""):
        # Return (rows, total_cards) tuple
        ...

    def query_logs(self, logger_names, levels, search, offset, limit, app_timezone, **kwargs):
        ...

    def count_logs(self, logger_names, levels, search, **kwargs):
        ...
```

Then point `LOG_PANEL["BACKEND"]` at your class:

```python
LOG_PANEL = {
    "BACKEND": "myapp.backends.MyBackend",
}
```

The `get_local_now_and_slot_delta` helper on `LogsBackend` is available for timeline slot calculations if your backend needs them.

## See also

- [Configuration reference](configuration.md)
- [Advanced topics](advanced.md)
- [Development workflow](development.md)
