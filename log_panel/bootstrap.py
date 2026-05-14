import logging
import sys
import warnings
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import request_started

from log_panel.conf import get_database_alias, get_setting
from log_panel.handlers import BufferedDatabaseHandler, DatabaseHandler

LOGS_ROUTER_PATH = "log_panel.routers.LogsRouter"
REQUEST_STARTED_DISPATCH_UID = "log_panel.attach_root_handler"
SERVER_COMMANDS: set[str] = {"daphne", "gunicorn", "hypercorn", "runserver", "uvicorn"}
MIGRATION_COMMANDS: set[str] = {"makemigrations", "migrate"}


def bootstrap_log_panel() -> None:
    """Configure log panel integration after Django app loading."""
    validate_database_router()
    configure_root_handler()


def validate_database_router() -> None:
    """Ensure the configured log database alias is routed through LogsRouter."""
    if get_database_alias() and LOGS_ROUTER_PATH not in getattr(
        settings, "DATABASE_ROUTERS", []
    ):
        raise ImproperlyConfigured(
            "log_panel: DATABASE_ALIAS is configured but 'log_panel.routers.LogsRouter' "
            "is not in DATABASE_ROUTERS. Add it to route Log reads/writes to the correct database."
        )


def build_database_handler() -> DatabaseHandler:
    """Return the configured database logging handler."""
    buffer_size = get_setting(key="BUFFER_SIZE")
    handler: DatabaseHandler = (
        BufferedDatabaseHandler() if buffer_size is not None else DatabaseHandler()
    )
    handler.setLevel(level=get_setting(key="LOG_LEVEL"))
    return handler


def attach_root_handler() -> None:
    """Attach the configured database handler to the root logger when enabled."""
    if not get_setting(key="ATTACH_ROOT_HANDLER"):
        return

    if not get_database_alias():
        return

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

    root.addHandler(hdlr=build_database_handler())
    root.setLevel(level=get_setting(key="LOG_LEVEL"))


def configure_root_handler() -> None:
    """Attach immediately for commands, or defer server attachment until request handling."""
    if not get_setting(key="ATTACH_ROOT_HANDLER"):
        return

    if not get_database_alias():
        return

    if is_migration_command():
        return

    if should_defer_root_handler_attachment():
        request_started.connect(
            attach_root_handler_on_request_started,
            dispatch_uid=REQUEST_STARTED_DISPATCH_UID,
            weak=False,
        )
        return

    attach_root_handler()


def attach_root_handler_on_request_started(**kwargs) -> None:
    """Attach the root handler once the serving process handles its first request."""
    request_started.disconnect(dispatch_uid=REQUEST_STARTED_DISPATCH_UID)
    attach_root_handler()


def should_defer_root_handler_attachment() -> bool:
    """Return whether root logging should wait until the serving process handles requests."""
    command_names = {Path(argument).name for argument in sys.argv}
    return bool(command_names & SERVER_COMMANDS)


def is_migration_command() -> bool:
    """Return whether the current process is running Django migration orchestration."""
    command_names = {Path(argument).name for argument in sys.argv}
    return bool(command_names & MIGRATION_COMMANDS)
