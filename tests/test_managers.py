from datetime import UTC, datetime

import pytest
from django.test import override_settings

from log_panel.backends.sql import SqlBackend
from log_panel.managers import LogManager, LogQueryset, levels_at_or_above
from log_panel.models import Panel


def test_levels_at_or_above_debug_returns_all_levels():
    result = levels_at_or_above("DEBUG")
    assert set(result) == {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def test_levels_at_or_above_warning_excludes_debug_and_info():
    result = levels_at_or_above("WARNING")
    assert set(result) == {"WARNING", "ERROR", "CRITICAL"}


def test_levels_at_or_above_critical_returns_only_critical():
    result = levels_at_or_above("CRITICAL")
    assert result == ["CRITICAL"]


def test_levels_at_or_above_is_case_insensitive():
    assert levels_at_or_above("warning") == levels_at_or_above("WARNING")


def test_levels_at_or_above_invalid_level_raises_value_error():
    with pytest.raises(ValueError, match="Unknown log level"):
        levels_at_or_above("VERBOSE")


def test_filter_returns_new_instance():
    qs = LogQueryset(backend=None)
    assert qs.filter(logger_names=["orders"]) is not qs


def test_filter_does_not_mutate_original():
    qs = LogQueryset(backend=None)
    qs.filter(logger_names=["orders"])
    assert qs._filters.logger_names is None


def test_filter_logger_names_stored():
    qs = LogQueryset(backend=None).filter(logger_names=["orders", "machines"])
    assert qs._filters.logger_names == ["orders", "machines"]


def test_filter_min_level_resolved_to_levels():
    qs = LogQueryset(backend=None).filter(min_level="WARNING")
    assert set(qs._filters.levels or []) == {"WARNING", "ERROR", "CRITICAL"}


def test_filter_chaining_combines_constraints():
    qs = (
        LogQueryset(backend=None)
        .filter(logger_names=["orders"])
        .filter(min_level="ERROR")
    )
    assert qs._filters.logger_names == ["orders"]
    assert set(qs._filters.levels or []) == {"ERROR", "CRITICAL"}


def test_filter_later_call_replaces_logger_names():
    qs = LogQueryset(backend=None).filter(logger_names=["a"]).filter(logger_names=["b"])
    assert qs._filters.logger_names == ["b"]


def test_filter_later_call_replaces_min_level():
    qs = LogQueryset(backend=None).filter(min_level="DEBUG").filter(min_level="ERROR")
    assert set(qs._filters.levels or []) == {"ERROR", "CRITICAL"}


def test_filter_timestamp_from_stored():
    from datetime import UTC, datetime

    ts = datetime(2024, 1, 1, tzinfo=UTC)
    qs = LogQueryset(backend=None).filter(timestamp_from=ts)
    assert qs._filters.timestamp_from == ts


def test_filter_timestamp_to_stored():
    from datetime import UTC, datetime

    ts = datetime(2024, 12, 31, tzinfo=UTC)
    qs = LogQueryset(backend=None).filter(timestamp_to=ts)
    assert qs._filters.timestamp_to == ts


def test_getitem_negative_index_raises_value_error():
    with pytest.raises(ValueError, match="Negative indexing"):
        LogQueryset(backend=None)[-1]


def test_getitem_invalid_key_raises_type_error():
    with pytest.raises(TypeError):
        LogQueryset(backend=None)["bad"]


@pytest.mark.django_db
def test_getitem_out_of_range_raises_index_error():
    with pytest.raises(IndexError):
        LogQueryset(SqlBackend())[100]


def test_len_returns_zero_when_backend_is_none():
    assert len(LogQueryset(backend=None)) == 0


def test_iter_returns_empty_when_backend_is_none():
    assert list(LogQueryset(backend=None)) == []


def test_getitem_returns_empty_list_for_slice_when_backend_is_none():
    assert LogQueryset(backend=None)[0:10] == []


@pytest.mark.django_db
def test_len_returns_total_count(panel_factory):
    for _ in range(4):
        panel_factory()
    assert len(LogQueryset(SqlBackend())) == 4


@pytest.mark.django_db
def test_len_filters_by_logger_names(panel_factory):
    panel_factory(logger_name="orders")
    panel_factory(logger_name="machines")
    panel_factory(logger_name="auth")
    assert (
        len(LogQueryset(SqlBackend()).filter(logger_names=["orders", "machines"])) == 2
    )


@pytest.mark.django_db
def test_len_filters_by_min_level(panel_factory):
    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        panel_factory(level=level)
    assert len(LogQueryset(SqlBackend()).filter(min_level="WARNING")) == 3


@pytest.mark.django_db
def test_iter_returns_all_entries(panel_factory):
    for _ in range(3):
        panel_factory()
    logs = list(LogQueryset(SqlBackend()))
    assert len(logs) == 3


@pytest.mark.django_db
def test_iter_filters_by_logger_names(panel_factory):
    panel_factory(logger_name="orders")
    panel_factory(logger_name="machines")
    panel_factory(logger_name="auth")
    logs = list(LogQueryset(SqlBackend()).filter(logger_names=["orders", "machines"]))
    assert len(logs) == 2
    assert all(log["logger_name"] in {"orders", "machines"} for log in logs)


@pytest.mark.django_db
def test_iter_filters_by_min_level(panel_factory):
    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        panel_factory(level=level)
    logs = list(LogQueryset(SqlBackend()).filter(min_level="WARNING"))
    assert len(logs) == 3
    assert all(log["level"] in {"WARNING", "ERROR", "CRITICAL"} for log in logs)


@pytest.mark.django_db
def test_iter_filters_by_search(panel_factory):
    panel_factory(message="timeout connecting to database")
    panel_factory(message="user logged in")
    logs = list(LogQueryset(SqlBackend()).filter(search="timeout"))
    assert len(logs) == 1
    assert "timeout" in logs[0]["message"]


@pytest.mark.django_db
def test_getitem_slice_returns_correct_count(panel_factory):
    for _ in range(5):
        panel_factory()
    result = LogQueryset(SqlBackend())[0:2]
    assert len(result) == 2


@pytest.mark.django_db
def test_getitem_slice_offset_skips_entries(panel_factory):
    for _ in range(5):
        panel_factory()
    all_logs = list(LogQueryset(SqlBackend()))
    sliced = LogQueryset(SqlBackend())[2:4]
    assert sliced == all_logs[2:4]


@pytest.mark.django_db
def test_getitem_int_returns_single_entry(panel_factory):
    panel_factory(message="only entry")
    result = LogQueryset(SqlBackend())[0]
    assert result["message"] == "only entry"


def test_log_manager_get_queryset_returns_log_queryset():
    assert isinstance(LogManager().get_queryset(), LogQueryset)


def test_log_manager_subclass_applies_default_filters():
    class OperatorManager(LogManager):
        def get_queryset(self):
            return (
                super()
                .get_queryset()
                .filter(
                    logger_names=["orders"],
                    min_level="WARNING",
                )
            )

    qs = OperatorManager().get_queryset()
    assert qs._filters.logger_names == ["orders"]
    assert set(qs._filters.levels) == {"WARNING", "ERROR", "CRITICAL"}


@pytest.mark.django_db
@override_settings(LOG_PANEL={"DATABASE_ALIAS": "default"})
def test_log_manager_subclass_restricts_results(panel_factory):
    panel_factory(logger_name="orders", level="WARNING")
    panel_factory(logger_name="orders", level="DEBUG")
    panel_factory(logger_name="auth", level="WARNING")

    class OperatorManager(LogManager):
        def get_queryset(self):
            return (
                super()
                .get_queryset()
                .filter(
                    logger_names=["orders"],
                    min_level="WARNING",
                )
            )

    qs = OperatorManager().get_queryset()
    assert len(qs) == 1
    logs = list(qs)
    assert logs[0]["logger_name"] == "orders"
    assert logs[0]["level"] == "WARNING"


@pytest.mark.django_db
def test_create_from_record_returns_panel_instance():
    ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
    result = Panel.objects.create_from_record(
        timestamp=ts,
        level="ERROR",
        logger_name="myapp.views",
        message="Something went wrong",
        module="views",
        pathname="/app/views.py",
        line_number=99,
    )
    assert isinstance(result, Panel)


@pytest.mark.django_db
def test_create_from_record_persists_all_fields():
    ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
    Panel.objects.create_from_record(
        timestamp=ts,
        level="ERROR",
        logger_name="myapp.views",
        message="Something went wrong",
        module="views",
        pathname="/app/views.py",
        line_number=99,
    )

    panel = Panel.objects.get()
    assert panel.timestamp == ts
    assert panel.level == "ERROR"
    assert panel.logger_name == "myapp.views"
    assert panel.message == "Something went wrong"
    assert panel.module == "views"
    assert panel.pathname == "/app/views.py"
    assert panel.line_number == 99
