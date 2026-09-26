# Phase 4 — REST API (DRF + JWT + Swagger)

`api/` app: tenant-aware ViewSets, JWT auth, `api_access` plan gating, drf-spectacular
Swagger UI.

---

## 1. Kya bana

| Cheez | Kahan |
|---|---|
| Base ViewSet (tenant scoping + permissions) | `api/viewsets.py` |
| Serializers (per-request FK scoping) | `api/serializers.py` |
| Permissions (`IsTenantMember`, `has_role`, `HasApiAccess`) | `api/permissions.py` |
| JWT with tenant claims | `api/jwt.py` |
| Endpoints | `api/views.py`, `api/urls.py` |
| Pagination | `api/pagination.py` |
| Exception handler (402 + codes) | `api/exceptions.py` |
| Swagger / OpenAPI | `/api/docs/`, `/api/redoc/`, `/api/schema/` |

Naye packages: `djangorestframework==3.18.1`, `djangorestframework-simplejwt==5.5.1`,
`drf-spectacular==0.30.0`.

---

## 2. Tenant resolution for API

Teen tareeke (middleware isi order mein try karta hai):

1. **Subdomain** — `acme.example.com/api/patients/` (production ka default)
2. **`X-Hospital-Slug: acme` header** — sirf `/api/` paths pe. Localhost/Postman/mobile
   clients ke liye, jahan subdomain possible nahi hota.
3. **Session / logged-in user** — Swagger UI se session auth ke waqt

Header wala raasta safe hai kyunki:

- Managers already tenant-scoped hain (header se sirf *kaunsa* tenant decide hota hai)
- `_guard_cross_tenant` user ka hospital match karta hai → mismatch pe 403
- **JWT user lookup khud tenant-scoped manager se hota hai** — hospital A ka token
  hospital B ke tenant mein load hi nahi hota (401)

Iska test hai: `test_x_hospital_slug_cannot_switch_tenant`,
`test_token_of_other_hospital_rejected`.

### API errors JSON mein

Tenant middleware pehle HTML login pe redirect kar deta tha. `/api/` paths ke liye ab:

- Tenant resolve na ho → **401** `{"detail": "Tenant resolve nahi hua... X-Hospital-Slug..."}`
- Cross-tenant user → **403** JSON (HTML 403 page nahi)

---

## 3. Auth

```
POST /api/token/            -> {access, refresh, user{...}}
POST /api/token/refresh/    -> {access}
POST /api/token/verify/     -> 200/401
```

Token claims: `user_id`, `username`, `role`, `hospital` (slug), `is_platform_admin`.
Response mein `user` object bhi milta hai (client ko turant `/api/me/` call na karna pade).

**Zaroori:** claims sirf convenience hain. Authorization har request pe server pe dobara
check hoti hai (permissions + tenant + plan) — client token ke andar ka role badal kar
kuch nahi kar sakta (signature verify hoti hai, aur permissions claims se nahi padhti).

Login khud tenant-scoped hai: `TenantModelBackend` sirf current hospital ke users ko
authenticate karta hai, isliye dono hospitals mein same `admin` username hone par bhi
cross-login nahi hota.

Settings: `SIMPLE_JWT_ACCESS_TOKEN_MINUTES` (60), `SIMPLE_JWT_REFRESH_TOKEN_DAYS` (7),
`ROTATE_REFRESH_TOKENS=True`.

---

## 4. Gating

`HasApiAccess` permission class har data ViewSet pe hai:

```python
allowed, reason = check_feature(request, Feature.API_ACCESS)
if not allowed:
    raise ApiFeatureDenied(feature=..., reason=...)   # HTTP 402
```

Jawab:

```json
{"detail": "Yeh API aapke current plan mein nahi hai.",
 "reason": "not_in_plan", "feature": "api_access", "code": "feature_not_in_plan"}
```

- **402** (403 nahi) — yeh permission ka issue nahi, PLAN ka issue hai. HTML side ka
  behaviour mirror karta hai.
- Token endpoint aur docs gate **nahi** hote — warna client ko pata hi na chale ki API hai.
- `POST /api/patients/` pe Phase 2 ka `patient_limit` bhi lagta hai (`limit_reached` → 402).

---

## 5. Endpoints

| Method | Path | Access |
|---|---|---|
| POST | `/api/token/` `/api/token/refresh/` `/api/token/verify/` | public (tenant zaroori) |
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

List endpoints pe: `?search=`, `?ordering=`, `?page=`, `?page_size=` (max 200) aur
`filter_fields` wale exact-match filters (`?status=PAID`, `?doctor=3`,
`?appointment_date=2026-10-01`).

Invalid filter value → **400** (silently ignore nahi karte, warna client ko lagega filter
lag gaya). Doctor role ko appointments queryset mein sirf apne appointments milte hain
(existing HTML RBAC jaisa).

`hospital` field kabhi client se accept nahi hota — `perform_create` request ke tenant se
set karta hai. Test: `test_hospital_field_cannot_be_spoofed`.

---

## 6. Files touched

**New:** `api/` (10 files), `tests/test_api.py` (46 tests), `docs/PHASE_4_API.md`

**Modified:**

| File | Kya |
|---|---|
| `hospital_system/settings.py` | `rest_framework` + `drf_spectacular` + `api` apps, `REST_FRAMEWORK`, `SIMPLE_JWT`, `SPECTACULAR_SETTINGS`, `import datetime as dt` |
| `hospital_system/urls.py` | `path('api/', include('api.urls'))` |
| `tenants/middleware.py` | `/api/` pe `X-Hospital-Slug` header + JSON 401/403 responses |
| `requirements.txt` | DRF, SimpleJWT, drf-spectacular |
| `.env.example` | JWT lifetimes + Swagger/API notes |

---

## 7. Migration commands

**Koi database migration nahi** — `api` app ka koi model nahi hai.

```bash
pip install -r requirements.txt
python manage.py check                    # 2 pre-existing warnings, koi naya nahi
python manage.py spectacular --file schema.yml --validate   # schema sanity
```

---

## 8. Verification

```
pytest tests/test_api.py    -> 46 passed
pytest tests/               -> 166 passed, coverage 84%
manage.py check             -> 2 issues (axes.W006, auth.W004) - dono pre-existing
spectacular --validate      -> Errors: 0, Warnings: 18 (7 unique)
```

`api/` coverage: views 98%, viewsets 97%, serializers 92%, urls 100%, jwt 100%,
permissions 85%, exceptions 88%.

46 tests mein: JWT (issue/refresh/verify/tamper/cross-tenant), tenant isolation (list,
detail 404, foreign FK reject, hospital spoof, header switch), RBAC (role-wise),
plan gating (402 + reasons), resources (CRUD, actions, search, pagination, ordering,
invalid filter), context endpoints, Swagger + OpenAPI schema validation.

### Is phase mein mile 2 asli bugs (dono fix)

1. **FK queryset import-time pe unscoped ban jaata tha.**
   `PrimaryKeyRelatedField(queryset=Patient.objects.all())` class definition ke waqt
   evaluate hota hai, jab tenant enforcement OFF hoti hai — queryset object UNSCOPED
   reh jaata. Matlab API client doosre hospital ka `patient` pk bhej kar appointment bana
   sakta tha. Fix: `TenantModelSerializer.get_fields()` har request pe queryset ko
   `model.objects.for_hospital(request.hospital)` se dobara bind karta hai
   (HTML forms ka `TenantModelForm` bhi yahi karta hai).
   Test: `test_cannot_use_foreign_patient_fk`.

2. **`@extend_schema` galat decorator order mein tha** — `@api_view` ke neeche hone se
   schema attributes wrap ho jaate the aur spectacular 5 endpoints pe "unable to guess
   serializer" warning deta tha. Order ulta kiya (extend_schema sabse upar), warnings gaye.

---

## 9. Jo verify NAHI hua

| Cheez | Status |
|---|---|
| Token blacklist / revocation | **Implement nahi hua** — `token_blacklist` app install nahi. Logout = client token phenk de; `ROTATE_REFRESH_TOKENS` on hai. Zaroorat pade to `rest_framework_simplejwt.token_blacklist` INSTALLED_APPS mein add karo. |
| Rate limiting / throttling | **Implement nahi hua** — API pe DRF throttling nahi lagi. django-axes sirf login attempts pe lagta hai. Production se pehle `DEFAULT_THROTTLE_CLASSES` add karna behtar hai. |
| Postgres pe API tests | **Unverified** — tests SQLite pe chale (CI Postgres pe chalata hai). |
| Real Razorpay-paid plan ke saath API | **Unverified** — tests mein subscription seed kiya gaya hai. |

---

## 10. Manual test steps

```bash
# 1. token
curl -X POST http://acme.localhost:8000/api/token/ \
  -d "username=admin&password=AcmePass!123"

# 2. data
TOKEN=<access>
curl -H "Authorization: Bearer $TOKEN" http://acme.localhost:8000/api/patients/

# 3. isolation: A ka token B pe
curl -H "Authorization: Bearer $TOKEN" -H "Host: beta-city.localhost" \
     http://localhost:8000/api/me/          # -> 401

# 4. gating: free plan wale hospital se
curl -H "Authorization: Bearer $TOKEN" http://free.localhost:8000/api/patients/
#    -> 402 {"reason":"not_in_plan","feature":"api_access"}

# 5. Swagger
open http://acme.localhost:8000/api/docs/
```

---

## 11. Phase 5 ke liye notes

- CI mein `spectacular --validate` step hai — schema tootne pe build fail hoga.
- Sentry `api` app ko automatically cover karta hai (DjangoIntegration).
- API pe abhi throttling nahi — Phase 5 ke Docker/CI setup mein performance test nahi hua,
  production traffic se pehle throttle classes add karna.
