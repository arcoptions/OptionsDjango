"""Isolated, broker-safe settings for an ARC staging deployment."""

import os

from .settings import *  # noqa: F403
from .staging_safety import validate_staging_configuration


DEBUG = False
ARC_FORCE_EXECUTION_MODE = "preview"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_STAGING_ALLOWED_HOSTS", "").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_STAGING_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

validate_staging_configuration(
    secret_key=SECRET_KEY,  # noqa: F405
    allowed_hosts=ALLOWED_HOSTS,
    database_engine=DATABASES["default"]["ENGINE"],  # noqa: F405
    database_name=DATABASES["default"]["NAME"],  # noqa: F405
    database_marker=os.environ.get("ARC_STAGING_DATABASE_MARKER", "staging"),
)
