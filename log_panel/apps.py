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
                "is not in DATABASE_ROUTERS. Add it to route Log reads/writes to the correct database."
            )

        self.attach_root_handler()

    @staticmethod
    def attach_root_handler() -> None:
        import logging
        import warnings

        from log_panel.conf import get_database_alias, get_setting

        if not get_setting(key="ATTACH_ROOT_HANDLER"):
            return

        db_alias: str | None = get_database_alias()

        if not db_alias:
            return

        from log_panel.handlers import DatabaseHandler

        root: logging.Logger = logging.getLogger()

        for handler in root.handlers:
            if isinstance(handler, DatabaseHandler):
                warnings.warn(
                    message="log_panel: ATTACH_ROOT_HANDLER is True but a "
                    "DatabaseHandler is already "
                    "attached to the root logger via Django LOGGING. Skipping auto-attach.",
                    stacklevel=2,
                )
                return

        level: str | None = get_setting(key="LOG_LEVEL")
        actual_handler: DatabaseHandler = DatabaseHandler()
        actual_handler.setLevel(level)
        root.addHandler(hdlr=actual_handler)
        root.setLevel(level)
