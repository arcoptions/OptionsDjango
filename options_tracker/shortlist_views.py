"""The daily call shortlist, and an honest account of how it has done.

WHY THERE ARE TWO PERFORMANCE PANELS AND THEY ARE NEVER ADDED UP.  Backfilled
picks were reconstructed from history: the rule was known, the data was known,
and nothing stopped a bad month from being quietly excluded. Live picks were
written down before the outcome existed. Only the second is evidence about the
future, and the only reason the first appears at all is to give the second
something to be compared against. Every statistic below is computed inside one
source or the other -- there is deliberately no combined number anywhere on the
page, because a combined number would be the backtest wearing the live record's
clothes.

WHY THE NUMBERS ARE COMPUTED, NOT TYPED IN.  The measured expectations in
`shortlist.py` come from the research run; the panels here recount them from the
stored rows. If the two ever disagree the screen is showing a different model
from the one that was tested, and that disagreement should be visible rather
than hidden behind a hard-coded constant.
"""
from __future__ import annotations

import datetime as dt
from statistics import median

from django.db.models import Q
from django.shortcuts import render

from . import shortlist as S
from .models import DailyPick, PickSource
from .stock_views import _rupees

RECENT_LIMIT = 60


def _pct(part, whole):
    return 100.0 * part / whole if whole else 0.0


def _median_pct(values):
    clean = [float(v) * 100 for v in values if v is not None]
    return median(clean) if clean else None


def _record(source):
    """What one population of picks actually did. None when nothing has scored."""
    rows = DailyPick.objects.filter(source=source)
    total = rows.count()
    if not total:
        return None

    scored = rows.filter(sessions_scored=S.HORIZON)
    n = scored.count()
    days = rows.values("day").distinct().count()
    span = rows.order_by("day").values_list("day", flat=True)
    first, last = span.first(), span.last()

    out = {
        "source": source,
        "total": total,
        "sessions": days,
        "symbols": rows.values("symbol").distinct().count(),
        "first_day": first,
        "last_day": last,
        "per_day": total / days if days else 0,
        "scored": n,
        # Picks whose five sessions have not finished. Shown separately rather
        # than counted as misses, which is how a young record gets slandered.
        "pending": total - n - rows.filter(sessions_scored=0,
                                           scored_on__isnull=False).count(),
    }
    if not n:
        out["has_results"] = False
        return out

    up5 = scored.filter(hit_up5=True).count()
    out.update({
        "has_results": True,
        "moved_pct": _pct(scored.filter(Q(hit_up5=True) | Q(hit_dn5=True)).count(), n),
        "up5_pct": _pct(up5, n),
        "dn5_pct": _pct(scored.filter(hit_dn5=True).count(), n),
        "up10_pct": _pct(scored.filter(hit_up10=True).count(), n),
        "med_high": _median_pct(scored.values_list("fwd_high_pct", flat=True)),
        "med_low": _median_pct(scored.values_list("fwd_low_pct", flat=True)),
        "med_close": _median_pct(scored.values_list("fwd_close_pct", flat=True)),
    })
    return out


def _monthly(source):
    """Month by month, because an average hides the months that pay for it."""
    rows = DailyPick.objects.filter(source=source, sessions_scored=S.HORIZON)
    buckets = {}
    for day, up5, up10 in rows.values_list("day", "hit_up5", "hit_up10"):
        key = day.replace(day=1)
        slot = buckets.setdefault(key, [0, 0, 0])
        slot[0] += 1
        slot[1] += 1 if up5 else 0
        slot[2] += 1 if up10 else 0
    return [
        {"month": key, "picks": n, "up5_pct": _pct(up5, n), "up10_pct": _pct(up10, n)}
        for key, (n, up5, up10) in sorted(buckets.items())
    ]


def _decorate(picks):
    """Display fields the template should not have to compute."""
    out = []
    for pick in picks:
        out.append({
            "row": pick,
            "close_text": _rupees(pick.close),
            "turnover_text": _rupees(float(pick.dollar_vol or 0) / 1e7),
            "atr_pct": float(pick.atr_pct or 0) * 100,
            "pos20_pct": float(pick.pos20 or 0) * 100,
            "mom5_pct": float(pick.mom5 or 0) * 100,
            "high_pct": float(pick.fwd_high_pct) * 100 if pick.fwd_high_pct is not None else None,
            "low_pct": float(pick.fwd_low_pct) * 100 if pick.fwd_low_pct is not None else None,
            "close_move": float(pick.fwd_close_pct) * 100 if pick.fwd_close_pct is not None else None,
        })
    return out


def daily_shortlist(request):
    """Today's names, what to expect from them, and the record so far."""
    live = DailyPick.objects.filter(source=PickSource.LIVE)
    backfill = DailyPick.objects.filter(source=PickSource.BACKFILL)

    # Prefer a live list. Falling back to the reconstruction keeps the screen
    # useful before the daemon's first evening, but it is labelled as such --
    # a backfilled row is not a suggestion anyone acted on.
    latest_day = live.order_by("-day").values_list("day", flat=True).first()
    latest_source = PickSource.LIVE
    if latest_day is None:
        latest_day = backfill.order_by("-day").values_list("day", flat=True).first()
        latest_source = PickSource.BACKFILL

    today_rows, age = [], None
    if latest_day is not None:
        today_rows = _decorate(DailyPick.objects.filter(
            day=latest_day, source=latest_source).order_by("rank"))
        age = (dt.date.today() - latest_day).days

    recent = _decorate(DailyPick.objects.filter(
        sessions_scored=S.HORIZON).order_by("-day", "rank")[:RECENT_LIMIT])

    return render(request, "options_tracker/daily_shortlist.html", {
        "today_rows": today_rows,
        "latest_day": latest_day,
        "latest_is_live": latest_source == PickSource.LIVE,
        "age_days": age,
        "stale": age is not None and age > 3,

        # The rule itself, so the screen states what it is doing.
        "atr_cut_pct": S.ATR_CUT * 100,
        "min_close": S.MIN_CLOSE,
        "min_turnover_cr": S.MIN_TURNOVER / 1e7,
        "horizon": S.HORIZON,

        # The research expectations, for the panel that sets them beside the
        # recounted numbers below.
        "exp_move": S.P_MOVE * 100,
        "exp_up": S.P_UP * 100,
        "exp_up10": S.P_UP10 * 100,
        "base_move": S.P_MOVE_BASE * 100,
        "base_up": S.P_UP_BASE * 100,
        "hurdle": S.HURDLE * 100,
        "hurdle_gap": (S.HURDLE - S.P_UP) * 100,

        "live": _record(PickSource.LIVE),
        "backtest": _record(PickSource.BACKFILL),
        "live_monthly": _monthly(PickSource.LIVE),
        "backtest_monthly": _monthly(PickSource.BACKFILL)[-18:],
        "recent": recent,
    })
