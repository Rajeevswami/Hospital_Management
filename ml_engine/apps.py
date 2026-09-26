from django.apps import AppConfig


class MlEngineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ml_engine"
    verbose_name = "ML Engine (No-Show Prediction)"

    def ready(self):
        # Appointment save hone pe async risk scoring wire karo.
        # Import yahan (na ki module level pe) - warna circular import hota hai.
        from . import signals  # noqa: F401
