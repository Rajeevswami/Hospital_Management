from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    path("", views.billing, name="billing"),
    path("checkout/<slug:plan_code>/", views.start_checkout, name="checkout"),
    path("checkout/callback/", views.checkout_callback, name="checkout_callback"),
    path("cancel/", views.cancel, name="cancel"),
    # Razorpay webhook - must be in TENANT_EXEMPT_PATHS
    path("webhook/", views.razorpay_webhook, name="razorpay_webhook"),
    path("webhook/probe/", views.webhook_probe, name="webhook_probe"),
]
