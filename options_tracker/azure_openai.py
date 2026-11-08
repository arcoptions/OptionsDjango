"""Narrow Azure OpenAI client used by ARC's evidence-grounded workflows."""

import os
from urllib.parse import quote

import requests


def chat_completion(*, system_prompt, user_prompt, max_tokens):
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/")
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
    if not endpoint or not api_key or not deployment:
        raise ValueError(
            "Azure OpenAI requires endpoint, API key, and deployment.",
        )
    api_version = os.getenv(
        "AZURE_OPENAI_API_VERSION",
        "2024-10-21",
    ).strip()
    url = (
        f"{endpoint}/openai/deployments/{quote(deployment, safe='')}"
        f"/chat/completions?api-version={quote(api_version, safe='')}"
    )
    response = requests.post(
        url,
        headers={"api-key": api_key, "Content-Type": "application/json"},
        json={
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
        },
        timeout=30,
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
