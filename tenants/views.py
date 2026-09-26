"""Health check - load balancer / uptime monitor ke liye. Tenant-exempt path hai."""
import time

from django.db import connection
from django.http import JsonResponse

_START = time.time()


def healthz(request):
    """DB reachable hai ya nahi - 200 ya 503. Koi tenant/auth zaroori nahi."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        db_ok = True
    except Exception:
        db_ok = False
    return JsonResponse(
        {
            "status": "ok" if db_ok else "degraded",
            "database": "up" if db_ok else "down",
            "uptime_seconds": round(time.time() - _START, 1),
        },
        status=200 if db_ok else 503,
    )
