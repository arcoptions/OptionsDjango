"""Narrow Azure OpenAI client used by ARC's evidence-grounded workflows."""

from datetime import timedelta
from functools import lru_cache
import json
import os
from urllib.parse import quote

from azure.identity import ManagedIdentityCredential
from django.db import transaction
from django.utils import timezone
import requests

from .models import AppSetting


RATE_STATE_KEY = "azure_openai_request_state"
TRUE_VALUES = {"1", "true", "yes", "on"}


def _managed_identity_enabled() -> bool:
    return os.getenv("AZURE_OPENAI_USE_MANAGED_IDENTITY", "").strip().lower() in TRUE_VALUES


@lru_cache(maxsize=1)
def _managed_identity_credential() -> ManagedIdentityCredential:
    return ManagedIdentityCredential()


def _acquire_request_slot() -> None:
    now = timezone.now()
    limit = max(1, int(os.getenv("AZURE_OPENAI_RATE_LIMIT", "4")))
    with transaction.atomic():
        row, _ = AppSetting.objects.select_for_update().get_or_create(
            key=RATE_STATE_KEY,
            defaults={"value": "{}"},
        )
        try:
            state = json.loads(row.value or "{}")
            window_started = timezone.datetime.fromisoformat(
                state.get("window_started", "")
            )
        except (TypeError, ValueError):
            state = {}
            window_started = now
        if timezone.is_naive(window_started):
            window_started = timezone.make_aware(window_started)
        if now-window_started >= timedelta(minutes=1):
            state = {"window_started": now.isoformat(), "count": 0}
        count = int(state.get("count", 0))
        if count >= limit:
            raise ValueError(
                "Azure OpenAI request budget is exhausted; retry after one minute."
            )
        state.update(
            window_started=state.get("window_started", now.isoformat()),
            count=count+1,
        )
        row.value = json.dumps(state, separators=(",", ":"))
        row.save(update_fields=["value", "updated_at"])


def provider_readiness():
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
    authenticated = bool(
        os.getenv("AZURE_OPENAI_API_KEY", "").strip()
        or _managed_identity_enabled()
    )
    required = {
        "endpoint": endpoint,
        "authentication": authenticated,
        "deployment": deployment,
    }
    missing = [name for name, value in required.items() if not value]
    return {
        "provider": "Azure OpenAI",
        "state": "UNAVAILABLE" if missing else "READY",
        "ready": not missing,
        "detail": (
            "Configuration required: " + ", ".join(missing) + "."
            if missing
            else "Configured for evidence-grounded research."
        ),
    }


def chat_completion(*, system_prompt, user_prompt, max_tokens):
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/")
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
    managed_identity = _managed_identity_enabled()
    if not endpoint or not deployment or not (api_key or managed_identity):
        raise ValueError(
            "Azure OpenAI requires endpoint, authentication, and deployment.",
        )
    _acquire_request_slot()
    api_version = os.getenv(
        "AZURE_OPENAI_API_VERSION",
        "2024-10-21",
    ).strip()
    url = (
        f"{endpoint}/openai/deployments/{quote(deployment, safe='')}"
        f"/chat/completions?api-version={quote(api_version, safe='')}"
    )
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["api-key"] = api_key
    else:
        token = _managed_identity_credential().get_token(
            "https://cognitiveservices.azure.com/.default"
        )
        headers["Authorization"] = f"Bearer {token.token}"
    request_body = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if os.getenv("AZURE_OPENAI_USE_MAX_COMPLETION_TOKENS", "").strip().lower() in TRUE_VALUES:
        request_body["max_completion_tokens"] = max_tokens
    else:
        request_body.update(temperature=0.1, max_tokens=max_tokens)
    response = requests.post(
        url,
        headers=headers,
        json=request_body,
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    try:
        response_text = str(payload["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("Azure OpenAI returned an invalid response.") from error
    if not response_text:
        raise ValueError("Azure OpenAI returned an empty response.")
    return {
        "provider": "AZURE_OPENAI",
        "model": deployment,
        "response_text": response_text,
    }
