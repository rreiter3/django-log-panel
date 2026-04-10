from datetime import UTC, datetime, timedelta
from io import StringIO

import pytest
import time_machine
from django.core.management import call_command
from django.test import override_settings

from log_panel.models import Panel

FROZEN_NOW = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)


def old(days: int) -> datetime:
    return FROZEN_NOW - timedelta(days=days)


@pytest.mark.django_db
@time_machine.travel(FROZEN_NOW, tick=False)
def test_deletes_entries_older_than_ttl(panel_factory):
    panel_factory(timestamp=old(100))
    panel_factory(timestamp=old(1))
    call_command("delete_old_logs", stdout=StringIO())
    assert Panel.objects.count() == 1


@pytest.mark.django_db
@time_machine.travel(FROZEN_NOW, tick=False)
def test_respects_days_override_deletes_entry(panel_factory):
    panel_factory(timestamp=old(40))
    call_command("delete_old_logs", days=30, stdout=StringIO())
    assert Panel.objects.count() == 0


@pytest.mark.django_db
@time_machine.travel(FROZEN_NOW, tick=False)
def test_respects_days_override_keeps_entry(panel_factory):
    panel_factory(timestamp=old(40))
    call_command("delete_old_logs", days=50, stdout=StringIO())
    assert Panel.objects.count() == 1


@pytest.mark.django_db
@time_machine.travel(FROZEN_NOW, tick=False)
def test_dry_run_does_not_delete(panel_factory):
    panel_factory(timestamp=old(100))
    panel_factory(timestamp=old(100))
    call_command("delete_old_logs", dry_run=True, stdout=StringIO())
    assert Panel.objects.count() == 2


@pytest.mark.django_db
@time_machine.travel(FROZEN_NOW, tick=False)
def test_dry_run_prints_count_to_stdout(panel_factory):
    panel_factory(timestamp=old(100))
    panel_factory(timestamp=old(100))
    out = StringIO()
    call_command("delete_old_logs", dry_run=True, stdout=out)
    assert "2" in out.getvalue()


@pytest.mark.django_db
@time_machine.travel(FROZEN_NOW, tick=False)
def test_success_message_printed_after_deletion(panel_factory):
    panel_factory(timestamp=old(100))

    out = StringIO()
    call_command("delete_old_logs", stdout=out)

    assert "Deleted" in out.getvalue()
    assert "1" in out.getvalue()


@pytest.mark.django_db
@time_machine.travel(FROZEN_NOW, tick=False)
def test_deletes_in_batches():
    Panel.objects.bulk_create(
        [
            Panel(
                timestamp=old(100),
                level="INFO",
                logger_name="myapp",
                message="old",
                module="m",
                pathname="/p",
                line_number=1,
            )
            for _ in range(2500)
        ]
    )

    call_command("delete_old_logs", batch_size=1000, stdout=StringIO())
    assert Panel.objects.count() == 0


@pytest.mark.django_db
@override_settings(LOG_PANEL={"TTL_DAYS": 5})
@time_machine.travel(FROZEN_NOW, tick=False)
def test_uses_ttl_days_from_settings(panel_factory):
    panel_factory(timestamp=old(10))
    call_command("delete_old_logs", stdout=StringIO())
    assert Panel.objects.count() == 0
