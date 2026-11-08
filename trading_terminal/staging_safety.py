"""Fail-closed validation for isolated ARC staging settings."""

from django.core.exceptions import ImproperlyConfigured


def validate_staging_configuration(
    *,
    secret_key,
    allowed_hosts,
    database_engine,
    database_name,
    database_marker="staging",
):
    errors = []
    if not secret_key or secret_key in {
        "dev-only-secret-key-change-in-production",
        "replace-me",
    }:
        errors.append("DJANGO_SECRET_KEY must be an explicit staging secret")
    if not allowed_hosts or "*" in allowed_hosts:
        errors.append("staging requires explicit allowed hosts")
    if database_engine.endswith("sqlite3"):
        errors.append("staging requires an isolated PostgreSQL database")
    if database_marker.lower() not in str(database_name).lower():
        errors.append(
            f"staging database name must contain {database_marker!r}",
        )
    if errors:
        raise ImproperlyConfigured("; ".join(errors))
