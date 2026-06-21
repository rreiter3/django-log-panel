import sys
from pathlib import Path


SERVER_COMMANDS: set[str] = {"daphne", "gunicorn", "hypercorn", "runserver", "uvicorn"}
MIGRATION_COMMANDS: set[str] = {"makemigrations", "migrate"}
STORAGE_MUTED_COMMANDS: set[str] = {
    *MIGRATION_COMMANDS,
    "delete_old_logs",
    "rebuild_log_cards",
}


def argv_command_names() -> set[str]:
    """Return executable and argument basenames from the current process argv."""
    return {Path(argument).name for argument in sys.argv}


def is_storage_muted_command() -> bool:
    """Return whether log-panel database writes should be disabled."""
    return bool(argv_command_names() & STORAGE_MUTED_COMMANDS)


def is_migration_command() -> bool:
    """Return whether the current process is running Django migration orchestration."""
    return bool(argv_command_names() & MIGRATION_COMMANDS)
