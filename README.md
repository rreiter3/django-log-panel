# django-log-panel

[![Latest on Django Packages](https://img.shields.io/badge/Django_Packages-django--log--panel-8c3c26.svg)](https://djangopackages.org/packages/p/django-log-panel/)

`django-log-panel` displays your Django logs inside Django admin as a per-logger status dashboard with searchable log entries and optional threshold alerts, without a separate service to run.

<p align="center">
  <a href="https://raw.githubusercontent.com/rreiter3/django-log-panel/main/docs/images/main.png">
    <img
      src="https://raw.githubusercontent.com/rreiter3/django-log-panel/main/docs/images/main.png"
      alt="Log panel dashboard showing per-logger health cards"
      width="100%"
    />
  </a>
</p>

<p align="center">
  <a href="https://raw.githubusercontent.com/rreiter3/django-log-panel/main/docs/images/main_2.png">
    <img
      src="https://raw.githubusercontent.com/rreiter3/django-log-panel/main/docs/images/main_2.png"
      alt="Log panel dashboard showing a 90 day logger timeline"
      width="100%"
    />
  </a>
</p>

<p align="center">
  <a href="https://raw.githubusercontent.com/rreiter3/django-log-panel/main/docs/images/filter.png">
    <img
      src="https://raw.githubusercontent.com/rreiter3/django-log-panel/main/docs/images/filter.png"
      alt="Log detail view with message search and paginated entries"
      width="49%"
    />
  </a>
  <a href="https://raw.githubusercontent.com/rreiter3/django-log-panel/main/docs/images/filter_2.png">
    <img
      src="https://raw.githubusercontent.com/rreiter3/django-log-panel/main/docs/images/filter_2.png"
      alt="Log detail view with the level filter dropdown open"
      width="49%"
    />
  </a>
</p>

## Features

- A status-page style dashboard in Django admin, with one health card per logger.
- A searchable, filterable log table for drilling into individual entries.
- MongoDB and SQL storage backends, depending on how you want to store logs.
- Threshold alerts through a Django signal that your application can react to.
- Configurable ranges, colors, page size, title, and access control.
- Automatic root-handler setup by default, with manual `LOGGING` control when needed.

## Requirements

- Python >= 3.12
- Django >= 5.2
- `pymongo>=4.16.0,<5` *only when using the MongoDB backend*
- A running, reachable MongoDB instance *when using the MongoDB backend*

## Installation

```bash
# with uv
uv add django-log-panel

# with pip
pip install django-log-panel
```

For MongoDB support, install the optional extra. This installs the Python client only; you still need an actual MongoDB instance to connect to:

```bash
# with uv
uv add "django-log-panel[mongodb]"

# with pip
pip install "django-log-panel[mongodb]"
```

## Choose a backend

| Backend | Use it when                                                             | Retention                                                     | Extra setup                                                                                                             |
| ------- | ----------------------------------------------------------------------- | ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| MongoDB | You want append-only logging with cheap writes and MongoDB TTL cleanup. | Automatic TTL expiry on the collection.                       | Install the `mongodb` extra, run a reachable MongoDB instance, and set `CONNECTION_STRING`.                         |
| SQL     | You want logs in a Django-managed relational database.                  | Run the `delete_old_logs` management command on a schedule. | Add `LogsRouter`, point `DATABASE_ALIAS` at the target database, and run the `log_panel` migration on that alias. |

## Quick start

### 1. Add the app

```python
INSTALLED_APPS = [
    ...,
    "log_panel",
]
```

### 2. Configure one backend

MongoDB:

```python
LOG_PANEL = {
    "CONNECTION_STRING": "mongodb://localhost:27017",
    "DB_NAME": "myapp_logs",
    "COLLECTION": "logs",
    "TTL_DAYS": 90,
}
```

This example assumes a MongoDB instance is running and reachable at `localhost:27017`.

SQL:

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

If you use the SQL backend, run the migration on the logging database:

```bash
python manage.py migrate log_panel --database=logs
```

### 3. Open Django admin

Go to `Application Logs`, or open:

```text
/admin/log_panel/panel/
```

Once configured, any standard Python logger that flows through the selected handler will show up in the panel.

## How log capture works

- `LOG_PANEL` selects how the admin reads log data.
- By default, `log_panel` auto-attaches the matching handler to the root logger at startup.
- Set `ATTACH_ROOT_HANDLER = False` when you want full control through Django `LOGGING`.
- `LOG_LEVEL` only affects the auto-attached root handler.
- Stored fields come from the log record itself; `LOGGING` formatters do not reshape the stored data.

Full setup notes and manual `LOGGING` examples are in the backend guide.

## Advanced topics

- [Backend setup and manual `LOGGING` examples](https://github.com/rreiter3/django-log-panel/blob/main/docs/backends.md)
- [Configuration reference](https://github.com/rreiter3/django-log-panel/blob/main/docs/configuration.md)
- [Alerts, buffering, SQL retention cleanup, and admin UI](https://github.com/rreiter3/django-log-panel/blob/main/docs/advanced.md)
- [Local development workflow](https://github.com/rreiter3/django-log-panel/blob/main/docs/development.md)

## Contributing

For local work, use the `uv` workflow in [docs/development.md](https://github.com/rreiter3/django-log-panel/blob/main/docs/development.md).
