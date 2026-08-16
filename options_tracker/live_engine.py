"""Live execution of the finalised NIFTY trail strategy.

The rules are in `research/STRATEGY.md` and the measured config is
`nifty_trail_strategy.nifty_trail_config()`. Nothing here re-decides strategy;
this module only takes the same signal live and puts it on Dhan.

Three deliberate choices are worth knowing before reading the code.

**The feed is 1-minute chart data, not the 30-second snapshot stream.** The
signal is defined on completed 1-minute bars, and `IndexOptionCandle` -- what the
backtest reads -- is built from `/v2/charts/intraday`. Polling the same endpoint
live keeps the lineage identical instead of approximating minute bars from
snapshot deltas. The snapshot stream is still used, for the two things charts
cannot give: `security_id`, and the bid/ask that the backtest never modelled.

**Entry is a limit order that is not chased.** The backtest enters at
`max(next_bar_open, signal_close) * 1.005` and takes the trade only if the next
bar trades there. Live, that becomes a limit order placed a few seconds into the
next minute and cancelled at the end of it. An unfilled order is not a missed
trade; it is the trade the backtest also declined.

**Dhan's `trailingJump` is not this trail.** It tightens from the first
favourable tick. This strategy holds a fixed -10% stop until the trade is +7% up
and only then follows 7% behind the running high, because 72% of stopped
contracts later trade back above entry. The stop is therefore moved from here,
once a minute, and `trailingJump` is always sent as 0.

The one thing day one exists to measure is the bid-ask: every decision logs the
quoted bid, ask and spread next to the price actually paid. It is the largest
sensitivity in the whole study and the only number in it that has been modelled
rather than observed.
"""
import json
import os
from datetime import datetime, time as clock_time, timedelta
from math import floor
from statistics import median

import requests
from django.utils import timezone

from .capital_pnl import NIFTY_LOT_SIZE
from .index_oi_services import DHAN_INTRADAY_URL, INDEX_CONFIG
from .models import (
    AppSetting,
    DhanOrderEvent,
    Direction,
    IndexOISnapshot,
    SignalStatus,
    TipSignal,
    TradeExecution,
    TradeState,
    TradeStyle,
)
from .nifty_trail_strategy import (
    MAX_CASH_FRACTION,
    RISK_PER_TRADE,
    STARTING_CAPITAL,
    nifty_trail_config,
)
from .services import (
    cancel_super_order_leg,
    fetch_super_order,
    get_dhan_credentials,
    is_dhan_market_open,
    modify_super_order_stop,
    place_market_exit,
    place_super_order,
)
from .strategy_backtest import (
    MARKET_OPEN,
    opening_coverage_floor,
    opening_range_closed,
    spot_setup_timestamps,
)


UNDERLYING = "NIFTY"
SQUARE_OFF = clock_time(15, 20)

STATE_KEY = "nifty_live_state"
STATUS_KEY = "nifty_live_status"
ENABLED_KEY = "nifty_live_enabled"

# Liquidity gates. None of these exist in the backtest -- it never saw a quote.
# They can only stop a fill, never create one, so they cannot flatter the result.
MAX_SPREAD_PERCENT = 4.0
DELTA_MIN, DELTA_MAX = 0.15, 0.70
MAX_QUOTE_AGE_SECONDS = 120

# Dhan requires targetPrice on POST /super/orders, so "no target" has to be an
# unreachable one. The largest single-trade premium gain in 246 sessions was
# roughly +18%; 3x entry is never getting hit, and the 15:20 square-off would
# close the trade long before it did.
TARGET_MULTIPLE = 3.0


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _setting(key, default=""):
    row = AppSetting.objects.filter(key=key).values_list("value", flat=True).first()
    return row if row is not None else default


def engine_enabled():
    """Kill switch. Reading it from the DB means it works without a redeploy."""
    setting = _setting(ENABLED_KEY, "")
    if setting:
        return str(setting).strip().lower() in {"1", "true", "yes", "on"}
    return os.getenv("NIFTY_LIVE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def live_capital():
    return _number(os.getenv("NIFTY_LIVE_CAPITAL"), STARTING_CAPITAL) or STARTING_CAPITAL


def lot_cap():
    """Day-one throttle. Caps size; never turns a skip into a trade."""
    return int(_number(os.getenv("NIFTY_LIVE_FIXED_LOTS"), 0))


def load_state():
    try:
        state = json.loads(_setting(STATE_KEY, "") or "{}")
    except (TypeError, ValueError):
        state = {}
    today = timezone.localdate().isoformat()
    if state.get("date") != today:
        # A fresh day resets the counters but never silently drops a position;
        # if one is still open at rollover it is carried so it can be squared off.
        state = {"date": today, "trades_today": 0, "realized_r": 0.0,
                 "last_exit_at": None, "position": state.get("position")}
    return state


def save_state(state):
    AppSetting.objects.update_or_create(key=STATE_KEY, defaults={"value": json.dumps(state, default=str)})


def _write_status(status):
    AppSetting.objects.update_or_create(key=STATUS_KEY, defaults={"value": json.dumps(status, default=str)})


def _log(event, payload, order_id=""):
    DhanOrderEvent.objects.create(
        order_id=str(order_id), correlation_id="", status=event[:30], payload_json=payload,
    )


# --------------------------------------------------------------------------- #
# Market data
# --------------------------------------------------------------------------- #

def intraday_bars(security_id, segment, instrument, session_date):
    """One session of completed 1-minute bars, oldest first.

    Both ends of Dhan's window are exclusive, which is not what the field names
    suggest and not what this asked for originally. `fromDate 09:15:00` returns
    09:16 onwards -- it drops the opening bar -- so the opening range came back
    fourteen minutes long against the fifteen the strategy is defined on, and
    `spot_setup_timestamps` rejected every session it was ever given. The engine
    could not have produced a signal on any day. Asking from 09:00 restores the
    09:15 bar; Dhan sends nothing before the open, but the trim below makes that
    a guarantee rather than an observation.

    The far end runs to 15:40 because the closing auction session extends F&O
    past 15:30, which is why a full session is 385 bars to 15:39 and not 375 to
    15:29. Nothing the shipped strategy does reaches that far -- it is flat by
    15:20 -- but the stored candles this was validated against include the tail,
    and the point of this window is to match them exactly.
    """
    access_token, client_id = get_dhan_credentials()
    if not access_token or not client_id:
        raise RuntimeError("Dhan credentials are not configured.")
    payload = {
        "securityId": str(security_id),
        "exchangeSegment": segment,
        "instrument": instrument,
        "interval": "1",
        "oi": False,
        "fromDate": f"{session_date.isoformat()} 09:00:00",
        "toDate": f"{session_date.isoformat()} 15:40:00",
    }
    response = requests.post(
        DHAN_INTRADAY_URL,
        json=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": access_token,
            "client-id": client_id,
        },
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"Dhan intraday failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    stamps = data.get("timestamp") or []
    tz = timezone.get_current_timezone()
    bars = []
    for index, epoch in enumerate(stamps):
        pick = lambda name: (data.get(name) or [None] * len(stamps))[index]
        stamp = datetime.fromtimestamp(epoch, tz=tz)
        if stamp.time() < MARKET_OPEN:
            # Asked for from 09:00 only to defeat the exclusive bound. Anything
            # genuinely before the bell is not part of the session the strategy
            # was measured on and must not reach the opening range or a volume
            # average.
            continue
        bars.append({
            "timestamp": stamp,
            "open": _number(pick("open")), "high": _number(pick("high")),
            "low": _number(pick("low")), "close": _number(pick("close")),
            "volume": _number(pick("volume")),
        })
    return bars


def spot_series(session_date):
    """`{minute_start: close}` for NIFTY spot, the shape spot_setup_timestamps wants."""
    bars = intraday_bars(INDEX_CONFIG[UNDERLYING]["security_id"], "IDX_I", "INDEX", session_date)
    return {bar["timestamp"]: bar["close"] for bar in bars}


def latest_snapshot(max_age_seconds=MAX_QUOTE_AGE_SECONDS, now=None):
    """The most recent option-chain snapshot, if the collector is keeping up."""
    now = now or timezone.localtime()
    snapshot = IndexOISnapshot.objects.filter(underlying=UNDERLYING).order_by("-created_at").first()
    if not snapshot:
        return None
    if (now - timezone.localtime(snapshot.created_at)).total_seconds() > max_age_seconds:
        return None
    return snapshot


def quote_row(snapshot, strike, option_type):
    return snapshot.strikes.filter(strike=strike, option_type=option_type).first()


def liquidity_reasons(row):
    """Why this contract is not safe to hit right now. Empty list means it is."""
    reasons = []
    bid, ask = _number(row.top_bid_price), _number(row.top_ask_price)
    if bid <= 0 or ask <= 0:
        reasons.append("no two-sided quote")
    elif ask < bid:
        reasons.append("crossed quote")
    else:
        spread = (ask - bid) / ask * 100
        if spread > MAX_SPREAD_PERCENT:
            reasons.append(f"spread {spread:.1f}% above {MAX_SPREAD_PERCENT:.0f}%")
    if row.top_bid_quantity <= 0 or row.top_ask_quantity <= 0:
        reasons.append("no depth at the top of book")
    if not (DELTA_MIN <= abs(_number(row.delta)) <= DELTA_MAX):
        reasons.append(f"delta {abs(_number(row.delta)):.2f} outside {DELTA_MIN}-{DELTA_MAX}")
    return reasons


def quote_of(row):
    bid, ask = _number(row.top_bid_price), _number(row.top_ask_price)
    return {
        "bid": bid, "ask": ask, "ltp": _number(row.last_price),
        "spread": round(ask - bid, 2) if bid > 0 and ask > 0 else None,
        "spread_percent": round((ask - bid) / ask * 100, 2) if ask > 0 and bid > 0 else None,
        "bid_qty": row.top_bid_quantity, "ask_qty": row.top_ask_quantity,
        "delta": round(_number(row.delta), 4),
    }


# --------------------------------------------------------------------------- #
# Signal
# --------------------------------------------------------------------------- #

def opening_minutes_present(spot_rows, config):
    """How many of the 09:15-09:29 minutes the feed actually delivered."""
    opening_end = (
        datetime.combine(timezone.localdate(), MARKET_OPEN)
        + timedelta(minutes=config.opening_range_minutes)
    ).time()
    return sum(
        1 for timestamp in spot_rows if MARKET_OPEN <= timestamp.time() < opening_end
    )


def detect_signal(now=None, spot_rows=None, snapshot=None):
    """Evaluate the last completed 1-minute bar. Returns (candidate, reasons).

    Every filter is the one `strategy_backtest._candidate` applies, in the same
    order, against the same 1-minute data -- plus the liquidity gates, which the
    backtest could not see.
    """
    now = now or timezone.localtime()
    config = nifty_trail_config()
    reasons = []
    session_date = now.date()

    if spot_rows is None:
        spot_rows = spot_series(session_date)
    if not spot_rows:
        return None, ["no spot bars yet"]

    setups = spot_setup_timestamps(spot_rows, config)
    if not setups:
        # An empty dict reads exactly like a quiet market, and three quite
        # different things produce one. Only the middle case is a fault, and
        # saying so plainly is the difference between a morning that is merely
        # early and one where no trade is possible at all.
        covered = opening_minutes_present(spot_rows, config)
        floor = opening_coverage_floor(config)
        if not opening_range_closed(spot_rows, config):
            return None, [f"opening range still forming; {covered} minutes so far"]
        if covered < floor:
            return None, [
                f"FEED GAP: {covered} of {config.opening_range_minutes} opening-range "
                f"minutes arrived, under the {floor} this needs. No trade is possible "
                f"today until this is repaired."
            ]
        return None, ["opening range held; no breakout yet"]


    # The signal bar is the last minute that has finished. Anything later is
    # still forming and the backtest would not have seen it.
    signal_at = (now - timedelta(minutes=1)).replace(second=0, microsecond=0)
    sides = setups.get(signal_at, set())
    if not sides:
        return None, [f"no opening-range breakout on the {signal_at:%H:%M} bar"]

    if snapshot is None:
        snapshot = latest_snapshot(now=now)
    if snapshot is None or snapshot.atm_strike is None:
        return None, ["no fresh option-chain snapshot"]

    prior_spot = spot_rows.get(signal_at - timedelta(minutes=config.spot_trend_minutes))
    spot = spot_rows.get(signal_at)
    if not prior_spot or not spot:
        return None, ["spot history has a gap"]
    spot_move_percent = abs(spot / prior_spot - 1) * 100

    best = None
    for option_type in sorted(sides):
        side = "CE" if option_type == "CALL" else "PE"
        aligned = spot > prior_spot if option_type == "CALL" else spot < prior_spot
        if not aligned:
            reasons.append(f"{side}: spot not moving the right way over {config.spot_trend_minutes} minutes")
            continue
        if spot_move_percent < config.minimum_spot_move_percent:
            reasons.append(
                f"{side}: 5-minute spot move {spot_move_percent:.2f}% below "
                f"{config.minimum_spot_move_percent}%"
            )
            continue

        row = quote_row(snapshot, snapshot.atm_strike, side)
        if not row or not row.security_id:
            reasons.append(f"{side}: no ATM contract in the snapshot")
            continue

        bars = intraday_bars(
            row.security_id, INDEX_CONFIG[UNDERLYING]["option_segment"], "OPTIDX", session_date,
        )
        by_time = {bar["timestamp"]: bar for bar in bars}
        signal_bar = by_time.get(signal_at)
        if not signal_bar:
            reasons.append(f"{side}: no option bar at {signal_at:%H:%M}")
            continue
        prior_bars = [
            by_time.get(signal_at - timedelta(minutes=offset))
            for offset in range(1, config.lookback + 1)
        ]
        if any(bar is None for bar in prior_bars):
            reasons.append(f"{side}: option bar history has a gap")
            continue

        premium = signal_bar["close"]
        if not (config.premium_min <= premium <= config.premium_max):
            reasons.append(f"{side}: premium Rs {premium:.2f} outside Rs {config.premium_min}+")
            continue
        if premium <= signal_bar["open"]:
            reasons.append(f"{side}: option bar did not close above its open")
            continue
        volumes = [bar["volume"] for bar in prior_bars if bar["volume"] > 0]
        baseline = median(volumes) if volumes else 0
        volume_ratio = signal_bar["volume"] / baseline if baseline else 0
        if volume_ratio < config.volume_ratio:
            reasons.append(f"{side}: volume ratio {volume_ratio:.2f} below {config.volume_ratio}")
            continue

        blocked = liquidity_reasons(row)
        if blocked:
            reasons.extend(f"{side}: {reason}" for reason in blocked)
            continue

        candidate = {
            "signal_at": signal_at,
            "option_type": side,
            "direction": Direction.CE if side == "CE" else Direction.PE,
            "strike": float(snapshot.atm_strike),
            "security_id": row.security_id,
            "signal_close": premium,
            "volume_ratio": round(volume_ratio, 2),
            "spot": spot,
            "spot_move_percent": round(spot_move_percent, 3),
            "expiry_date": snapshot.expiry_date,
            "quote": quote_of(row),
        }
        # Ties on the same minute go to the stronger breakout, as in the backtest.
        if best is None or candidate["volume_ratio"] > best["volume_ratio"]:
            best = candidate

    if best is None:
        return None, reasons or ["no side passed the contract filters"]
    return best, reasons


# --------------------------------------------------------------------------- #
# Sizing
# --------------------------------------------------------------------------- #

def size_position(equity, entry, stop_percent=0.10, lot_size=NIFTY_LOT_SIZE):
    """The STRATEGY.md section 5 formula, unchanged. Zero lots means skip."""
    unit_risk = entry * stop_percent
    if unit_risk <= 0:
        return 0
    risk_lots = floor(equity * RISK_PER_TRADE / (unit_risk * lot_size))
    cash_lots = floor(equity * MAX_CASH_FRACTION / (entry * lot_size))
    lots = max(0, min(risk_lots, cash_lots))
    cap = lot_cap()
    return min(lots, cap) if cap > 0 else lots


# --------------------------------------------------------------------------- #
# Entry
# --------------------------------------------------------------------------- #

def _entry_limit(candidate, snapshot, now):
    """`max(next_bar_open, signal_close) * 1.005`, with the live price standing
    in for the next bar's open -- we are a few seconds into that bar."""
    row = quote_row(snapshot, candidate["strike"], candidate["option_type"])
    live = _number(row.last_price) if row else 0
    return round(max(live, candidate["signal_close"]) * 1.005, 2), row


def open_position(candidate, state, now=None, dry_run=False):
    now = now or timezone.localtime()
    snapshot = latest_snapshot(now=now)
    if snapshot is None:
        return None, ["no fresh option-chain snapshot at entry"]

    entry, row = _entry_limit(candidate, snapshot, now)
    stop = round(entry * (1 - nifty_trail_config().stop_percent), 2)
    lots = size_position(live_capital(), entry)
    if not lots:
        return None, [f"sizing rounds to zero lots at Rs {entry:.2f}"]
    quantity = lots * NIFTY_LOT_SIZE

    signal = TipSignal.objects.create(
        source_type="ENGINE",
        source_name="nifty_trail",
        option_symbol=f"{UNDERLYING} {candidate['strike']:.0f} {candidate['option_type']}",
        security_id=candidate["security_id"],
        exchange_segment=INDEX_CONFIG[UNDERLYING]["option_segment"],
        direction=candidate["direction"],
        trade_style=TradeStyle.INTRADAY,
        entry_price=entry,
        stop_loss=stop,
        # Unreachable by construction; Dhan requires the field.
        target_1=round(entry * TARGET_MULTIPLE, 2),
        expiry_date=candidate["expiry_date"],
        status=SignalStatus.NEW,
        recommendation="NIFTY trail strategy",
        reason_tags=f"volume x{candidate['volume_ratio']}, spot {candidate['spot_move_percent']}%",
        tip_time=now,
    )

    order_payload = {
        "signal_at": candidate["signal_at"].isoformat(),
        "option": signal.option_symbol,
        "entry_limit": entry, "stop": stop, "target": float(signal.target_1),
        "lots": lots, "quantity": quantity,
        "quote_at_signal": candidate["quote"],
        "quote_at_entry": quote_of(row) if row else None,
        "signal_close": candidate["signal_close"],
    }

    if dry_run:
        _log("DRY_RUN_ENTRY", order_payload)
        return None, ["dry run: order not sent"]

    result = place_super_order(signal, quantity)
    _log("ENTRY", {**order_payload, "result": result}, order_id=result.get("order_id", ""))
    if not result.get("ok"):
        signal.status = SignalStatus.REJECTED
        signal.save(update_fields=["status"])
        return None, [f"Dhan rejected the order: {result.get('error')}"]

    execution = TradeExecution.objects.create(
        signal=signal,
        dhan_order_id=result["order_id"],
        correlation_id=result.get("correlation_id", ""),
        quantity=quantity,
        entry_price=entry,
        stop_loss=stop,
        target_1=signal.target_1,
        state=TradeState.OPEN,
        journal_reason=json.dumps(order_payload, default=str),
        opened_at=now,
    )

    state["position"] = {
        "order_id": result["order_id"],
        "signal_id": signal.id,
        "execution_id": execution.id,
        "security_id": candidate["security_id"],
        "option_type": candidate["option_type"],
        "strike": candidate["strike"],
        "entry": entry,
        "initial_stop": stop,
        "stop": stop,
        "quantity": quantity,
        "high_water": entry,
        "filled": False,
        "placed_at": now.isoformat(),
        "signal_at": candidate["signal_at"].isoformat(),
    }
    return state["position"], []


# --------------------------------------------------------------------------- #
# Position management
# --------------------------------------------------------------------------- #

def _trailed_stop(position, high_water):
    """Fixed at -10% until +7%, then 7% behind the high. Upward only."""
    entry = position["entry"]
    config = nifty_trail_config()
    gap = entry * config.stop_percent * config.trail_gap_r
    if high_water - entry < gap:
        return position["stop"]
    return max(position["stop"], round(high_water - gap, 2))


def manage_position(state, now=None, dry_run=False):
    """Advance the open position by one tick: fill check, trail, close detection."""
    position = state.get("position")
    if not position:
        return []
    now = now or timezone.localtime()
    notes = []

    book = None if dry_run else fetch_super_order(position["order_id"])
    if book:
        status = str(book.get("orderStatus", "")).upper()
        filled = _number(book.get("filledQty"))
        if filled > 0 and not position["filled"]:
            position["filled"] = True
            traded = _number(book.get("averageTradedPrice"))
            if traded > 0:
                position["fill_price"] = traded
                position["slippage"] = round(traded - position["entry"], 2)
                _log("FILL", {
                    "order_id": position["order_id"], "limit": position["entry"],
                    "filled_at": traded, "slippage": round(traded - position["entry"], 2),
                }, order_id=position["order_id"])
            notes.append(f"filled {filled:.0f} at Rs {traded:.2f}")
        if status in {"CLOSED", "CANCELLED", "REJECTED"} or (
            position["filled"] and _number(book.get("remainingQuantity")) == 0
            and status in {"TRIGGERED", "CLOSED"}
        ):
            _close_position(state, book, now)
            return notes + [f"position closed ({status})"]

    # Never chase. If the limit has not filled by the end of its minute, the
    # backtest would not have taken this trade either.
    if not position["filled"] and not dry_run:
        placed = datetime.fromisoformat(position["placed_at"])
        if now - placed > timedelta(seconds=75):
            cancel_super_order_leg(position["order_id"], "ENTRY_LEG")
            _log("ENTRY_EXPIRED", {"order_id": position["order_id"], "limit": position["entry"]},
                 order_id=position["order_id"])
            state["position"] = None
            return notes + ["entry limit not filled inside its minute; cancelled, not chased"]
        return notes + ["waiting for the entry limit to fill"]

    snapshot = latest_snapshot(now=now)
    row = quote_row(snapshot, position["strike"], position["option_type"]) if snapshot else None
    if row:
        high_water = max(position["high_water"], _number(row.last_price))
        position["high_water"] = high_water
        new_stop = _trailed_stop(position, high_water)
        if new_stop > position["stop"]:
            if dry_run:
                notes.append(f"dry run: would trail stop Rs {position['stop']:.2f} -> Rs {new_stop:.2f}")
            else:
                result = modify_super_order_stop(position["order_id"], new_stop)
                _log("TRAIL", {
                    "order_id": position["order_id"], "high_water": high_water,
                    "from": position["stop"], "to": new_stop, "result": result,
                }, order_id=position["order_id"])
                if result.get("ok"):
                    position["stop"] = new_stop
                    notes.append(f"stop trailed to Rs {new_stop:.2f}")
                else:
                    notes.append(f"trail rejected: {result.get('error')}")
    return notes


def _close_position(state, book, now, exit_price=None, reason="EXCHANGE"):
    position = state["position"]
    price = exit_price
    if price is None and book:
        price = _number(book.get("averageTradedPrice")) or None
        for leg in book.get("legDetails") or []:
            if str(leg.get("orderStatus", "")).upper() in {"TRADED", "CLOSED", "TRIGGERED"}:
                price = _number(leg.get("price")) or price
    if price is None:
        price = position["stop"]

    entry = position.get("fill_price") or position["entry"]
    unit_risk = entry - position["initial_stop"]
    realized_r = round((price - entry) / unit_risk, 3) if unit_risk > 0 else 0.0

    TradeExecution.objects.filter(id=position["execution_id"]).update(
        state=TradeState.CLOSED, closed_at=now,
    )
    _log("EXIT", {
        "order_id": position["order_id"], "reason": reason, "exit_price": price,
        "entry": entry, "realized_r": realized_r,
        "gross_pnl": round((price - entry) * position["quantity"], 2),
    }, order_id=position["order_id"])

    state["realized_r"] = round(_number(state.get("realized_r")) + realized_r, 3)
    state["trades_today"] = int(_number(state.get("trades_today"))) + 1
    state["last_exit_at"] = now.isoformat()
    state["position"] = None


def square_off(state, now=None, dry_run=False):
    """Flat at 15:20, without exception.

    Order matters and is not negotiable: cancel the resting exit legs first,
    confirm they are gone, and only then sell. Selling while a stop-loss leg is
    still live risks filling twice and leaving the account short a naked option.
    If the cancel cannot be confirmed, do nothing -- the exchange stop is still
    protecting the position, which is a far better failure than an unhedged short.
    """
    position = state.get("position")
    if not position:
        return []
    now = now or timezone.localtime()

    if dry_run:
        return [f"dry run: would square off {position['quantity']} at market"]

    if not position["filled"]:
        cancel_super_order_leg(position["order_id"], "ENTRY_LEG")
        _log("SQUARE_OFF_UNFILLED", {"order_id": position["order_id"]}, order_id=position["order_id"])
        state["position"] = None
        return ["unfilled entry cancelled at the bell"]

    for leg in ("STOP_LOSS_LEG", "TARGET_LEG"):
        cancel_super_order_leg(position["order_id"], leg)

    book = fetch_super_order(position["order_id"])
    still_live = [
        leg.get("legName") for leg in (book or {}).get("legDetails") or []
        if str(leg.get("orderStatus", "")).upper() in {"PENDING", "TRANSIT", "PART_TRADED"}
    ]
    if still_live:
        _log("SQUARE_OFF_BLOCKED", {
            "order_id": position["order_id"], "live_legs": still_live,
            "note": "exit legs still resting; refusing to sell into them",
        }, order_id=position["order_id"])
        return [f"CRITICAL: could not cancel {', '.join(still_live)}; did not sell. "
                f"The exchange stop is still in place -- square off by hand."]

    result = place_market_exit(position["security_id"], position["quantity"])
    _log("SQUARE_OFF", {"order_id": position["order_id"], "result": result},
         order_id=position["order_id"])
    if not result.get("ok"):
        return [f"CRITICAL: square-off order rejected: {result.get('error')}"]

    _close_position(state, None, now, exit_price=position["high_water"], reason="TIME_EXIT")
    return ["squared off at the bell"]


# --------------------------------------------------------------------------- #
# The tick
# --------------------------------------------------------------------------- #

def _entry_allowed(state, now, config):
    if state.get("position"):
        return False, "a position is already open"
    if int(_number(state.get("trades_today"))) >= config.max_trades_per_day:
        return False, f"{config.max_trades_per_day} trades already taken today"
    if _number(state.get("realized_r")) <= -config.daily_loss_limit_r:
        return False, f"daily loss limit of -{config.daily_loss_limit_r}R reached"
    last_exit = state.get("last_exit_at")
    if last_exit:
        elapsed = now - datetime.fromisoformat(last_exit)
        if elapsed < timedelta(minutes=config.reentry_cooldown_minutes):
            return False, f"cooling down for another {config.reentry_cooldown_minutes}-minute window"
    window = config.entry_windows[0]
    if not (window[0] <= now.time() <= window[1]):
        return False, f"outside the {window[0]:%H:%M}-{window[1]:%H:%M} entry window"
    return True, ""


def tick(now=None, dry_run=False):
    """One pass of the engine. Safe to call as often as you like."""
    now = now or timezone.localtime()
    config = nifty_trail_config()
    state = load_state()
    status = {"at": now.isoformat(), "dry_run": dry_run, "notes": []}

    if not engine_enabled():
        status["state"] = "DISABLED"
        _write_status(status)
        return status

    if not is_dhan_market_open(now):
        status["state"] = "CLOSED"
        _write_status(status)
        return status

    try:
        if state.get("position"):
            status["notes"].extend(manage_position(state, now, dry_run))

        if now.time() >= SQUARE_OFF:
            status["notes"].extend(square_off(state, now, dry_run))
            status["state"] = "FLAT_FOR_THE_DAY"
            save_state(state)
            _write_status(status)
            return status

        allowed, blocker = _entry_allowed(state, now, config)
        if allowed:
            candidate, reasons = detect_signal(now)
            status["rejections"] = reasons
            if candidate:
                position, entry_notes = open_position(candidate, state, now, dry_run)
                status["notes"].extend(entry_notes)
                if position:
                    status["notes"].append(
                        f"entered {position['quantity']} {position['option_type']} "
                        f"{position['strike']:.0f} at Rs {position['entry']:.2f}, "
                        f"stop Rs {position['stop']:.2f}"
                    )
        else:
            status["notes"].append(blocker)

        status["state"] = "RUNNING"
    except Exception as error:
        status["state"] = "ERROR"
        status["error"] = str(error)
        _log("TICK_ERROR", {"error": str(error), "at": now.isoformat()})

    status["position"] = state.get("position")
    status["trades_today"] = state.get("trades_today")
    status["realized_r"] = state.get("realized_r")
    save_state(state)
    _write_status(status)
    return status
