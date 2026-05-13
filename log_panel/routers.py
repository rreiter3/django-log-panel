from typing import Any

from django.db.models import Model

from log_panel.conf import get_database_alias


class LogsRouter:
    """
    Route Log model reads/writes to the configured logging database alias.

    Add to Django settings::

        DATABASE_ROUTERS = ['log_panel.routers.LogsRouter']

    When no ``DATABASE_ALIAS`` is configured, ``allow_migrate`` returns
    ``False`` for all databases so the ``Log`` table is never created.
    """

    def db_for_read(self, model: type[Model], **hints: Any) -> str | None:
        """Direct Log reads to the logging database alias."""
        if model._meta.app_label == "log_panel":
            return get_database_alias()
        return None

    def db_for_write(self, model: type[Model], **hints: Any) -> str | None:
        """Direct Log writes to the logging database alias."""
        if model._meta.app_label == "log_panel":
            return get_database_alias()
        return None

    def allow_migrate(
        self, db: str, app_label: str, model_name: str | None = None, **hints: Any
    ) -> bool | None:
        """
        Allow Log migrations only on the configured logging alias.

        Returns ``False`` when no alias is configured, preventing accidental
        table creation in the default database.
        """
        if app_label == "log_panel":
            alias: str | None = get_database_alias()
            if alias is None:
                return False
            return db == alias
        return None
