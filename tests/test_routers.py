import django.contrib.auth.models as auth_models
import pytest
from django.test import override_settings

from log_panel.models import Panel
from log_panel.routers import LogsRouter


@pytest.fixture
def router():
    return LogsRouter()


@override_settings(LOG_PANEL={"DATABASE_ALIAS": "logs"})
def test_db_for_read_returns_alias_for_panel_model(router):
    assert router.db_for_read(Panel) == "logs"


def test_db_for_read_returns_none_for_other_model(router):
    assert router.db_for_read(auth_models.User) is None


@override_settings(LOG_PANEL={"DATABASE_ALIAS": "logs"})
def test_db_for_write_returns_alias_for_panel_model(router):
    assert router.db_for_write(Panel) == "logs"


def test_db_for_write_returns_none_for_other_model(router):
    assert router.db_for_write(auth_models.User) is None


@override_settings(LOG_PANEL={"DATABASE_ALIAS": "logs"})
def test_allow_migrate_true_for_matching_alias(router):
    assert router.allow_migrate("logs", "log_panel") is True


@override_settings(LOG_PANEL={"DATABASE_ALIAS": "logs"})
def test_allow_migrate_false_for_wrong_alias(router):
    assert router.allow_migrate("default", "log_panel") is False


def test_allow_migrate_false_when_no_alias_configured(router):
    assert router.allow_migrate("default", "log_panel") is False


def test_allow_migrate_none_for_other_app_label(router):
    assert router.allow_migrate("default", "auth") is None
    assert router.allow_migrate("default", "contenttypes") is None
