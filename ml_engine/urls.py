from django.urls import path

from . import views

app_name = "ml_engine"

urlpatterns = [
    path("no-show/", views.no_show_dashboard, name="no_show_dashboard"),
    path("no-show/<int:pk>/rescore/", views.rescore, name="rescore"),
    path("no-show/rescore-all/", views.rescore_all, name="rescore_all"),
]
