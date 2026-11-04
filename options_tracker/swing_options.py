"""Strength dips bought as call options: contract choice, marking and the exit rule.

The plan is the one research/STRENGTH_DIP_OPTIONS_REPORT.md found best, and it
is deliberately plain:

  * the call nearest the money, in the first monthly expiry that leaves at
    least 15 sessions after entry;
  * bought at the next open, sold on the close of the 10th session after entry,
    or 3 sessions before expiry if that comes first;
  * NO STOP. The premium is the stop. Every early exit tested -- a premium stop,
    a stock stop, a time stop on laggards, an ATR trail, a premium trail, a profit
    target -- lowered the average trade AND deepened the book's drawdown, because
    this is a dip-buy: its losers usually recover inside ten sessions and its
    winners are the whole of its return. Risk is controlled by SIZE instead:
    keep one trade's premium near 1% of capital.

Only the live book has options. Historical option quotes for a 10-session hold
do not exist in usable form (the feed is nearest-expiry and drops a strike once
spot moves three strikes away), so the page quotes the research expectations
rather than a reconstructed record.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import numpy as np

MIN_DTE = 15
HOLD = 10
EXPIRY_BUFFER = 3
ROUND_TRIP = 0.07
RISK_PCT = 0.01
DEFAULT_CAPITAL = 1_000_000

# Measured in research/breakout2_options_book.py and breakout2_options_extra.txt:
# Black-Scholes on the stock's own path, IV = 1.02 x 20-day realised vol (the
# quoted median), IV moving with the stock at beta -0.76, 7% round trip.
EXP_MEAN = (11.8, 10.1)           # % per trade, 2016-2021 / 2022-2026
EXP_MEDIAN = (-16.3, -19.9)
EXP_WIN = (41.3, 40.7)
EXP_LOST_HALF = (28.4, 31.7)
CONTROL_MEAN = (2.5, 0.8)         # leaders bought on a random day, same plan
BOOK_CAGR_1PCT, BOOK_DD_1PCT = 17.8, -28.6
CONTROL_BOOK_CAGR_1PCT = 2.3

# The exit plans that were tried, as the page shows them (mean % per trade,
# 2016-2021 / 2022-2026, and the book at 1-2% premium per trade).
PLANS = [
    ("Sell at session 10 (the plan)", 11.8, 10.1, "17.8% / -28.6%"),
    ("Hold to session 20", 25.5, 14.7, "17.4% / -30.4%"),
    ("Premium stop at -40%", 15.4, 9.6, "20.2% / -76.5% (2%)"),
    ("Stock stop 1.5 ATR below entry", 13.9, 9.3, "15.8% / -77.8% (2%)"),
    ("Stock stop + sell laggards on day 5", 11.3, 9.2, "7.2% / -80.2% (2%)"),
    ("... + ATR trail after +1 ATR", 11.0, 6.7, "8.3% / -80.8% (2%)"),
    ("... + premium trail 30% after +50%", 8.0, 5.6, "4.3% / -80.8% (2%)"),
    ("Take profit at +100% premium", 6.2, 5.1, "worse on every panel"),
]


def sessions_between(start: dt.date, end: dt.date) -> int:
    """Weekday sessions after `start` up to and including `end` (holidays ignored)."""
    return int(np.busday_count(start + dt.timedelta(days=1), end + dt.timedelta(days=1)))


def choose_contract(symbol: str, spot: float, signal_day: dt.date, contracts: dict):
    """Nearest-the-money call in the first expiry with MIN_DTE sessions after entry."""
    legs = [c for c in contracts.get(symbol, []) if c.get("option_type") == "CE" and c.get("expiry")]
    if not legs or not spot:
        return None
    entry = signal_day + dt.timedelta(days=1)
    while entry.weekday() >= 5:
        entry += dt.timedelta(days=1)
    expiries = sorted({dt.date.fromisoformat(c["expiry"][:10]) for c in legs})
    usable = [e for e in expiries if sessions_between(entry, e) >= MIN_DTE]
    if not usable:
        return None
    expiry = usable[0]
    chain = [c for c in legs if c["expiry"][:10] == expiry.isoformat()]
    best = min(chain, key=lambda c: abs(c["strike"] - spot))
    return {**best, "expiry": expiry}


def _dec(value, places=2):
    if value is None:
        return None
    return Decimal(str(round(float(value), places)))


def _spread_pct(q):
    try:
        bid = q["depth"]["buy"][0]["price"]
        ask = q["depth"]["sell"][0]["price"]
    except (KeyError, IndexError, TypeError):
        return None
    if not bid or not ask:
        return None
    return (ask - bid) / ((ask + bid) / 2) * 100


def lots_for(premium, lot_size, capital=DEFAULT_CAPITAL, risk=RISK_PCT):
    """Whole lots whose premium fits the risk budget, and one lot as % of capital."""
    if not premium or not lot_size:
        return 0, None
    per_lot = float(premium) * lot_size
    return int(capital * risk // per_lot), per_lot / capital * 100


def advance(day: dt.date, quotes, contracts) -> dict:
    """Open, mark and close the live option trades for the session `day`.

    `quotes(security_ids)` returns {security_id: Dhan quote dict} as of now, so
    this must run on the evening of `day` itself -- a quote cannot be replayed.
    """
    from .models import PickSource, SwingOptionTrade, SwingSignal, SwingStatus

    opened = []
    for sig in SwingSignal.objects.filter(source=PickSource.LIVE, day=day, status=SwingStatus.PENDING,
                                          option_trade__isnull=True):
        leg = choose_contract(sig.symbol, float(sig.close or 0), day, contracts)
        if leg is None:
            SwingOptionTrade.objects.create(signal=sig, symbol=sig.symbol, status=SwingStatus.CLOSED,
                                            exit_reason="NO CONTRACT", note="no listed call with 15 sessions left")
            continue
        opened.append(SwingOptionTrade.objects.create(
            signal=sig, symbol=sig.symbol, security_id=str(leg["security_id"]), contract=leg["display"],
            expiry=leg["expiry"], strike=_dec(leg["strike"]), lot_size=int(leg["lot_size"] or 0)))

    live = list(SwingOptionTrade.objects.filter(status__in=[SwingStatus.PENDING, SwingStatus.OPEN])
                .exclude(security_id=""))
    book = quotes([t.security_id for t in live]) if live else {}
    filled, exited = [], []
    today = day.strftime("%d/%m/%Y")
    for t in live:
        q = book.get(t.security_id)
        traded_today = bool(q) and (q.get("last_trade_time") or "")[:10] == today
        if t.status == SwingStatus.PENDING and t.signal.day == day:
            if q and q.get("last_price"):
                t.signal_premium = _dec(q["last_price"])
                t.signal_spread_pct = _dec(_spread_pct(q))
                t.save(update_fields=["signal_premium", "signal_spread_pct"])
            continue
        if t.status == SwingStatus.PENDING:
            if not traded_today or not (q.get("ohlc") or {}).get("open"):
                t.note = f"no trade on {day:%d %b}; will fill at the next open it trades"
                t.save(update_fields=["note"])
                continue
            t.status, t.entry_day = SwingStatus.OPEN, day
            t.entry_premium = _dec(q["ohlc"]["open"])
            t.sessions_held, t.note = 0, ""
            filled.append(t)
        if t.marked_on and t.marked_on >= day:
            t.save()
            continue
        if traded_today:
            t.last_premium = _dec(q["last_price"])
        if t.entry_day != day:
            t.sessions_held += 1
        t.marked_on = day
        left = sessions_between(day, t.expiry) if t.expiry else 99
        reason = "TIME" if t.sessions_held >= HOLD else ("EXPIRY" if left <= EXPIRY_BUFFER else "")
        if reason and t.last_premium:
            t.status, t.exit_day, t.exit_reason = SwingStatus.CLOSED, day, reason
            t.exit_premium = t.last_premium
            gross = float(t.exit_premium) / float(t.entry_premium)
            t.net_return = _dec(gross * (1 - ROUND_TRIP / 2) / (1 + ROUND_TRIP / 2) - 1, 4)
            exited.append(t)
        t.save()
    return {"opened": opened, "filled": filled, "exited": exited}


def format_alert(result: dict, capital=DEFAULT_CAPITAL) -> str:
    from .models import SwingOptionTrade, SwingStatus

    lines = []
    if result["opened"]:
        lines.append("\n<b>As options</b> (sell on the close of session 10, no stop; size ≈1% of capital)")
        for t in SwingOptionTrade.objects.filter(pk__in=[o.pk for o in result["opened"]]):
            lot_cost = float(t.signal_premium) * t.lot_size if t.signal_premium else None
            cost = f" · lot {t.lot_size} ≈ ₹{lot_cost:,.0f}" if lot_cost else ""
            prem = f" ~₹{t.signal_premium}" if t.signal_premium else ""
            lines.append(f"• {t.contract}{prem}{cost}")
    if result["exited"]:
        lines.append("\n<b>Options closed</b>")
        for t in result["exited"]:
            lines.append(f"• {t.contract} {t.exit_reason.lower()} · {float(t.net_return) * 100:+.0f}% net")
    held = SwingOptionTrade.objects.filter(status=SwingStatus.OPEN).order_by("entry_day")
    if held:
        lines.append("\n<b>Options held</b>")
        for t in held:
            move = (float(t.last_premium) / float(t.entry_premium) - 1) * 100 if t.last_premium else 0.0
            lines.append(f"• {t.contract} {move:+.0f}% · session {t.sessions_held}/{HOLD}")
    return "\n".join(lines)
