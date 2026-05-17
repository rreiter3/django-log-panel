import logging

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

import log_panel
from log_panel.bootstrap import (
    REQUEST_STARTED_DISPATCH_UID,
    attach_root_handler,
    attach_root_handler_on_request_started,
    build_database_handler,
    configure_root_handler,
    validate_database_router,
)
from log_panel.handlers import BufferedDatabaseHandler, DatabaseHandler


def test_log_panel_config_ready_delegates_to_bootstrap():
    from unittest.mock import patch

    from log_panel.apps import LogPanelConfig

    config = LogPanelConfig("log_panel", log_panel)
    with patch("log_panel.bootstrap.bootstrap_log_panel") as mock_bootstrap:
        config.ready()

    mock_bootstrap.assert_called_once_with()


@override_settings(LOG_PANEL={"DATABASE_ALIAS": "logs"}, DATABASE_ROUTERS=[])
def test_validate_database_router_requires_logs_router():
    with pytest.raises(ImproperlyConfigured):
        validate_database_router()


@override_settings(
    LOG_PANEL={"DATABASE_ALIAS": "logs"},
    DATABASE_ROUTERS=["log_panel.routers.LogsRouter"],
)
def test_validate_database_router_accepts_logs_router():
    validate_database_router()


@override_settings(LOG_PANEL={"BUFFER_SIZE": 50, "LOG_LEVEL": "ERROR"})
def test_build_database_handler_uses_buffered_handler_when_buffer_size_is_set():
    handler = build_database_handler()
    try:
        assert isinstance(handler, BufferedDatabaseHandler)
        assert handler.level == logging.ERROR
        assert handler._buffer == []
    finally:
        handler.close()


@override_settings(LOG_PANEL={"BUFFER_SIZE": None, "LOG_LEVEL": "WARNING"})
def test_build_database_handler_uses_database_handler_when_buffer_size_is_none():
    handler = build_database_handler()
    try:
        assert type(handler) is DatabaseHandler
        assert handler.level == logging.WARNING
    finally:
        handler.close()


@override_settings(
    LOG_PANEL={
        "ATTACH_ROOT_HANDLER": True,
        "DATABASE_ALIAS": "logs",
        "BUFFER_SIZE": 50,
    }
)
def test_attach_root_handler_attaches_only_one_database_handler():
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    try:
        attach_root_handler()
        with pytest.warns(UserWarning):
            attach_root_handler()

        handlers = [h for h in root.handlers if isinstance(h, DatabaseHandler)]
        assert len(handlers) == 1
        assert isinstance(handlers[0], BufferedDatabaseHandler)
    finally:
        for handler in root.handlers[:]:
            if handler not in original_handlers:
                root.removeHandler(handler)
                handler.close()


@override_settings(
    LOG_PANEL={
        "ATTACH_ROOT_HANDLER": False,
        "DATABASE_ALIAS": "logs",
        "BUFFER_SIZE": 50,
    }
)
def test_attach_root_handler_skips_when_auto_attach_is_disabled():
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    try:
        attach_root_handler()
        assert root.handlers == original_handlers
    finally:
        for handler in root.handlers[:]:
            if handler not in original_handlers:
                root.removeHandler(handler)
                handler.close()


@override_settings(
    LOG_PANEL={
        "ATTACH_ROOT_HANDLER": True,
        "DATABASE_ALIAS": None,
        "BUFFER_SIZE": 50,
    }
)
def test_attach_root_handler_skips_when_database_alias_is_not_configured():
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    try:
        attach_root_handler()
        assert root.handlers == original_handlers
    finally:
        for handler in root.handlers[:]:
            if handler not in original_handlers:
                root.removeHandler(handler)
                handler.close()


@override_settings(
    LOG_PANEL={
        "ATTACH_ROOT_HANDLER": True,
        "DATABASE_ALIAS": "logs",
        "BUFFER_SIZE": 50,
    }
)
def test_configure_root_handler_attaches_immediately_for_management_command(
    monkeypatch,
):
    monkeypatch.setattr(
        "log_panel.bootstrap.sys.argv", ["manage.py", "delete_old_logs"]
    )

    from unittest.mock import patch

    with patch("log_panel.bootstrap.attach_root_handler") as mock_attach:
        configure_root_handler()

    mock_attach.assert_called_once_with()


@override_settings(
    LOG_PANEL={
        "ATTACH_ROOT_HANDLER": True,
        "DATABASE_ALIAS": "logs",
        "BUFFER_SIZE": 50,
    }
)
def test_configure_root_handler_skips_migration_commands(monkeypatch):
    monkeypatch.setattr("log_panel.bootstrap.sys.argv", ["manage.py", "migrate"])

    from unittest.mock import patch

    with patch("log_panel.bootstrap.attach_root_handler") as mock_attach:
        configure_root_handler()

    mock_attach.assert_not_called()


@override_settings(
    LOG_PANEL={
        "ATTACH_ROOT_HANDLER": True,
        "DATABASE_ALIAS": "logs",
        "BUFFER_SIZE": 50,
    }
)
def test_configure_root_handler_defers_server_attachment_until_first_request(
    monkeypatch,
):
    monkeypatch.setattr(
        "log_panel.bootstrap.sys.argv",
        ["uvicorn", "config.asgi:application", "--reload"],
    )
    from unittest.mock import patch

    with patch("log_panel.bootstrap.attach_root_handler") as mock_attach:
        with patch("log_panel.bootstrap.request_started.connect") as mock_connect:
            configure_root_handler()

    mock_attach.assert_not_called()
    mock_connect.assert_called_once_with(
        attach_root_handler_on_request_started,
        dispatch_uid=REQUEST_STARTED_DISPATCH_UID,
        weak=False,
    )


@pytest.mark.parametrize(
    "argv",
    [
        [
            "/opt/venv/lib/python3.14/site-packages/uvicorn/__main__.py",
            "config.asgi:application",
        ],
        [
            "/opt/venv/lib/python3.14/site-packages/daphne/__main__.py",
            "config.asgi:application",
        ],
        [
            "/opt/venv/lib/python3.14/site-packages/gunicorn/__main__.py",
            "config.asgi:application",
        ],
    ],
)
@override_settings(
    LOG_PANEL={
        "ATTACH_ROOT_HANDLER": True,
        "DATABASE_ALIAS": "logs",
        "BUFFER_SIZE": 50,
    }
)
def test_configure_root_handler_defers_when_run_as_module(monkeypatch, argv):
    monkeypatch.setattr("log_panel.bootstrap.sys.argv", argv)

    from unittest.mock import patch

    with patch("log_panel.bootstrap.attach_root_handler") as mock_attach:
        with patch("log_panel.bootstrap.request_started.connect") as mock_connect:
            configure_root_handler()

    mock_attach.assert_not_called()
    mock_connect.assert_called_once_with(
        attach_root_handler_on_request_started,
        dispatch_uid=REQUEST_STARTED_DISPATCH_UID,
        weak=False,
    )


def test_attach_root_handler_on_request_started_disconnects_then_attaches():
    from unittest.mock import patch

    with patch("log_panel.bootstrap.request_started.disconnect") as mock_disconnect:
        with patch("log_panel.bootstrap.attach_root_handler") as mock_attach:
            attach_root_handler_on_request_started()

    mock_disconnect.assert_called_once_with(dispatch_uid=REQUEST_STARTED_DISPATCH_UID)
    mock_attach.assert_called_once_with()
