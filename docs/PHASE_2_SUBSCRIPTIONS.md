# Phase 2 — Subscription & Billing (Razorpay recurring)

`subscriptions` app: `Plan` (platform catalog) + `Subscription` (per-hospital) +
`PaymentEvent` (webhook audit trail). Completely separate from the patient-facing
`billing` app — here **the hospital itself is the customer**.

> **Keys are never in the code.** `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` /
> `RAZORPAY_WEBHOOK_SECRET` come only from `.env` (local) or Render Environment
> (production).

---

## 2.1 Setup (one-time)

```bash
# 1. Seed the plans (idempotent)
python manage.py manage_plans
python manage.py manage_plans --list

# 2. Assign a plan to a hospital (local testing / manual onboarding)
python manage.py assign_plan --hospital acme --plan growth --trial-days 14
python manage.py assign_plan --hospital acme --plan scale --days 30

# 3. Create the plans on Razorpay (plan_id is stored in the DB automatically)
python manage.py manage_plans --ensure-razorpay

# 4. Reconciliation cron (in case a webhook is missed)
*/15 * * * * python manage.py sync_subscriptions
```

`.env`:
```
RAZORPAY_KEY_ID=rzp_test_xxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxx
RAZORPAY_WEBHOOK_SECRET=xxxxxxxx      # Dashboard > Webhooks > Add > Secret
RAZORPAY_WEBHOOK_URL=https://<domain>/saas/webhook/
SUBSCRIPTION_GRACE_DAYS=3
```

When adding the webhook in the Razorpay Dashboard, use the URL
`https://<your-domain>/saas/webhook/` (**the root domain, not a subdomain** —
that is why this path is in `TENANT_EXEMPT_PATHS`) and select these events:
`subscription.charged`, `subscription.authenticated`, `subscription.halted`,
`subscription.cancelled`, `subscription.completed`, `subscription.paused`,
`payment.failed`.

---

## 2.2 Models

### `Plan` (NOT tenant-scoped)
`name`, `code` (unique slug), `price`, `currency`, `interval` (monthly/yearly),
`patient_limit`, `staff_limit`, `appointment_limit`, `features_json`,
`razorpay_plan_id`, `trial_days`, `is_active`, `sort_order`.

- `patient_limit = -1` → unlimited
- Adding an unknown key to `features_json` raises a ValidationError in `clean()`
- `price_paise` → to send amounts to Razorpay in the smallest currency unit

To add a new feature, just add the `Feature` enum + a key in the plan's
`features_json` — the gating code does not change.

### `Subscription` (OneToOne with Hospital)
`plan`, `status`, `current_period_start/end`, `cancel_at_period_end`, `trial_ends_at`,
`razorpay_subscription_id`, `razorpay_customer_id`, `razorpay_plan_id`,
`last_payment_id/at`, `failure_count`.

Statuses: `TRIALING / ACTIVE / PAST_DUE / PENDING / CANCELLED / EXPIRED / HALTED`.

**Access rule** (`is_accessible`):
- `TRIALING`, `ACTIVE`, `PENDING` → access
- `PAST_DUE` → **access during the grace period (`SUBSCRIPTION_GRACE_DAYS`,
  default 3)**, then blocked. Stopping a hospital's work over a single failed
  payment is not safe.
- `CANCELLED / EXPIRED / HALTED` → blocked

### `PaymentEvent`
`event_id` is **unique** → duplicate Razorpay deliveries are not processed twice.
It is written only AFTER the signature verifies.

---

## 2.3 Feature gating

```python
from subscriptions.gating import feature_required, check_feature, check_limit

@login_required
@feature_required("ai_no_show")
def no_show_report(request): ...
```

Or in a CBV:
```python
class NoShowView(FeatureRequiredMixin, View):
    required_feature = "ai_no_show"
```

`check_feature(request, feature)` → `(allowed, reason)` where reason ∈
`no_subscription | subscription_cancelled | subscription_expired | subscription_halted |
subscription_past_due | not_in_plan | allowed`.

On deny:
- normal page → warning message + redirect to `/saas/` (billing page)
- API (`/api/...`) → **402 JSON** `{"detail": ..., "reason": ..., "feature": ...}`

**Platform super-admins (`is_platform_admin=True`) are always allowed** — for the
SaaS operator to test.

In templates (via the context processor):
```django
{% if has_feature.ai_no_show %}...{% endif %}
{{ current_plan.name }}
```

### Numeric limits (already wired)
- `patients/views.py:patient_create` → `patient_limit`
- `accounts/views.py:staff_create` → `staff_limit`

When a limit is hit: error message + redirect to the billing page.

---

## 2.4 Razorpay integration

`subscriptions/razorpay_service.py`:

| Function | Purpose |
|---|---|
| `is_configured()` / `get_client()` | Key check, SDK client. If missing → `RazorpayNotConfigured` (no crash) |
| `ensure_plan(plan)` | Create the plan on Razorpay, store `razorpay_plan_id` (idempotent) |
| `create_customer(hospital)` / `create_subscription(sub)` | Recurring subscription entity |
| `checkout_payload(sub)` | Data for Razorpay.js (**public key only**) |
| `verify_checkout_signature(...)` | `HMAC_SHA256(key_secret, "{sub_id}|{pay_id}")` |
| `verify_webhook_signature(raw_body, sig)` | `HMAC_SHA256(webhook_secret, raw_body)`, **timing-safe compare** |
| `fetch_subscription` / `cancel_subscription` | Sync/cancel |

The webhook signature is verified **ourselves** (no SDK dependency) — it is a
security boundary and that is why it is unit-testable. The **exact bytes** of
`request.body` are used.

Webhook order (views.py): signature verify → `PaymentEvent` (duplicate guard) →
handler → 200. If the handler fails → 500 + the reason stored in
`PaymentEvent.error`.

Flow: `/saas/` (plan catalog) → `/saas/checkout/<plan_code>/` → Razorpay.js →
`POST /saas/checkout/callback/` (signature verify) → `mark_active()`.

---

## 2.5 Files touched

**New (19):** `subscriptions/` app (16 files: `__init__`, `apps`, `models`,
`razorpay_service`, `gating`, `context_processors`, `views`, `urls`, `admin`,
`migrations/0001_initial`, 3 management commands + `__init__`s),
`templates/subscriptions/billing.html`, `templates/subscriptions/checkout.html`,
`tests/test_subscriptions.py`.

**Modified (11):** `hospital_system/settings.py` (app, context processor, Razorpay
settings, `/saas/webhook/` exempt), `hospital_system/urls.py`,
`hospital_system/wsgi.py` + `asgi.py` (URLconf preload — see below),
`patients/views.py` + `accounts/views.py` (limits), `templates/base.html` (Plan nav
link + badge), `requirements.txt` (`razorpay==2.0.1`), `.env.example`,
`core/management/commands/preflight.py` (subscription census).

**Migration:** `subscriptions.0001_initial` (new app, no existing table touched)
→ `python manage.py migrate` is safe, no data risk.

---

## 2.6 Verification (in this sandbox, Django 5.2.17)

```
$ pytest tests/ -q                     →  78 passed
$ pytest tests/ --cov=. -q             →  TOTAL 77%
$ manage.py migrate (fresh)            →  subscriptions.0001_initial OK
$ manage.py makemigrations --check     →  No changes detected
$ manage.py manage_plans               →  4 plans created; verified with --list
$ manage.py provision_tenant + assign_plan --plan growth --trial-days 14
  → Subscription: plan=Growth status=TRIALING accessible=True days=13
    has_feature(ai_no_show)=False, has_feature(sms_reminders)=True   (correct for the plan)
$ manage.py preflight --census         →  "subscriptions: 1/2 hospitals accessible"
```

Phase 2's 45 new tests: Plan features/limits, subscription accessibility + grace
period, gating (5 cases), gated views (patient create blocked/allowed/limit),
billing page render + **tenant isolation of the billing page**, Razorpay config,
**signature verify/tamper/missing**, webhook 8 events + duplicate + unknown + 404,
3 management commands.

---

## 2.7 A bug the Phase 2 tests caught (from Phase 1, production-breaking)

**URLconf lazy import** — Django imports the URLconf on the first request. At
that point `ModelForm`'s metaclass evaluates the FK's tenant-scoped default
manager, no tenant is active → `ImproperlyConfigured`. That means **in
production the first request returned a 500**, and the 500 error page failed too
(because it also resolves the URLconf).

Fix: **preload at startup** in `wsgi.py` / `asgi.py` via
`get_resolver().url_patterns`, plus a session-scoped fixture in tests. Now this
problem shows up at startup before deploy.

Two smaller bugs: `django.utils.timezone.utc` was removed in Django 5.0 (switched
to `datetime.timezone.utc`), and the webhook handler's exception was hidden
behind the 500 in tests.

---

## 2.8 What could NOT be verified

1. **Real Razorpay API calls were not tested** — test-mode keys are required and
   the network is outside the sandbox. Signature logic, webhook handlers, and
   status mapping were all verified locally (by computing HMACs ourselves).
2. **Not run on Django 6 / Python 3.12** (sandbox has 3.11.2).
3. **Not run on PostgreSQL** — SQLite only.
4. **The Razorpay.js checkout browser flow** must be tested manually.

---

## 2.9 What to test manually

```bash
python manage.py manage_plans
python manage.py assign_plan --hospital default --plan starter
# http://default.localhost:8000/saas/  -> shows current plan + usage + catalog

# Limits:
python manage.py assign_plan --hospital default --plan free      # patient_limit=50
#   /patients/add/  -> after 50, a "plan limit" message + redirect to /saas/

# With Razorpay (test mode):
#   put test keys in .env -> /saas/checkout/starter/ -> Razorpay popup
#   test card: 4111 1111 1111 1111, any future expiry, CVV 123, OTP 1111
#   add http://<tunnel>/saas/webhook/ under Dashboard > Webhooks
#   (ngrok for local: ngrok http 8000)
```

Regression: all the old tests (33) still pass — RBAC, booking, PDF, tenant
isolation, nothing broke.

---

## 2.10 Notes for Phase 3

- `Feature.AI_NO_SHOW` is currently enabled **only in the `scale` plan**. The
  Phase 3 no-show prediction view will be gated with
  `@feature_required("ai_no_show")`.
- `Feature.API_ACCESS` is reserved for Phase 4 (DRF).
- Celery tasks must set the tenant context manually:
  `with tenant_context(appointment.hospital): ...` (middleware does not run
  there).
