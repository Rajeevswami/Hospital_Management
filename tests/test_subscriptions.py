"""
Phase 2 - Plan / Subscription / Razorpay / feature gating tests.
"""
import hashlib
import hmac
import json

import pytest
from django.test import Client, override_settings
from django.utils import timezone

from accounts.models import User
from patients.models import Patient
from subscriptions import razorpay_service as rzp
from subscriptions.gating import check_feature, check_limit
from subscriptions.models import Feature, PaymentEvent, Plan, Subscription

from .conftest import tenant_scope

pytestmark = pytest.mark.django_db

TEST_KEYS = {
    "RAZORPAY_KEY_ID": "rzp_test_dummyid",
    "RAZORPAY_KEY_SECRET": "dummy_secret_for_tests",
    "RAZORPAY_WEBHOOK_SECRET": "dummy_webhook_secret",
}


@pytest.fixture
def free_plan(db):
    return Plan.objects.create(
        name="Free", code="free", price=0, patient_limit=2, staff_limit=2,
        features_json={},
    )


@pytest.fixture
def scale_plan(db):
    return Plan.objects.create(
        name="Scale", code="scale", price=4999, patient_limit=-1, staff_limit=-1,
        features_json={Feature.AI_NO_SHOW: True, Feature.API_ACCESS: True},
    )


def make_sub(hospital, plan, status=Subscription.Status.ACTIVE, days=30):
    now = timezone.now()
    return Subscription.all_objects.create(
        hospital=hospital, plan=plan, status=status,
        current_period_start=now, current_period_end=now + timezone.timedelta(days=days),
    )


# ------------------------------------------------------------------- Plan
class TestPlan:
    def test_features_merge_with_defaults(self, free_plan, scale_plan):
        assert free_plan.has_feature(Feature.AI_NO_SHOW) is False
        assert scale_plan.has_feature(Feature.AI_NO_SHOW) is True
        # keys not in features_json default to False
        assert free_plan.features[Feature.SMS_REMINDERS] is False

    def test_unlimited_sentinel(self, scale_plan, free_plan):
        assert scale_plan.is_unlimited_patients() is True
        assert free_plan.is_unlimited_patients() is False

    def test_price_paise_conversion(self, scale_plan):
        assert scale_plan.price_paise == 499900

    def test_unknown_feature_key_rejected_on_clean(self, db):
        plan = Plan(name="Bad", code="bad", features_json={"not_a_feature": True})
        from django.core.exceptions import ValidationError
        with pytest.raises(ValidationError) as exc:
            plan.clean()
        assert "not_a_feature" in str(exc.value)


# ----------------------------------------------------------- Subscription
class TestSubscriptionAccess:
    def test_active_is_accessible(self, hospital_a, scale_plan):
        sub = make_sub(hospital_a, scale_plan)
        assert sub.is_accessible is True
        assert sub.has_feature(Feature.AI_NO_SHOW) is True

    def test_cancelled_is_not_accessible(self, hospital_a, scale_plan):
        sub = make_sub(hospital_a, scale_plan, status=Subscription.Status.CANCELLED)
        assert sub.is_accessible is False
        assert sub.has_feature(Feature.AI_NO_SHOW) is False

    def test_expired_is_not_accessible(self, hospital_a, scale_plan):
        sub = make_sub(hospital_a, scale_plan, days=-1)
        assert sub.is_expired is True
        assert sub.is_accessible is False

    def test_past_due_gets_grace_period(self, hospital_a, scale_plan):
        sub = make_sub(hospital_a, scale_plan, status=Subscription.Status.PAST_DUE, days=-1)
        # period_end passed yesterday, but within the grace (default 3 days)
        assert sub.is_accessible is True

    def test_past_due_beyond_grace_is_blocked(self, hospital_a, scale_plan):
        now = timezone.now()
        sub = Subscription.all_objects.create(
            hospital=hospital_a, plan=scale_plan, status=Subscription.Status.PAST_DUE,
            current_period_start=now - timezone.timedelta(days=40),
            current_period_end=now - timezone.timedelta(days=10),
        )
        assert sub.is_accessible is False

    def test_no_subscription_returns_none(self, hospital_a):
        assert Subscription.for_hospital(hospital_a) is None
        assert Subscription.for_hospital(None) is None

    def test_mark_payment_failed_increments_counter(self, hospital_a, scale_plan):
        sub = make_sub(hospital_a, scale_plan)
        sub.mark_payment_failed()
        sub.refresh_from_db()
        assert sub.failure_count == 1
        assert sub.status == Subscription.Status.PAST_DUE


# --------------------------------------------------------------- gating
class TestFeatureGating:
    def _request(self, hospital):
        from django.test import RequestFactory
        req = RequestFactory().get("/")
        req.hospital = hospital
        return req

    def test_no_subscription_denies(self, hospital_a, make_user):
        user = make_user(hospital_a, username="u1")
        req = self._request(hospital_a)
        req.user = user
        allowed, reason = check_feature(req, Feature.AI_NO_SHOW)
        assert allowed is False and reason == "no_subscription"

    def test_plan_without_feature_denies(self, hospital_a, free_plan, make_user):
        user = make_user(hospital_a, username="u2")
        make_sub(hospital_a, free_plan)
        req = self._request(hospital_a)
        req.user = user
        allowed, reason = check_feature(req, Feature.AI_NO_SHOW)
        assert allowed is False and reason == "not_in_plan"

    def test_plan_with_feature_allows(self, hospital_a, scale_plan, make_user):
        user = make_user(hospital_a, username="u3")
        make_sub(hospital_a, scale_plan)
        req = self._request(hospital_a)
        req.user = user
        allowed, reason = check_feature(req, Feature.AI_NO_SHOW)
        assert allowed is True

    def test_platform_admin_always_allowed(self, hospital_a, db):
        admin = User(username="ops", is_platform_admin=True, hospital=None)
        admin.set_password("x"); admin.save()
        req = self._request(hospital_a)
        req.user = admin
        allowed, reason = check_feature(req, Feature.AI_NO_SHOW)
        assert allowed is True and reason == "platform admin"

    def test_patient_limit_enforced(self, hospital_a, free_plan, make_user):
        user = make_user(hospital_a, username="u4")
        make_sub(hospital_a, free_plan)   # patient_limit = 2
        req = self._request(hospital_a)
        req.user = user
        allowed, reason, limit = check_limit(req, "patient_limit", 2)
        assert allowed is False and reason == "limit_reached" and limit == 2
        allowed, reason, _ = check_limit(req, "patient_limit", 1)
        assert allowed is True

    def test_unlimited_plan_never_hits_limit(self, hospital_a, scale_plan, make_user):
        user = make_user(hospital_a, username="u5")
        make_sub(hospital_a, scale_plan)
        req = self._request(hospital_a)
        req.user = user
        allowed, reason, _ = check_limit(req, "patient_limit", 999999)
        assert allowed is True and reason == "unlimited"


# ------------------------------------------------------- view integration
class TestGatedViews:
    def test_patient_create_blocked_when_no_subscription(
        self, client, hospital_a, make_user
    ):
        admin = make_user(hospital_a, username="adm", role=User.Role.ADMIN)
        with tenant_scope(hospital_a):
            client.force_login(admin)
            resp = client.get("/patients/add/", HTTP_HOST="acme.testserver")
        assert resp.status_code == 302
        assert "/saas/" in resp["Location"]
        with tenant_scope(hospital_a):
            assert Patient.objects.count() == 0

    def test_patient_create_allowed_with_subscription(
        self, client, hospital_a, scale_plan, make_user
    ):
        admin = make_user(hospital_a, username="adm2", role=User.Role.ADMIN)
        make_sub(hospital_a, scale_plan)
        with tenant_scope(hospital_a):
            client.force_login(admin)
            resp = client.post("/patients/add/", {
                "first_name": "New", "last_name": "Patient",
                "date_of_birth": "1990-01-01", "gender": "M",
                "blood_group": "UNK", "phone": "9000000777",
                "address": "", "emergency_contact_name": "",
                "emergency_contact_phone": "", "known_allergies": "",
            }, HTTP_HOST="acme.testserver")
        assert resp.status_code == 302
        with tenant_scope(hospital_a):
            assert Patient.objects.count() == 1

    def test_patient_limit_blocks_over_quota(
        self, client, hospital_a, free_plan, make_user, make_patient
    ):
        admin = make_user(hospital_a, username="adm3", role=User.Role.ADMIN)
        make_sub(hospital_a, free_plan)      # patient_limit = 2
        make_patient(hospital_a); make_patient(hospital_a)
        with tenant_scope(hospital_a):
            client.force_login(admin)
            resp = client.get("/patients/add/", HTTP_HOST="acme.testserver")
        assert resp.status_code == 302 and "/saas/" in resp["Location"]

    def test_billing_page_renders_plan_catalog(
        self, client, hospital_a, scale_plan, free_plan, make_user
    ):
        admin = make_user(hospital_a, username="adm4", role=User.Role.ADMIN)
        make_sub(hospital_a, scale_plan)
        with tenant_scope(hospital_a):
            client.force_login(admin)
            resp = client.get("/saas/", HTTP_HOST="acme.testserver")
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "Scale" in content and "Free" in content
        assert resp.context["current_plan"].code == "scale"

    def test_billing_page_visible_only_to_own_tenant(
        self, client, hospital_a, hospital_b, scale_plan, make_user
    ):
        """Hospital A's plan must not appear as current on Hospital B's billing page."""
        admin_b = make_user(hospital_b, username="admb", role=User.Role.ADMIN)
        make_sub(hospital_a, scale_plan)
        with tenant_scope(hospital_b):
            client.force_login(admin_b)
            resp = client.get("/saas/", HTTP_HOST="beta-city.testserver")
        assert resp.status_code == 200
        assert resp.context["current_plan"] is None


# --------------------------------------------------------------- Razorpay
class TestRazorpayConfig:
    def test_not_configured_by_default(self, settings):
        settings.RAZORPAY_KEY_ID = ""
        settings.RAZORPAY_KEY_SECRET = ""
        assert rzp.is_configured() is False
        with pytest.raises(rzp.RazorpayNotConfigured):
            rzp.get_client()

    @override_settings(**TEST_KEYS)
    def test_configured_when_keys_present(self):
        assert rzp.is_configured() is True
        client = rzp.get_client()
        assert client is not None


class TestWebhookSignature:
    @override_settings(**TEST_KEYS)
    def test_valid_signature_accepted(self):
        body = json.dumps({"event": "subscription.charged", "event_id": "ev_1"}).encode()
        sig = hmac.new(b"dummy_webhook_secret", body, hashlib.sha256).hexdigest()
        assert rzp.verify_webhook_signature(body, sig) is True

    @override_settings(**TEST_KEYS)
    def test_tampered_body_rejected(self):
        body = json.dumps({"event": "subscription.charged", "event_id": "ev_1"}).encode()
        sig = hmac.new(b"dummy_webhook_secret", body, hashlib.sha256).hexdigest()
        tampered = json.dumps({"event": "subscription.charged", "event_id": "ev_2"}).encode()
        assert rzp.verify_webhook_signature(tampered, sig) is False

    @override_settings(**TEST_KEYS)
    def test_missing_signature_rejected(self):
        assert rzp.verify_webhook_signature(b"{}", "") is False
        assert rzp.verify_webhook_signature(b"{}", None) is False

    def test_missing_webhook_secret_raises(self, settings):
        settings.RAZORPAY_WEBHOOK_SECRET = ""
        with pytest.raises(rzp.RazorpayNotConfigured):
            rzp.verify_webhook_signature(b"{}", "abc")

    @override_settings(**TEST_KEYS)
    def test_checkout_signature(self):
        sub_id, pay_id = "sub_ABC", "pay_XYZ"
        payload = f"{sub_id}|{pay_id}"
        sig = hmac.new(b"dummy_secret_for_tests", payload.encode(), hashlib.sha256).hexdigest()
        assert rzp.verify_checkout_signature(sub_id, pay_id, sig) is True
        assert rzp.verify_checkout_signature(sub_id, pay_id, "wrong") is False
        assert rzp.verify_checkout_signature("", "", "") is False


class TestWebhookEndpoint:
    def _post(self, client, payload, secret=b"dummy_webhook_secret"):
        body = json.dumps(payload).encode()
        sig = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return client.post(
            "/saas/webhook/", data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=sig,
        )

    @override_settings(**TEST_KEYS)
    def test_invalid_signature_returns_400_and_processes_nothing(self, client, db):
        resp = client.post(
            "/saas/webhook/", data=b"{\"event\": \"subscription.charged\"}",
            content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE="deadbeef",
        )
        assert resp.status_code == 400
        assert PaymentEvent.objects.count() == 0

    @override_settings(**TEST_KEYS)
    def test_charged_event_activates_subscription(self, client, hospital_a, scale_plan):
        sub = make_sub(hospital_a, scale_plan, status=Subscription.Status.PAST_DUE)
        sub.razorpay_subscription_id = "sub_TEST1"
        sub.save(update_fields=["razorpay_subscription_id"])

        resp = self._post(client, {
            "event": "subscription.charged",
            "event_id": "ev_charged_1",
            "payload": {"subscription": {"entity": {
                "id": "sub_TEST1",
                "charge_at": int((timezone.now() + timezone.timedelta(days=30)).timestamp()),
                "payment_id": "pay_1",
            }}},
        })
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.status == Subscription.Status.ACTIVE
        assert sub.last_payment_id == "pay_1"
        assert sub.failure_count == 0

    @override_settings(**TEST_KEYS)
    def test_failed_payment_marks_past_due(self, client, hospital_a, scale_plan):
        sub = make_sub(hospital_a, scale_plan)
        sub.razorpay_subscription_id = "sub_TEST2"
        sub.save(update_fields=["razorpay_subscription_id"])

        resp = self._post(client, {
            "event": "payment.failed", "event_id": "ev_fail_1",
            "payload": {"subscription": {"entity": {"id": "sub_TEST2"}}},
        })
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.status == Subscription.Status.PAST_DUE
        assert sub.failure_count == 1

    @override_settings(**TEST_KEYS)
    @pytest.mark.parametrize("event,expected", [
        ("subscription.halted", Subscription.Status.HALTED),
        ("subscription.cancelled", Subscription.Status.CANCELLED),
        ("subscription.completed", Subscription.Status.EXPIRED),
        ("subscription.paused", Subscription.Status.PAST_DUE),
    ])
    def test_status_events(self, client, hospital_a, scale_plan, event, expected):
        sub = make_sub(hospital_a, scale_plan)
        sub.razorpay_subscription_id = "sub_STATUS"
        sub.save(update_fields=["razorpay_subscription_id"])

        resp = self._post(client, {
            "event": event, "event_id": f"ev_{event}",
            "payload": {"subscription": {"entity": {"id": "sub_STATUS"}}},
        })
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.status == expected

    @override_settings(**TEST_KEYS)
    def test_duplicate_event_processed_once(self, client, hospital_a, scale_plan):
        sub = make_sub(hospital_a, scale_plan)
        sub.razorpay_subscription_id = "sub_DUP"
        sub.save(update_fields=["razorpay_subscription_id"])

        payload = {
            "event": "payment.failed", "event_id": "ev_dup",
            "payload": {"subscription": {"entity": {"id": "sub_DUP"}}},
        }
        first = self._post(client, payload)
        second = self._post(client, payload)
        assert first.status_code == 200
        assert second.status_code == 200
        sub.refresh_from_db()
        assert sub.failure_count == 1          # the count did not increase again
        assert PaymentEvent.objects.filter(event_id="ev_dup").count() == 1

    @override_settings(**TEST_KEYS)
    def test_unknown_event_is_ignored_but_logged(self, client, hospital_a, scale_plan):
        sub = make_sub(hospital_a, scale_plan)
        sub.razorpay_subscription_id = "sub_UNK"
        sub.save(update_fields=["razorpay_subscription_id"])
        resp = self._post(client, {
            "event": "some.future.event", "event_id": "ev_unk",
            "payload": {"subscription": {"entity": {"id": "sub_UNK"}}},
        })
        assert resp.status_code == 200
        ev = PaymentEvent.objects.get(event_id="ev_unk")
        assert ev.processed is True and "no handler" in ev.error

    @override_settings(**TEST_KEYS)
    def test_unknown_subscription_returns_404(self, client, db):
        resp = self._post(client, {
            "event": "subscription.charged", "event_id": "ev_ghost",
            "payload": {"subscription": {"entity": {"id": "sub_GHOST"}}},
        })
        assert resp.status_code == 404

    def test_webhook_is_reachable_without_tenant(self, client, settings):
        """The webhook is TENANT_EXEMPT - even on the root domain it should be 400, not 302."""
        settings.RAZORPAY_WEBHOOK_SECRET = "s"
        resp = client.post("/saas/webhook/", data=b"{}", content_type="application/json")
        assert resp.status_code == 400


# ------------------------------------------------------- management cmds
class TestManagementCommands:
    def test_manage_plans_seeds_defaults(self, db):
        from django.core.management import call_command
        call_command("manage_plans", verbosity=0)
        assert Plan.objects.count() == 4
        # idempotent
        call_command("manage_plans", verbosity=0)
        assert Plan.objects.count() == 4
        assert Plan.objects.get(code="scale").has_feature(Feature.AI_NO_SHOW) is True
        assert Plan.objects.get(code="free").patient_limit == 50

    def test_assign_plan_creates_active_subscription(self, hospital_a, db):
        from django.core.management import call_command
        call_command("manage_plans", verbosity=0)
        call_command("assign_plan", hospital="acme", plan="growth", verbosity=0)
        sub = Subscription.for_hospital(hospital_a)
        assert sub is not None
        assert sub.plan.code == "growth"
        assert sub.status == Subscription.Status.ACTIVE
        assert sub.is_accessible is True

    def test_assign_plan_trial(self, hospital_a, db):
        from django.core.management import call_command
        call_command("manage_plans", verbosity=0)
        call_command("assign_plan", hospital="acme", plan="starter",
                     **{"trial_days": 14, "verbosity": 0})
        sub = Subscription.for_hospital(hospital_a)
        assert sub.status == Subscription.Status.TRIALING
        assert sub.is_accessible is True

    def test_assign_plan_unknown_plan_errors(self, hospital_a, db):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with pytest.raises(CommandError):
            call_command("assign_plan", hospital="acme", plan="nope", verbosity=0)

    def test_sync_subscriptions_without_keys_exits_cleanly(self, db, settings):
        from django.core.management import call_command
        settings.RAZORPAY_KEY_ID = ""
        settings.RAZORPAY_KEY_SECRET = ""
        call_command("sync_subscriptions", verbosity=0)   # no exception should occur
