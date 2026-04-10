from datetime import UTC, datetime, timedelta
from typing import Any

from django.core.management.base import BaseCommand
from django.db.models import QuerySet, Subquery

from log_panel.conf import get_setting
from log_panel.models import Panel


class Command(BaseCommand):
    help = 'Delete Panel entries older than LOG_PANEL["TTL_DAYS"] (default: 90 days).'

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override TTL_DAYS for this run.",
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
        ttl_days: int = options["days"] or get_setting(key="TTL_DAYS")
        batch_size: int = options["batch_size"]
        dry_run: bool = options["dry_run"]

        cutoff: datetime = datetime.now(tz=UTC) - timedelta(days=ttl_days)
        base_qs: QuerySet[Panel] = Panel.objects.filter(timestamp__lt=cutoff)

        if dry_run:
            self.stdout.write(
                f"[dry-run] Would delete {base_qs.count()} log entries older than {ttl_days} days."
            )
            return

        deleted_total: int = 0

        # The Subquery runs the LIMIT entirely in SQL — no IDs pulled into Python.
        # The walrus operator assigns the deleted count and drives the loop.
        while count := Panel.objects.filter(
            pk__in=Subquery(base_qs.values("pk")[:batch_size])
        ).delete()[0]:
            deleted_total += count

        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted_total} log entries older than {ttl_days} days."
            )
        )
