from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('tenants.urls')),   # /healthz (tenant-exempt)
    path('saas/', include('subscriptions.urls')),  # Phase 2: billing + Razorpay webhook
    path('ai/', include('ml_engine.urls')),        # Phase 3: no-show risk dashboard
    path('api/', include('api.urls')),             # Phase 4: DRF + JWT + Swagger
    path('', include('dashboard.urls')),
    path('accounts/', include('accounts.urls')),
    path('patients/', include('patients.urls')),
    path('doctors/', include('doctors.urls')),
    path('wards/', include('wards.urls')),
    path('appointments/', include('appointments.urls')),
    path('pharmacy/', include('pharmacy.urls')),
    path('billing/', include('billing.urls')),
    # path('pharmacy/', include('pharmacy.urls')),       # Day 6-8
    # path('billing/', include('billing.urls')),         # Day 9-10
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
