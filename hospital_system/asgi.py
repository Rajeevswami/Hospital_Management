"""
ASGI config for hospital_system project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hospital_system.settings')

application = get_asgi_application()

# Same rationale as wsgi.py - preload the URLconf at startup (see the wsgi.py comment)
from django.urls import get_resolver  # noqa: E402

get_resolver().url_patterns
