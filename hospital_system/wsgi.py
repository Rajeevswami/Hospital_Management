"""
WSGI config for hospital_system project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hospital_system.settings')

application = get_wsgi_application()

# Multi-tenant (Phase 1): load the URLconf at STARTUP.
# Django imports the URLconf lazily (on the first request), and ModelForm's
# metaclass evaluates the FK's tenant-scoped default manager at that moment -
# when no tenant is active. This could make the first request return a 500
# (and the error page failed too). Preloading here catches the problem
# at startup, not on a request.
from django.urls import get_resolver  # noqa: E402

get_resolver().url_patterns
