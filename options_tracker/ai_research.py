"""Traceable, evidence-grounded research and risk explanations."""

import hashlib
import json
import re

from django.utils import timezone

from .ai_briefing import briefing_evidence
from .azure_openai import chat_completion
from .models import AIResearchSnapshot


PROMPT_VERSION = "ARC-RESEARCH-1"
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.&_-]{0,40}$")


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8"),
    ).hexdigest()


def generate_research(*, user, question, symbol="", now=None):
    question = question.strip()
    symbol = symbol.strip().upper()
    if not question or len(question) > 500:
        raise ValueError("Question must contain between 1 and 500 characters.")
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError("Symbol contains unsupported characters.")
    now = now or timezone.now()
    evidence = briefing_evidence(now)
    if symbol:
        evidence["indexes"] = [
            row for row in evidence["indexes"] if row["symbol"] == symbol
        ]
        evidence["opportunities"] = [
            row for row in evidence["opportunities"] if row["symbol"] == symbol
        ]
    prompt = (
        "Answer the operator's research or risk question using only EVIDENCE_JSON. "
        "Cite factual claims with available [S#] source IDs. Treat internal index "
        "and opportunity rows without source IDs as uncited ARC signals. State "
        "missing, stale, invalid, or conflicting evidence. Separate facts, "
        "interpretation, risks, and a manual verification checklist. Do not invent "
        "prices, news, confidence, positions, orders, fills, or account details. "
        "Do not give automatic execution instructions or call broker services.\n\n"
        f"QUESTION={question}\nSYMBOL={symbol or 'ALL'}\n"
        f"EVIDENCE_JSON={json.dumps(evidence, sort_keys=True, default=str)}"
    )
    provider_result = chat_completion(
        system_prompt=(
            "You are ARC's evidence-grounded research and risk assistant. "
            "You cannot access accounts or place orders."
        ),
        user_prompt=prompt,
        max_tokens=1_000,
    )
    return AIResearchSnapshot.objects.create(
        user=user,
        question=question,
        symbol=symbol,
        provider=provider_result["provider"],
        model=provider_result["model"],
        prompt_version=PROMPT_VERSION,
        prompt_hash=_hash(prompt),
        evidence_hash=_hash(evidence),
        prompt=prompt,
        evidence=evidence,
        response_text=provider_result["response_text"],
        source_at=now,
    )
