from django.apps import AppConfig


class LogPanelConfig(AppConfig):
    name = "log_panel"
    verbose_name = "Log Panel"

    def ready(self) -> None:
        from log_panel.bootstrap import bootstrap_log_panel

        bootstrap_log_panel()
