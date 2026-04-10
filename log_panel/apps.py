from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class LogPanelConfig(AppConfig):
    name = "log_panel"
    verbose_name = "Log Panel"

    def ready(self) -> None:
        from django.conf import settings

        from log_panel.conf import get_database_alias

        if get_database_alias() and "log_panel.routers.LogsRouter" not in getattr(
            settings, "DATABASE_ROUTERS", []
        ):
            raise ImproperlyConfigured(
                "log_panel: DATABASE_ALIAS is configured but 'log_panel.routers.LogsRouter' "
                "is not in DATABASE_ROUTERS. Add it to route Panel reads/writes to the correct database."
            )
