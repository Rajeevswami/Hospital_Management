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

# Multi-tenant (Phase 1): URLconf ko STARTUP pe hi load karo.
# Django URLconf ko lazily (pehli request pe) import karta hai, aur ModelForm ki
# metaclass us waqt FK ka tenant-scoped default manager evaluate karti hai -
# jab koi tenant active nahi hota. Isse pehli request 500 de sakti thi
# (aur error page bhi fail hota tha). Yahan preload karne se yeh problem
# startup pe hi pakdi jaati hai, request pe nahi.
from django.urls import get_resolver  # noqa: E402

get_resolver().url_patterns
