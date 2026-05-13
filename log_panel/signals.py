from django.dispatch import Signal

from log_panel.types import ThresholdAlertEvent

log_threshold_reached = Signal()

__all__: list[str] = ["ThresholdAlertEvent", "log_threshold_reached"]
