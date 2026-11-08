"""Traceable, evidence-only daily briefing generation."""

import hashlib
import json
import os
from urllib.parse import quote

import requests
from django.utils import timezone

from .command_centre import command_centre_context
from .models import AIBriefingSnapshot


PROMPT_VERSION = "ARC-BRIEFING-1"


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8"),
    ).hexdigest()


def briefing_evidence(now=None):
    now = now or timezone.now()
    context = command_centre_context(now)
    sources = []
    market_groups = {}
    source_number = 1
    for category, rows in context["market_context"]["groups"].items():
        market_groups[category] = []
        for row in rows[:20]:
            source_id = f"S{source_number}"
            source_number += 1
            sources.append({
                "id": source_id,
                "provider": row["provider"],
                "source_ref": row["source_ref"],
                "source_at": row["source_at"].isoformat(),
                "received_at": row["received_at"].isoformat(),
                "valid": row["valid"],
                "stale": row["stale"],
            })
            market_groups[category].append({
                "source_id": source_id,
                "key": row["key"],
                "value": row["value"],
                "unit": row["unit"],
                "valid": row["valid"],
                "stale": row["stale"],
                "issues": row["issues"],
            })
    return {
        "generated_at": now.isoformat(),
        "indexes": [
            {
                key: index[key]
                for key in (
                    "symbol",
                    "price",
                    "change_percent",
                    "pcr",
                    "regime",
                    "source",
                    "age_seconds",
                    "stale",
                )
            }
            for index in context["indexes"]
        ],
        "breadth": context["breadth"],
        "regime": context["regime"],
        "market_groups": market_groups,
        "events": [
            {
                "title": event.title,
                "event_type": event.event_type,
                "starts_at": event.starts_at.isoformat(),
                "importance": event.importance,
                "provider": event.provider,
                "source_ref": event.source_ref,
            }
            for event in context["market_context"]["events"]
        ],
        "opportunities": [
            {
                key: row.get(key)
                for key in (
                    "symbol",
                    "type",
                    "action",
                    "score",
                    "source",
                    "source_type",
                    "action_reason",
                    "timestamp",
                )
            }
            for row in context["opportunities"]
        ],
        "sources": sources,
    }


def briefing_prompt(evidence):
    return (
        "Produce ARC's concise pre-market briefing from only the supplied JSON. "
        "Cite factual claims with the matching [S#] source IDs. Explicitly label "
        "stale, invalid, missing, or conflicting evidence. Separate market regime, "
        "events, ranked research items, risks, and a manual-review checklist. "
        "Do not invent prices, confidence, news, orders, fills, or broker actions. "
        "Do not instruct automatic execution. If evidence is insufficient, say so.\n\n"
        f"EVIDENCE_JSON={json.dumps(evidence, sort_keys=True, default=str)}"
    )


def generate_briefing(*, now=None, apply=False):
    now = now or timezone.now()
    evidence = briefing_evidence(now)
    prompt = briefing_prompt(evidence)
    evidence_hash = _hash(evidence)
    prompt_hash = _hash(prompt)
    result = {
        "provider": "AZURE_OPENAI",
        "model": os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip(),
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": prompt_hash,
        "evidence_hash": evidence_hash,
        "evidence": evidence,
        "prompt": prompt,
    }
    if not apply:
        return result

    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/")
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    deployment = result["model"]
    if not endpoint or not api_key or not deployment:
        raise ValueError(
            "Azure OpenAI briefing requires endpoint, API key, and deployment.",
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
                {
                    "role": "system",
                    "content": (
                        "You are ARC's evidence-grounded research summarizer. "
                        "You cannot place or recommend automatic orders."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 900,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    try:
        response_text = str(payload["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("Azure OpenAI returned an invalid briefing response.") from error
    if not response_text:
        raise ValueError("Azure OpenAI returned an empty briefing response.")
    snapshot, _ = AIBriefingSnapshot.objects.get_or_create(
        briefing_date=timezone.localdate(now),
        provider=result["provider"],
        model=deployment,
        prompt_hash=prompt_hash,
        evidence_hash=evidence_hash,
        defaults={
            "prompt_version": PROMPT_VERSION,
            "prompt": prompt,
            "evidence": evidence,
            "response_text": response_text,
            "source_at": now,
        },
    )
    return snapshot
