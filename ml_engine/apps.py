from django.apps import AppConfig


class MlEngineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ml_engine"
    verbose_name = "ML Engine (No-Show Prediction)"

    def ready(self):
        # Wire up async risk scoring when an Appointment is saved.
        # Import here (not at module level) - otherwise there is a circular import.
        from . import signals  # noqa: F401
