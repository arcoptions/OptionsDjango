"""Versioned JSON responses for ARC browser polling contracts."""

from django.http import JsonResponse


API_VERSION = "2026-11-09"


def versioned_json(payload, *, status=200):
    response = JsonResponse({**payload, "api_version": API_VERSION}, status=status)
    response["X-ARC-API-Version"] = API_VERSION
    return response
