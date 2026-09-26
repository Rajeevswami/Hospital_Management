"""
Phase 3 - No-show prediction tests.

Cover hota hai:
  * rule-based (cold start) engine ka behaviour
  * feature extraction + leakage guard (current appointment history mein nahi ginta)
  * Celery task (eager) appointment create pe risk bana deta hai
  * train -> joblib artifact -> sklearn engine pe switch
  * feature gating (ai_no_show) + tenant isolation
"""
import datetime as dt

import pytest
from django.test import Client, override_settings
from django.utils import timezone

from accounts.models import User
from appointments.models import Appointment
from ml_engine import rules
from ml_engine.features import FEATURE_NAMES, extract_for_appointment
from ml_engine.models import AppointmentRisk, NoShowModelArtifact
from ml_engine.predict import readiness, score_appointment
from ml_engine.sklearn_model import get_predictor, train
from subscriptions.models import Feature, Plan, Subscription

from .conftest import tenant_scope

pytestmark = pytest.mark.django_db


def _feat(**overrides):
    """Poora FEATURE_NAMES dict, sirf diye gaye keys override - rules unit-test ke liye."""
    base = {name: 0 for name in FEATURE_NAMES}
    base.update({
        "lead_days": 7, "lead_hours": 168, "day_of_week": 2, "hour_of_day": 10,
        "patient_age": 35, "doctor_prev_appts": 50, "fee": 500,
        "has_reason": 1, "has_phone": 1,
    })
    base.update(overrides)
    return base


# ------------------------------------------------------------------ fixtures
@pytest.fixture
def doctor(hospital_a, make_user):
    from doctors.models import Doctor

    user = make_user(hospital_a, username="dr.ai", role=User.Role.DOCTOR)
    with tenant_scope(hospital_a):
        return Doctor.objects.create(user=user, specialization="General Medicine", hospital=hospital_a)


@pytest.fixture
def patient(hospital_a, make_patient):
    return make_patient(hospital_a, first_name="Ramesh")


def make_appt(patient, doctor, *, days=7, hour=10, weekday=None, status=None, reason="Fever"):
    """Ek appointment banao. `weekday` do to us din pe shift ho jaata hai."""
    date = timezone.now().date() + dt.timedelta(days=days)
    if weekday is not None:
        while date.weekday() != weekday:
            date += dt.timedelta(days=1)
    kwargs = dict(
        patient=patient, doctor=doctor, appointment_date=date,
        appointment_time=dt.time(hour, 0), reason=reason, hospital=patient.hospital,
    )
    if status:
        kwargs["status"] = status
    return Appointment.objects.create(**kwargs)


# ------------------------------------------------------------ rule engine
class TestRuleEngine:
    def test_score_is_clamped_probability(self):
        score, reasons = rules.score(_feat(lead_days=0, is_same_day=1))
        assert 0.01 <= score <= 0.99
        assert isinstance(reasons, list) and reasons
        assert all({"factor", "impact", "note"} <= set(r) for r in reasons)

    def test_long_lead_time_scores_higher_than_same_day(self):
        same_day, _ = rules.score(_feat(lead_days=0.2, is_same_day=1))
        far, _ = rules.score(_feat(lead_days=45, is_same_day=0))
        assert far > same_day

    def test_repeat_no_shows_raise_score(self):
        clean, clean_reasons = rules.score(_feat(patient_prev_appts=5, patient_prev_no_shows=0))
        repeat, repeat_reasons = rules.score(_feat(patient_prev_appts=5, patient_prev_no_shows=3))
        assert repeat > clean
        assert [r["factor"] for r in repeat_reasons].count("patient_history") == 1

    def test_missing_phone_raises_score(self):
        with_phone, _ = rules.score(_feat(has_phone=1))
        no_phone, _ = rules.score(_feat(has_phone=0))
        assert no_phone > with_phone

    def test_classify_uses_thresholds(self):
        assert rules.classify(0.05) == AppointmentRisk.Level.LOW
        assert rules.classify(0.50) == AppointmentRisk.Level.MEDIUM
        assert rules.classify(0.90) == AppointmentRisk.Level.HIGH

    @override_settings(ML_HIGH_RISK_THRESHOLD=0.30, ML_MEDIUM_RISK_THRESHOLD=0.10)
    def test_thresholds_are_configurable(self):
        assert rules.classify(0.50) == AppointmentRisk.Level.HIGH


# ------------------------------------------------------------- features
class TestFeatures:
    def test_feature_vector_matches_declared_names(self, patient, doctor):
        with tenant_scope(patient.hospital):
            appt = make_appt(patient, doctor, days=7)
            feats = extract_for_appointment(appt)
        assert set(feats) == set(FEATURE_NAMES)

    def test_no_leakage_from_current_appointment(self, patient, doctor):
        """Current appointment khud patient history mein count NAHI hona chahiye."""
        with tenant_scope(patient.hospital):
            # purani history PAST mein (negative days) - warna woh "upcoming" ke
            # baad ki date maani jaayegi aur history features 0 rahenge
            make_appt(patient, doctor, days=-30, status=Appointment.Status.NO_SHOW)
            make_appt(patient, doctor, days=-25, status=Appointment.Status.COMPLETED)
            upcoming = make_appt(patient, doctor, days=5)
            feats = extract_for_appointment(upcoming)
        # 2 purane appointments hi dikhne chahiye, upcoming (SCHEDULED) nahi
        assert feats["patient_prev_appts"] == 2
        assert feats["patient_prev_no_shows"] == 1
        assert feats["is_first_visit"] == 0

    def test_future_appointments_ignored_in_history(self, patient, doctor):
        with tenant_scope(patient.hospital):
            make_appt(patient, doctor, days=40, status=Appointment.Status.NO_SHOW)
            upcoming = make_appt(patient, doctor, days=3)
            feats = extract_for_appointment(upcoming)
        assert feats["patient_prev_appts"] == 0
        assert feats["is_first_visit"] == 1


# ------------------------------------------------------- Celery task path
class TestAsyncScoring:
    def test_risk_row_created_on_appointment_create(self, patient, doctor):
        """post_save signal -> celery task (eager) -> AppointmentRisk bana."""
        with tenant_scope(patient.hospital):
            appt = make_appt(patient, doctor, days=10)
            appt.refresh_from_db()
            assert hasattr(appt, "risk")
            assert appt.risk.engine == AppointmentRisk.Engine.RULES
            assert 0.01 <= appt.risk.score <= 0.99

    def test_task_returns_result_dict(self, patient, doctor):
        from ml_engine.tasks import score_appointment_task

        with tenant_scope(patient.hospital):
            appt = make_appt(patient, doctor, days=10)
            res = score_appointment_task.delay(appt.pk).get()
        assert res["scored"] is True
        assert res["level"] in dict(AppointmentRisk.Level.choices)

    def test_task_handles_missing_appointment(self):
        from ml_engine.tasks import score_appointment_task

        res = score_appointment_task.delay(999999).get()
        assert res["scored"] is False
        assert res["reason"] == "not found"

    @override_settings(ML_AUTO_SCORE_ON_CREATE=False)
    def test_auto_score_can_be_disabled(self, patient, doctor):
        with tenant_scope(patient.hospital):
            appt = make_appt(patient, doctor, days=10)
        assert not AppointmentRisk.all_objects.filter(appointment=appt).exists()

    def test_score_appointment_is_upsert(self, patient, doctor):
        with tenant_scope(patient.hospital):
            appt = make_appt(patient, doctor, days=10)
            first = score_appointment(appt)
            second = score_appointment(appt)
            assert first.pk == second.pk
            assert AppointmentRisk.all_objects.filter(appointment=appt).count() == 1


# ------------------------------------------------------- training / sklearn
class TestTraining:
    def _seed_history(self, patient, doctor, n=60):
        """Synthetic pattern: Monday appointments zyada no-show hote hain."""
        import random

        rnd = random.Random(11)
        with tenant_scope(patient.hospital):
            for i in range(n):
                weekday = 0 if i % 2 == 0 else 3
                no_show = rnd.random() < (0.8 if weekday == 0 else 0.05)
                make_appt(
                    patient, doctor, days=-(10 + i), hour=9 + (i % 8), weekday=weekday,
                    status=Appointment.Status.NO_SHOW if no_show else Appointment.Status.COMPLETED,
                )

    def test_cold_start_verdict_when_no_data(self, patient, doctor):
        with tenant_scope(patient.hospital):
            info = readiness(patient.hospital)
        assert info["ml_ready"] is False
        assert "COLD START" in info["verdict"]

    def test_train_refuses_below_threshold(self, patient, doctor):
        result = train(hospital=patient.hospital, min_rows=10_000)
        assert result["ok"] is False
        assert "rule-based" in result["reason"]
        assert NoShowModelArtifact.objects.count() == 0

    def test_train_then_sklearn_engine_is_used(self, patient, doctor):
        self._seed_history(patient, doctor, n=60)

        with tenant_scope(patient.hospital):
            info = readiness(patient.hospital)
            assert info["ml_ready"] is True, info

            result = train(hospital=patient.hospital, min_rows=40)
            assert result["ok"] is True, result
            assert 0.5 < result["metrics"]["roc_auc"] <= 1.0

            artifact = NoShowModelArtifact.objects.get(version=result["version"])
            assert artifact.is_active is True
            assert artifact.hospital == patient.hospital
            assert len(artifact.feature_importances) == len(FEATURE_NAMES)

            # ab prediction trained model se honi chahiye
            predictor = get_predictor(patient.hospital)
            assert predictor is not None and predictor.version == result["version"]

            upcoming = make_appt(patient, doctor, days=6, weekday=0)
            risk = score_appointment(upcoming)
            assert risk.engine == AppointmentRisk.Engine.SKLEARN
            assert risk.model_version == result["version"]

    def test_retrain_deactivates_previous_and_cleans_file(self, patient, doctor):
        self._seed_history(patient, doctor, n=60)
        with tenant_scope(patient.hospital):
            first = train(hospital=patient.hospital, min_rows=40)
            assert first["ok"] is True
            from ml_engine.sklearn_model import model_dir

            first_path = model_dir() / first["artifact"].relative_path
            assert first_path.exists()

            second = train(hospital=patient.hospital, min_rows=40)
            assert second["ok"] is True
            assert NoShowModelArtifact.objects.filter(is_active=True).count() == 1
            assert not first_path.exists()  # purani file cleanup ho gayi
            # global model na hone par bhi hospital-specific hi pick ho
            assert get_predictor(patient.hospital).version == second["version"]

    def test_global_model_fallback_when_no_hospital_model(self, patient, doctor):
        self._seed_history(patient, doctor, n=60)
        with tenant_scope(patient.hospital):
            result = train(hospital=None, min_rows=40)   # global
        assert result["ok"] is True
        with tenant_scope(patient.hospital):
            assert get_predictor(patient.hospital) is not None
            assert NoShowModelArtifact.active_for(patient.hospital).hospital is None

    def test_corrupt_model_file_falls_back_to_rules(self, patient, doctor):
        self._seed_history(patient, doctor, n=60)
        with tenant_scope(patient.hospital):
            result = train(hospital=patient.hospital, min_rows=40)
            from ml_engine.sklearn_model import model_dir

            (model_dir() / result["artifact"].relative_path).write_bytes(b"not a joblib file")
            assert get_predictor(patient.hospital) is None

            upcoming = make_appt(patient, doctor, days=8)
            risk = score_appointment(upcoming)
            assert risk.engine == AppointmentRisk.Engine.RULES


# ----------------------------------------------------------- views / gating
@pytest.fixture
def scale_plan(db):
    return Plan.objects.create(
        name="Scale", code="scale", price=4999, patient_limit=-1, staff_limit=-1,
        features_json={Feature.AI_NO_SHOW: True},
    )


@pytest.fixture
def free_plan(db):
    return Plan.objects.create(
        name="Free", code="free", price=0, patient_limit=2, staff_limit=2, features_json={},
    )


def subscribe(hospital, plan, days=30):
    now = timezone.now()
    return Subscription.all_objects.create(
        hospital=hospital, plan=plan, status=Subscription.Status.ACTIVE,
        current_period_start=now, current_period_end=now + timezone.timedelta(days=days),
    )


class TestDashboard:
    def test_ai_page_gated_without_feature(self, hospital_a, free_plan, make_user):
        admin = make_user(hospital_a, username="admin.ai", role=User.Role.ADMIN)
        subscribe(hospital_a, free_plan)

        client = Client()
        client.force_login(admin)
        resp = client.get("/ai/no-show/", HTTP_HOST="acme.testserver")
        assert resp.status_code == 302
        assert resp.url.startswith("/saas/")

    def test_ai_page_visible_with_feature(self, hospital_a, scale_plan, make_user):
        admin = make_user(hospital_a, username="admin.ai2", role=User.Role.ADMIN)
        subscribe(hospital_a, scale_plan)

        client = Client()
        client.force_login(admin)
        resp = client.get("/ai/no-show/", HTTP_HOST="acme.testserver")
        assert resp.status_code == 200
        assert b"No-Show Risk" in resp.content

    def test_dashboard_shows_high_risk_only_with_feature(self, hospital_a, hospital_b,
                                                         free_plan, scale_plan, make_user):
        admin_a = make_user(hospital_a, username="dash.a", role=User.Role.ADMIN)
        subscribe(hospital_a, scale_plan)

        client = Client()
        client.force_login(admin_a)
        resp = client.get("/", HTTP_HOST="acme.testserver")
        assert resp.status_code == 200
        assert b"High Risk Appointments" in resp.content

    def test_navbar_link_follows_plan(self, hospital_a, scale_plan, free_plan, make_user):
        """feature_flags context processor se navbar ka AI link aata/jaata hai."""
        admin = make_user(hospital_a, username="nav.ai", role=User.Role.ADMIN)
        sub = subscribe(hospital_a, scale_plan)

        client = Client()
        client.force_login(admin)
        assert b"AI Risk" in client.get("/", HTTP_HOST="acme.testserver").content

        sub.plan = free_plan
        sub.save()
        assert b"AI Risk" not in client.get("/", HTTP_HOST="acme.testserver").content

    def test_dashboard_hides_panel_on_free_plan(self, hospital_b, free_plan, make_user):
        admin_b = make_user(hospital_b, username="dash.b", role=User.Role.ADMIN)
        subscribe(hospital_b, free_plan)

        client = Client()
        client.force_login(admin_b)
        resp = client.get("/", HTTP_HOST="beta-city.testserver")
        assert resp.status_code == 200
        assert b"High Risk Appointments" not in resp.content


# ------------------------------------------------------ management commands
class TestCommands:
    def _seed(self, patient, doctor, n=50):
        import random

        rnd = random.Random(3)
        with tenant_scope(patient.hospital):
            for i in range(n):
                weekday = 0 if i % 2 == 0 else 3
                no_show = rnd.random() < (0.8 if weekday == 0 else 0.05)
                make_appt(
                    patient, doctor, days=-(10 + i), hour=9 + (i % 8), weekday=weekday,
                    status=Appointment.Status.NO_SHOW if no_show else Appointment.Status.COMPLETED,
                )

    def test_census_command_reports_cold_start(self, patient, doctor, capsys):
        from django.core.management import call_command

        call_command("no_show_census", "--hospital", patient.hospital.slug)
        out = capsys.readouterr().out
        assert "COLD START" in out
        assert patient.hospital.slug in out

    def test_census_by_month(self, patient, doctor, capsys):
        from django.core.management import call_command

        self._seed(patient, doctor, n=10)
        call_command("no_show_census", "--hospital", patient.hospital.slug, "--by-month")
        assert "month-wise" in capsys.readouterr().out

    def test_train_command_dry_run(self, patient, doctor, capsys):
        from django.core.management import call_command

        call_command("train_no_show", "--hospital", patient.hospital.slug, "--dry-run")
        out = capsys.readouterr().out
        assert "verdict" in out
        assert NoShowModelArtifact.objects.count() == 0

    def test_train_command_cold_start_does_not_fail(self, patient, doctor, capsys):
        from django.core.management import call_command

        call_command("train_no_show", "--hospital", patient.hospital.slug, "--min-rows", "5000")
        assert "Rule-based engine" in capsys.readouterr().out
        assert NoShowModelArtifact.objects.count() == 0

    def test_train_command_creates_active_artifact(self, patient, doctor, capsys):
        from django.core.management import call_command

        self._seed(patient, doctor, n=50)
        call_command("train_no_show", "--hospital", patient.hospital.slug, "--min-rows", "40")
        out = capsys.readouterr().out
        assert "TRAINED:" in out
        artifact = NoShowModelArtifact.objects.get(is_active=True)
        assert artifact.version in out

    def test_train_command_unknown_hospital(self, capsys):
        from django.core.management import call_command

        call_command("train_no_show", "--hospital", "nope-not-here")
        assert "nahi mila" in capsys.readouterr().err

    def test_score_appointments_backfills(self, patient, doctor, capsys):
        """Signal sirf NAYE appointments pe chalta hai - backfill command purane bhar deta hai."""
        from django.core.management import call_command

        with tenant_scope(patient.hospital):
            for i in range(3):
                make_appt(patient, doctor, days=20 + i, hour=9 + i)
            AppointmentRisk.all_objects.filter(
                appointment__hospital=patient.hospital
            ).delete()
            assert AppointmentRisk.all_objects.filter(appointment__hospital=patient.hospital).count() == 0

        call_command("score_appointments", "--hospital", patient.hospital.slug)
        assert "scored" in capsys.readouterr().out
        with tenant_scope(patient.hospital):
            assert AppointmentRisk.objects.count() == 3

    def test_score_appointments_async_mode(self, patient, doctor, capsys):
        from django.core.management import call_command

        with tenant_scope(patient.hospital):
            make_appt(patient, doctor, days=20)
            AppointmentRisk.all_objects.filter(appointment__hospital=patient.hospital).delete()
        call_command("score_appointments", "--hospital", patient.hospital.slug, "--async")
        assert "queued" in capsys.readouterr().out
        with tenant_scope(patient.hospital):
            assert AppointmentRisk.objects.count() == 1


# --------------------------------------------------------- tenant isolation
class TestTenantIsolation:
    def test_high_risk_upcoming_is_tenant_scoped(self, hospital_a, hospital_b,
                                                 make_patient, make_user):
        from doctors.models import Doctor

        user_a = make_user(hospital_a, username="doc.a", role=User.Role.DOCTOR)
        user_b = make_user(hospital_b, username="doc.b", role=User.Role.DOCTOR)

        with tenant_scope(hospital_a):
            doc_a = Doctor.objects.create(user=user_a, specialization="General", hospital=hospital_a)
            p_a = make_patient(hospital_a, first_name="Aonly")
            make_appt(p_a, doc_a, days=5)

        with tenant_scope(hospital_b):
            doc_b = Doctor.objects.create(user=user_b, specialization="General", hospital=hospital_b)
            p_b = make_patient(hospital_b, first_name="Bonly")
            make_appt(p_b, doc_b, days=5)

        with tenant_scope(hospital_a):
            risks = list(AppointmentRisk.objects.all())
            assert risks, "A ke liye risk banna chahiye tha"
            assert all(r.hospital_id == hospital_a.pk for r in risks)
            assert all(r.appointment.patient.first_name != "Bonly" for r in risks)
            high = list(AppointmentRisk.high_risk_upcoming())
            assert all(r.hospital_id == hospital_a.pk for r in high)

        with tenant_scope(hospital_b):
            risks = list(AppointmentRisk.objects.all())
            assert all(r.hospital_id == hospital_b.pk for r in risks)

    def test_artifact_lookup_uses_unscoped_manager_for_global(self, hospital_a, hospital_b):
        """Global artifact ka hospital NULL hai - tenant-scoped manager use kiya to kabhi na milega."""
        artifact = NoShowModelArtifact.objects.create(
            version="gbm_global_test", hospital=None, relative_path="global/x.joblib",
            training_rows=500, positive_rate=0.3, is_active=True,
        )
        with tenant_scope(hospital_b):
            assert NoShowModelArtifact.active_for(hospital_b) == artifact
        with tenant_scope(hospital_a):
            assert NoShowModelArtifact.active_for(hospital_a) == artifact
