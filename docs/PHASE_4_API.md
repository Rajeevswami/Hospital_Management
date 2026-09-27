# Phase 4 — REST API (DRF + JWT + Swagger)

`api/` app: tenant-aware ViewSets, JWT auth, `api_access` plan gating, drf-spectacular
Swagger UI.

---

## 1. What was built

| Item | Where |
|---|---|
| Base ViewSet (tenant scoping + permissions) | `api/viewsets.py` |
| Serializers (per-request FK scoping) | `api/serializers.py` |
| Permissions (`IsTenantMember`, `has_role`, `HasApiAccess`) | `api/permissions.py` |
| JWT with tenant claims | `api/jwt.py` |
| Endpoints | `api/views.py`, `api/urls.py` |
| Pagination | `api/pagination.py` |
| Exception handler (402 + codes) | `api/exceptions.py` |
| Swagger / OpenAPI | `/api/docs/`, `/api/redoc/`, `/api/schema/` |

New packages: `djangorestframework==3.18.1`, `djangorestframework-simplejwt==5.5.1`,
`drf-spectacular==0.30.0`.

---

## 2. Tenant resolution for the API

Three ways (the middleware tries them in this order):

1. **Subdomain** — `acme.example.com/api/patients/` (the production default)
2. **`X-Hospital-Slug: acme` header** — only on `/api/` paths. For
   localhost/Postman/mobile clients, where a subdomain is not possible.
3. **Session / logged-in user** — when using session auth from the Swagger UI

The header path is safe because:

- Managers are already tenant-scoped (the header only decides *which* tenant)
- `_guard_cross_tenant` matches the user's hospital → 403 on mismatch
- **The JWT user lookup itself goes through the tenant-scoped manager** — a
  hospital A token is never loaded in hospital B's tenant (401)

Tests for this: `test_x_hospital_slug_cannot_switch_tenant`,
`test_token_of_other_hospital_rejected`.

### API errors in JSON

The tenant middleware used to redirect to the HTML login page. For `/api/` paths
it now returns:

- Tenant unresolved → **401** `{"detail": "Tenant could not be resolved... X-Hospital-Slug..."}`
- Cross-tenant user → **403** JSON (not the HTML 403 page)

---

## 3. Auth

```
POST /api/token/            -> {access, refresh, user{...}}
POST /api/token/refresh/    -> {access}
POST /api/token/verify/     -> 200/401
```

Token claims: `user_id`, `username`, `role`, `hospital` (slug), `is_platform_admin`.
The response also includes a `user` object (so the client does not need to call
`/api/me/` right away).

**Important:** claims are for convenience only. Authorization is re-checked on
the server on every request (permissions + tenant + plan) — a client cannot do
anything by editing the role inside the token (the signature is verified, and
permissions are not read from claims).

Login itself is tenant-scoped: `TenantModelBackend` only authenticates users of
the current hospital, so even with the same `admin` username in both hospitals
there is no cross-login.

Settings: `SIMPLE_JWT_ACCESS_TOKEN_MINUTES` (60), `SIMPLE_JWT_REFRESH_TOKEN_DAYS` (7),
`ROTATE_REFRESH_TOKENS=True`.

---

## 4. Gating

The `HasApiAccess` permission class is on every data ViewSet:

```python
allowed, reason = check_feature(request, Feature.API_ACCESS)
if not allowed:
    raise ApiFeatureDenied(feature=..., reason=...)   # HTTP 402
```

Response:

```json
{"detail": "This API is not included in your current plan.",
 "reason": "not_in_plan", "feature": "api_access", "code": "feature_not_in_plan"}
```

- **402** (not 403) — this is not a permission issue, it is a PLAN issue. It
  mirrors the HTML-side behaviour.
- The token endpoint and docs are NOT gated — otherwise a client would not even
  discover that the API exists.
- On `POST /api/patients/` the Phase 2 `patient_limit` applies as well
  (`limit_reached` → 402).

---

## 5. Endpoints

| Method | Path | Access |
|---|---|---|
| POST | `/api/token/` `/api/token/refresh/` `/api/token/verify/` | public (tenant required) |
| GET | `/api/` | public (discovery) |
| GET | `/api/me/` `/api/hospital/` `/api/subscription/` `/api/features/` | any staff |
| CRUD | `/api/patients/` | read: all · write: ADMIN, RECEPTIONIST |
| GET | `/api/patients/<id>/appointments/` | any staff |
| CRUD | `/api/doctors/` | read: all · write: ADMIN |
| CRUD | `/api/appointments/` | read: all · write: ADMIN, RECEPTIONIST, DOCTOR |
| GET | `/api/appointments/today/` | any staff |
| PATCH | `/api/appointments/<id>/status/` | ADMIN, RECEPTIONIST, DOCTOR |
| GET | `/api/appointments/<id>/risk/` | any staff |
| GET | `/api/invoices/` | ADMIN, RECEPTIONIST (read-only) |
| GET | `/api/staff/` | ADMIN |
| GET | `/api/risks/` `/api/risks/high/` | any staff (read-only) |

On list endpoints: `?search=`, `?ordering=`, `?page=`, `?page_size=` (max 200)
and exact-match filters from `filter_fields` (`?status=PAID`, `?doctor=3`,
`?appointment_date=2026-10-01`).

Invalid filter value → **400** (not silently ignored — otherwise the client
would think the filter applied). The Doctor role gets only its own appointments
in the appointments queryset (like the existing HTML RBAC).

The `hospital` field is never accepted from the client — `perform_create` sets it
from the request's tenant. Test: `test_hospital_field_cannot_be_spoofed`.

---

## 6. Files touched

**New:** `api/` (10 files), `tests/test_api.py` (46 tests), `docs/PHASE_4_API.md`

**Modified:**

| File | What |
|---|---|
| `hospital_system/settings.py` | `rest_framework` + `drf_spectacular` + `api` apps, `REST_FRAMEWORK`, `SIMPLE_JWT`, `SPECTACULAR_SETTINGS`, `import datetime as dt` |
| `hospital_system/urls.py` | `path('api/', include('api.urls'))` |
| `tenants/middleware.py` | `X-Hospital-Slug` header on `/api/` + JSON 401/403 responses |
| `requirements.txt` | DRF, SimpleJWT, drf-spectacular |
| `.env.example` | JWT lifetimes + Swagger/API notes |

---

## 7. Migration commands

**No database migration** — the `api` app has no models.

```bash
pip install -r requirements.txt
python manage.py check                    # 2 pre-existing warnings, no new ones
python manage.py spectacular --file schema.yml --validate   # schema sanity
```

---

## 8. Verification

```
pytest tests/test_api.py    -> 46 passed
pytest tests/               -> 166 passed, coverage 84%
manage.py check             -> 2 issues (axes.W006, auth.W004) - both pre-existing
spectacular --validate      -> Errors: 0, Warnings: 18 (7 unique)
```

`api/` coverage: views 98%, viewsets 97%, serializers 92%, urls 100%, jwt 100%,
permissions 85%, exceptions 88%.

The 46 tests cover: JWT (issue/refresh/verify/tamper/cross-tenant), tenant
isolation (list, detail 404, foreign FK reject, hospital spoof, header switch),
RBAC (role-wise), plan gating (402 + reasons), resources (CRUD, actions, search,
pagination, ordering, invalid filter), context endpoints, Swagger + OpenAPI
schema validation.

### 2 real bugs found in this phase (both fixed)

1. **FK querysets became unscoped at import time.**
   `PrimaryKeyRelatedField(queryset=Patient.objects.all())` is evaluated at class
   definition time, when tenant enforcement is OFF — the queryset object stayed
   UNSCOPED. That meant an API client could create an appointment with another
   hospital's `patient` pk. Fix: `TenantModelSerializer.get_fields()` re-binds the
   queryset on every request via
   `model.objects.for_hospital(request.hospital)` (the HTML forms'
   `TenantModelForm` does the same).
   Test: `test_cannot_use_foreign_patient_fk`.

2. **`@extend_schema` was in the wrong decorator order** — being under
   `@api_view` caused the schema attributes to be wrapped, and spectacular
   emitted "unable to guess serializer" warnings on 5 endpoints. The order was
   flipped (extend_schema on top), warnings gone.

---

## 9. What was NOT verified

| Item | Status |
|---|---|
| Token blacklist / revocation | **Not implemented** — the `token_blacklist` app is not installed. Logout = the client discards the token; `ROTATE_REFRESH_TOKENS` is on. If needed, add `rest_framework_simplejwt.token_blacklist` to INSTALLED_APPS. |
| Rate limiting / throttling | **Not implemented** — no DRF throttling on the API. django-axes only applies to login attempts. Adding `DEFAULT_THROTTLE_CLASSES` before production is advisable. |
| API tests on Postgres | **Unverified** — tests ran on SQLite (CI runs them on Postgres). |
| API with a real Razorpay-paid plan | **Unverified** — tests seed the subscription. |

---

## 10. Manual test steps

```bash
# 1. token
curl -X POST http://acme.localhost:8000/api/token/ \
  -d "username=admin&password=AcmePass!123"

# 2. data
TOKEN=<access>
curl -H "Authorization: Bearer $TOKEN" http://acme.localhost:8000/api/patients/

# 3. isolation: A's token on B
curl -H "Authorization: Bearer $TOKEN" -H "Host: beta-city.localhost" \
     http://localhost:8000/api/me/          # -> 401

# 4. gating: from a hospital on the free plan
curl -H "Authorization: Bearer $TOKEN" http://free.localhost:8000/api/patients/
#    -> 402 {"reason":"not_in_plan","feature":"api_access"}

# 5. Swagger
open http://acme.localhost:8000/api/docs/
```

---

## 11. Notes for Phase 5

- CI has a `spectacular --validate` step — the build fails if the schema breaks.
- Sentry covers the `api` app automatically (DjangoIntegration).
- There is no throttling on the API yet — Phase 5's Docker/CI setup does not
  include performance testing; add throttle classes before production traffic.
