# Phase 2 — Subscription & Billing (Razorpay recurring)

`subscriptions` app: `Plan` (platform catalog) + `Subscription` (per-hospital) +
`PaymentEvent` (webhook audit trail). Patient-facing `billing` app se bilkul alag —
yahan **hospital khud customer** hai.

> **Keys kabhi code mein nahi.** `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` /
> `RAZORPAY_WEBHOOK_SECRET` sirf `.env` (local) ya Render Environment (production) se.

---

## 2.1 Setup (ek baar)

```bash
# 1. Plans seed karo (idempotent)
python manage.py manage_plans
python manage.py manage_plans --list

# 2. Kisi hospital ko plan do (local testing / manual onboarding)
python manage.py assign_plan --hospital acme --plan growth --trial-days 14
python manage.py assign_plan --hospital acme --plan scale --days 30

# 3. Razorpay pe plans banao (plan_id apne aap DB mein store ho jaata hai)
python manage.py manage_plans --ensure-razorpay

# 4. Reconciliation cron (webhook miss ho jaaye to)
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

Razorpay Dashboard mein webhook add karte waqt URL `https://<tumhara-domain>/saas/webhook/`
dena (**root domain, subdomain nahi** — isliye yeh path `TENANT_EXEMPT_PATHS` mein hai)
aur events select karo: `subscription.charged`, `subscription.authenticated`,
`subscription.halted`, `subscription.cancelled`, `subscription.completed`,
`subscription.paused`, `payment.failed`.

---

## 2.2 Models

### `Plan` (tenant-scoped NAHI)
`name`, `code` (unique slug), `price`, `currency`, `interval` (monthly/yearly),
`patient_limit`, `staff_limit`, `appointment_limit`, `features_json`,
`razorpay_plan_id`, `trial_days`, `is_active`, `sort_order`.

- `patient_limit = -1` → unlimited
- `features_json` mein unknown key daalne pe `clean()` ValidationError deta hai
- `price_paise` → Razorpay ko smallest unit mein bhejne ke liye

Naya feature add karna ho to sirf `Feature` enum + plan ke `features_json` mein key
daalo — gating code change nahi karna padta.

### `Subscription` (OneToOne with Hospital)
`plan`, `status`, `current_period_start/end`, `cancel_at_period_end`, `trial_ends_at`,
`razorpay_subscription_id`, `razorpay_customer_id`, `razorpay_plan_id`,
`last_payment_id/at`, `failure_count`.

Statuses: `TRIALING / ACTIVE / PAST_DUE / PENDING / CANCELLED / EXPIRED / HALTED`.

**Access ka rule** (`is_accessible`):
- `TRIALING`, `ACTIVE`, `PENDING` → access
- `PAST_DUE` → **grace period (`SUBSCRIPTION_GRACE_DAYS`, default 3) tak access**,
  uske baad block. Ek failed payment se hospital ka kaam band karna safe nahi.
- `CANCELLED / EXPIRED / HALTED` → block

### `PaymentEvent`
`event_id` **unique** → Razorpay ki duplicate delivery dobara process nahi hoti.
Signature verify hone ke BAAD hi likha jaata hai.

---

## 2.3 Feature gating

```python
from subscriptions.gating import feature_required, check_feature, check_limit

@login_required
@feature_required("ai_no_show")
def no_show_report(request): ...
```

Ya CBV mein:
```python
class NoShowView(FeatureRequiredMixin, View):
    required_feature = "ai_no_show"
```

`check_feature(request, feature)` → `(allowed, reason)` jahan reason ∈
`no_subscription | subscription_cancelled | subscription_expired | subscription_halted |
subscription_past_due | not_in_plan | allowed`.

Deny hone pe:
- normal page → warning message + `/saas/` (billing page) pe redirect
- API (`/api/...`) → **402 JSON** `{"detail": ..., "reason": ..., "feature": ...}`

**Platform super-admin (`is_platform_admin=True`) hamesha allowed** — SaaS operator
ko testing ke liye.

Templates mein (context processor se):
```django
{% if has_feature.ai_no_show %}...{% endif %}
{{ current_plan.name }}
```

### Numeric limits (already wired)
- `patients/views.py:patient_create` → `patient_limit`
- `accounts/views.py:staff_create` → `staff_limit`

Limit hit hone pe error message + billing page pe redirect.

---

## 2.4 Razorpay integration

`subscriptions/razorpay_service.py`:

| Function | Kaam |
|---|---|
| `is_configured()` / `get_client()` | Keys check, SDK client. Na ho to `RazorpayNotConfigured` (crash nahi) |
| `ensure_plan(plan)` | Razorpay pe plan banao, `razorpay_plan_id` store karo (idempotent) |
| `create_customer(hospital)` / `create_subscription(sub)` | Recurring subscription entity |
| `checkout_payload(sub)` | Razorpay.js ke liye data (**public key only**) |
| `verify_checkout_signature(...)` | `HMAC_SHA256(key_secret, "{sub_id}|{pay_id}")` |
| `verify_webhook_signature(raw_body, sig)` | `HMAC_SHA256(webhook_secret, raw_body)`, **timing-safe compare** |
| `fetch_subscription` / `cancel_subscription` | Sync/cancel |

Webhook signature **khud** verify hota hai (SDK pe depend nahi) — security boundary
hai aur isliye unit-testable hai. `request.body` ke **exact bytes** use hote hain.

Webhook order (views.py): signature verify → `PaymentEvent` (duplicate guard) →
handler → 200. Handler fail ho to 500 + `PaymentEvent.error` mein reason store.

Flow: `/saas/` (plan catalog) → `/saas/checkout/<plan_code>/` → Razorpay.js →
`POST /saas/checkout/callback/` (signature verify) → `mark_active()`.

---

## 2.5 Files touched

**Naye (19):** `subscriptions/` app (16 files: `__init__`, `apps`, `models`,
`razorpay_service`, `gating`, `context_processors`, `views`, `urls`, `admin`,
`migrations/0001_initial`, 3 management commands + `__init__`s),
`templates/subscriptions/billing.html`, `templates/subscriptions/checkout.html`,
`tests/test_subscriptions.py`.

**Modified (11):** `hospital_system/settings.py` (app, context processor, Razorpay
settings, `/saas/webhook/` exempt), `hospital_system/urls.py`,
`hospital_system/wsgi.py` + `asgi.py` (URLconf preload — neeche dekho),
`patients/views.py` + `accounts/views.py` (limits), `templates/base.html` (Plan nav
link + badge), `requirements.txt` (`razorpay==2.0.1`), `.env.example`,
`core/management/commands/preflight.py` (subscription census).

**Migration:** `subscriptions.0001_initial` (naya app, koi existing table touch nahi hui)
→ `python manage.py migrate` safe hai, koi data risk nahi.

---

## 2.6 Verify (is sandbox mein, Django 5.2.17)

```
$ pytest tests/ -q                     →  78 passed
$ pytest tests/ --cov=. -q             →  TOTAL 77%
$ manage.py migrate (fresh)            →  subscriptions.0001_initial OK
$ manage.py makemigrations --check     →  No changes detected
$ manage.py manage_plans               →  4 plans created; --list se verify
$ manage.py provision_tenant + assign_plan --plan growth --trial-days 14
  → Subscription: plan=Growth status=TRIALING accessible=True days=13
    has_feature(ai_no_show)=False, has_feature(sms_reminders)=True   (plan ke hisaab se sahi)
$ manage.py preflight --census         →  "subscriptions: 1/2 hospitals accessible"
```

Phase 2 ke 45 naye tests: Plan features/limits, subscription accessibility + grace
period, gating (5 cases), gated views (patient create blocked/allowed/limit),
billing page render + **tenant isolation of billing page**, Razorpay config,
**signature verify/tamper/missing**, webhook 8 events + duplicate + unknown + 404,
3 management commands.

---

## 2.7 Bug jo Phase 2 tests ne pakda (Phase 1 ka, production-breaking)

**URLconf lazy import** — Django URLconf ko pehli request pe import karta hai. Us
waqt `ModelForm` ki metaclass FK ka tenant-scoped default manager evaluate karti
hai, tenant active nahi hota → `ImproperlyConfigured`. Matlab **production mein
pehli request 500** deti, aur 500 error page bhi fail hota (kyunki woh bhi URLconf
resolve karta hai).

Fix: `wsgi.py` / `asgi.py` mein `get_resolver().url_patterns` se **startup pe
preload**, aur tests mein session-scoped fixture. Ab yeh problem deploy se pehle
startup pe hi dikhegi.

Do chhote bugs: `django.utils.timezone.utc` Django 5.0 se removed hai
(`datetime.timezone.utc` use kiya), aur webhook handler ka exception test mein
500 ke peeche chhupa hua tha.

---

## 2.8 Jo verify NAHI ho saka

1. **Asli Razorpay API calls test nahi hue** — test-mode keys chahiye aur network
   sandbox se bahar. Signature logic, webhook handlers, status mapping sab local
   (HMAC khud compute karke) verify hue hain.
2. **Django 6 / Python 3.12 pe nahi chala** (sandbox 3.11.2).
3. **PostgreSQL pe nahi chala** — SQLite only.
4. **Razorpay.js checkout browser flow** manually test karna padega.

---

## 2.9 Manually kya test karna hai

```bash
python manage.py manage_plans
python manage.py assign_plan --hospital default --plan starter
# http://default.localhost:8000/saas/  -> current plan + usage + catalog dikhega

# Limits:
python manage.py assign_plan --hospital default --plan free      # patient_limit=50
#   /patients/add/  -> 50 ke baad "plan ki limit" message + /saas/ pe redirect

# Razorpay (test mode) ke saath:
#   .env mein test keys daalo -> /saas/checkout/starter/ -> Razorpay popup
#   test card: 4111 1111 1111 1111, koi future expiry, CVV 123, OTP 1111
#   Dashboard > Webhooks mein http://<tunnel>/saas/webhook/ add karo
#   (local ke liye ngrok: ngrok http 8000)
```

Regression: purane saare tests (33) abhi bhi pass hain — RBAC, booking, PDF,
tenant isolation kuch nahi toota.

---

## 2.10 Phase 3 ke liye notes

- `Feature.AI_NO_SHOW` abhi **sirf `scale` plan** mein on hai. Phase 3 ka no-show
  prediction view `@feature_required("ai_no_show")` se gate hoga.
- `Feature.API_ACCESS` Phase 4 (DRF) ke liye reserved hai.
- Celery task mein tenant context manually set karna hoga:
  `with tenant_context(appointment.hospital): ...` (middleware nahi chalta wahan).
