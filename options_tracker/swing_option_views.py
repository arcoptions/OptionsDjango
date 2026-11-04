"""Options-lab view for the strength-dip stock signals."""
from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.shortcuts import render

from .models import PickSource, SwingSignal

STRATEGY_PATH = Path(settings.BASE_DIR) / "options_tracker" / "data" / "swing_option_strategy.json"


def _strategy() -> dict:
    with STRATEGY_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _latest_candidates(eligible_symbols: set[str]) -> tuple[object, list[dict]]:
    live = SwingSignal.objects.filter(source=PickSource.LIVE)
    latest_day = live.order_by("-day").values_list("day", flat=True).first()
    source = PickSource.LIVE
    if latest_day is None:
        backfill = SwingSignal.objects.filter(source=PickSource.BACKFILL)
        latest_day = backfill.order_by("-day").values_list("day", flat=True).first()
        source = PickSource.BACKFILL
    if latest_day is None:
        return None, []

    rows = SwingSignal.objects.filter(source=source, day=latest_day).order_by("rank")
    return latest_day, [
        {
            "row": row,
            "historically_cheap": row.symbol in eligible_symbols,
            "decision": "PAPER TEST" if row.symbol in eligible_symbols else "SKIP",
        }
        for row in rows
    ]


def swing_option_lab(request):
    research = _strategy()
    latest_day, candidates = _latest_candidates(set(research["eligible_symbols"]))
    return render(
        request,
        "options_tracker/swing_option_lab.html",
        {
            "title": "Front-month Options Lab",
            "research": research,
            "rule": research["rule"],
            "all_result": research["selected"]["all"],
            "cheap_result": research["selected"]["cheap"],
            "latest_day": latest_day,
            "candidates": candidates,
        },
    )
