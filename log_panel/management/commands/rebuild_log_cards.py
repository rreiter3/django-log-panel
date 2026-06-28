from typing import Any

from django.core.management.base import BaseCommand
from django.db.models.functions import TruncDay, TruncHour

from log_panel.models import Log, LogCard, LogTimelineBucket
from log_panel.types import RangeUnit


class Command(BaseCommand):
    help = "Rebuild all LogCard and LogTimelineBucket rows from the Log table."

    def clean_up(self) -> None:
        """Delete all LogCard and LogTimelineBucket rows."""
        self.stdout.write("Deleting all LogCard rows...")
        deleted_cards, _ = LogCard.objects.all().delete()
        self.stdout.write(f"Deleted {deleted_cards} LogCard rows.")

        self.stdout.write("Deleting all LogTimelineBucket rows...")
        deleted_buckets, _ = LogTimelineBucket.objects.all().delete()
        self.stdout.write(f"Deleted {deleted_buckets} LogTimelineBucket rows.")

    def refresh_cards(self) -> int:
        self.stdout.write("Running aggregate_counts_by_logger()...")
        aggregation = Log.objects.all().aggregate_counts_by_logger()  # ty: ignore[unresolved-attribute]
        self.stdout.write("Aggregation done. Writing LogCard rows...")
        refreshed_count = 0
        for row in aggregation:
            LogCard.objects.replace_snapshot(
                logger_name=row["logger_name"],
                total=row["total"],
                total_errors=row["total_errors"] or 0,
                total_warnings=row["total_warnings"] or 0,
                last_seen=row["last_seen"],
            )
            refreshed_count += 1
        self.stdout.write(f"LogCard rows written: {refreshed_count}")
        return refreshed_count

    def refresh_timeline_buckets(self) -> int:
        buckets_created = 0
        for trunc_class, unit in (
            (TruncHour, RangeUnit.HOUR),
            (TruncDay, RangeUnit.DAY),
        ):
            self.stdout.write(f"Running timeline_aggregate() for unit={unit}...")
            timeline_agg = Log.objects.all().timeline_aggregate(trunc_class=trunc_class)  # ty: ignore[unresolved-attribute]
            self.stdout.write(
                f"Aggregation done for unit={unit}. Writing LogTimelineBucket rows..."
            )
            unit_count = 0
            for row in timeline_agg:
                LogTimelineBucket.objects.replace_snapshot(
                    logger_name=row["logger_name"],
                    bucket=row["bucket"],
                    unit=unit,
                    log_count=row["log_count"] or 0,
                    error_count=row["error_count"] or 0,
                    warning_count=row["warning_count"] or 0,
                )
                unit_count += 1
            self.stdout.write(
                f"LogTimelineBucket rows written for unit={unit}: {unit_count}"
            )
            buckets_created += unit_count

        return buckets_created

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write("Rebuilding log cards…")
        self.clean_up()

        refreshed_count: int = self.refresh_cards()
        buckets_count: int = self.refresh_timeline_buckets()

        total: int = refreshed_count + buckets_count

        self.stdout.write(self.style.SUCCESS(f"Done. {total} log card(s) rebuilt."))
