from datetime import UTC, datetime, timedelta
from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db.models import QuerySet

from log_panel.conf import get_setting
from log_panel.datetimes import to_database_datetime
from log_panel.models import Log, LogMessageChunk


class Command(BaseCommand):
    help = (
        'Delete log entries older than LOG_PANEL["RETENTION_DAYS"] (default: 90 days).'
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override RETENTION_DAYS for this run.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Number of records to delete per batch (default: 1000).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print how many records would be deleted without deleting them.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        retention_days: int = options["days"] or get_setting(key="RETENTION_DAYS")
        batch_size: int = options["batch_size"]
        dry_run: bool = options["dry_run"]

        cutoff: datetime = to_database_datetime(
            value=datetime.now(tz=UTC) - timedelta(days=retention_days)
        )
        base_qs: QuerySet[Log] = Log.objects.filter(timestamp__lt=cutoff)

        if dry_run:
            self.stdout.write(
                msg=f"[dry-run] Would delete {base_qs.count()} log entries older than {retention_days} days."
            )
            return

        deleted_total: int = 0
        while True:
            pks: list[Any] = list(
                base_qs.order_by("timestamp").values_list("pk", flat=True)[:batch_size]
            )
            if not pks:
                break
            count: int = self._delete_batch(pks=pks, base_qs=base_qs)
            deleted_total += count

        self.stdout.write(
            msg=self.style.SUCCESS(
                f"Deleted {deleted_total} log entries older than {retention_days} days."
            )
        )

        call_command(command_name="rebuild_log_cards", stdout=self.stdout)

    @staticmethod
    def _delete_batch(*, pks: list[Any], base_qs: QuerySet[Log]) -> int:
        """
        Delete one batch without Django's cascade collector.

        The normal QuerySet.delete() path discovers related objects before
        deleting. That is costly for very large log tables, so cleanup deletes
        the known child table first and then removes the parent rows directly.
        """
        db: str = base_qs.db
        LogMessageChunk.objects.filter(log_id__in=pks)._raw_delete(using=db)
        return base_qs.model.objects.filter(pk__in=pks)._raw_delete(using=db)
