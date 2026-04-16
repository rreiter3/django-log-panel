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

        self.attach_root_handler()

    @staticmethod
    def attach_root_handler() -> None:
        import logging
        import warnings

        from log_panel.conf import get_database_alias, get_setting

        if not get_setting(key="ATTACH_ROOT_HANDLER"):
            return

        conn_str: str | None = get_setting(key="CONNECTION_STRING")
        db_alias: str | None = get_database_alias()

        if conn_str and isinstance(conn_str, str) and conn_str.strip():
            from log_panel.handlers import MongoDBHandler

            handler_cls = MongoDBHandler
        elif db_alias:
            from log_panel.handlers import DatabaseHandler

            handler_cls = DatabaseHandler
        else:
            return

        root: logging.Logger = logging.getLogger()

        for handler in root.handlers:
            if isinstance(handler, handler_cls):
                warnings.warn(
                    message="log_panel: ATTACH_ROOT_HANDLER is True but a "
                    f"{handler_cls.__name__} is already "
                    "attached to the root logger via Django LOGGING. Skipping auto-attach.",
                    stacklevel=2,
                )
                return

        level: str | None = get_setting(key="LOG_LEVEL")
        actual_handler: MongoDBHandler | DatabaseHandler = handler_cls()
        actual_handler.setLevel(level)
        root.addHandler(hdlr=actual_handler)
        root.setLevel(level)

        if handler_cls.__name__ == "MongoDBHandler":
            pymongo_logger: logging.Logger = logging.getLogger(name="pymongo")
            pymongo_logger.setLevel(level=logging.WARNING)
            pymongo_logger.propagate = False
