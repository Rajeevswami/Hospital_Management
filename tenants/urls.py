from django.urls import path

from . import views

app_name = "tenants"

urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
]
