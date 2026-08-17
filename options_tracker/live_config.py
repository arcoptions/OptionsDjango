"""Risk settings that can be changed from the dashboard while the engine runs.

`nifty_trail_config()` is the measured strategy and stays exactly where it is:
the backtest, `report_trail_strategy` and every research script keep importing it
and keep producing the same numbers. Nothing in this module is visible to them.
What this adds is a layer *over* it that only the live engine reads, so the
account can be re-tuned without a redeploy and without disturbing the lineage
the edge was measured on.

Three rules make that safe.

**Hard bounds are arithmetic, not opinion.** A stop of 0% makes the unit risk
zero and the position size infinite; a risk fraction above 1 sizes past the
account. Those are clamped because the code cannot execute them, not because
they are unwise. Everything between the bounds is allowed.

**The tested range is advice, and it is measured.** `build_risk_surface`
backtests each value over the same 246 sessions, so when a setting sits outside
what research actually explored the panel can say so and show what the nearest
measured points did. The user asked to be able to leave the envelope; this makes
leaving it an informed act rather than a blind one.

**An open position keeps the settings it was opened with.** The stop and trail
are captured into the position at entry, so moving a slider at 11:00 cannot
retroactively widen the stop on a trade that is already running. Changes take
effect on the next entry.
"""
import json
import os
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from django.utils import timezone

from .models import AppSetting, DhanOrderEvent
from .nifty_trail_strategy import (
    MAX_CASH_FRACTION,
    RISK_PER_TRADE,
    STARTING_CAPITAL,
    nifty_trail_config,
)

RISK_KEY = "nifty_live_risk"
SURFACE = Path(__file__).resolve().parent / "data" / "risk_surface.json"

# `kind` drives how the field renders and how the dashboard reacts to it.
# "sizing" knobs do not change which trades happen, only how large they are, so
# their effect is recomputed exactly from the cached trade list. "strategy"
# knobs change the trades themselves and can only be answered by the sweep.
TUNABLES = (
    {
        "key": "capital", "label": "Trading capital", "unit": "Rs",
        "kind": "sizing", "step": 5000, "decimals": 0, "env": "NIFTY_LIVE_CAPITAL",
        "shipped": STARTING_CAPITAL, "hard": (10_000, 1_00_00_000),
        "help": "The equity every position is sized against. Below Rs 1,00,000 the "
                "2% risk budget starts rounding to zero lots and signals are simply "
                "skipped: replayed at Rs 50,000 the account takes 23 of the 51 "
                "trades and makes Rs 7,576, and at Rs 25,000 it takes none at all. "
                "This is a cliff, not a slope.",
    },
    {
        "key": "risk_per_trade", "label": "Risk per trade", "unit": "%",
        "kind": "sizing", "step": 0.25, "decimals": 2, "percent": True,
        "shipped": RISK_PER_TRADE, "hard": (0.001, 0.10),
        "help": "How much of the account a single stop-out may cost. Upward it "
                "scales roughly as you would expect -- 3% makes Rs 77,957 for a "
                "10.1% drawdown against 2% making Rs 38,626 for 5.1%. Downward it "
                "does not: at 1% the lot arithmetic rounds 30 of the 51 signals "
                "away and the result collapses to Rs 7,341, which is a fifth, not "
                "a half.",
    },
    {
        "key": "max_cash_fraction", "label": "Max cash deployed", "unit": "%",
        "kind": "sizing", "step": 5, "decimals": 2, "percent": True,
        "shipped": MAX_CASH_FRACTION, "hard": (0.05, 1.0),
        "help": "A ceiling on how much of the account can sit in one contract, "
                "independent of the risk budget. At Rs 1,00,000 it never actually "
                "binds -- the largest position the strategy ever took was Rs 27,407 "
                "-- so raising it changes nothing. Lowering it below about 20% "
                "removes trades: at 10% the account takes 21 of 51 and makes "
                "Rs 7,341.",
    },
    {
        "key": "fixed_lots", "label": "Lot cap", "unit": "lots",
        "kind": "sizing", "step": 1, "decimals": 0, "env": "NIFTY_LIVE_FIXED_LOTS",
        "shipped": 0, "hard": (0, 20),
        "help": "A hard ceiling on position size, applied after sizing. 0 means no "
                "cap. It can only make a position smaller -- it never turns a "
                "skipped signal into a trade. Held to 1 lot the strategy would have "
                "made Rs 26,648 instead of Rs 38,626: about a third of the edge, "
                "which is the price of measuring a real fill before trusting size.",
    },
    {
        "key": "stop_percent", "label": "Stop loss", "unit": "%",
        "kind": "strategy", "step": 1, "decimals": 2, "percent": True,
        "shipped": None, "hard": (0.02, 0.50),
        "help": "How far below entry the initial stop sits. 10% is a peak, not a "
                "point on a slope -- 8% makes Rs 20,517 and 12.5% makes Rs 26,153. "
                "Wider is emphatically not safer: because a wider stop raises the "
                "per-lot risk, it prices the account out of its own signals. At 15% "
                "only 35 of the 51 trades can still be sized, and at 30% only one.",
    },
    {
        "key": "trail_gap_r", "label": "Trail gap", "unit": "R",
        "kind": "strategy", "step": 0.1, "decimals": 2,
        "shipped": None, "hard": (0.1, 3.0),
        "help": "The stop stays fixed until the trade is this far in profit, then "
                "follows this far behind the running high. The largest lever on the "
                "page -- the exit, not the entry, is where this edge lives -- and "
                "an asymmetric one. Loosening to 0.8R costs little (Rs 36,344), "
                "tightening to 0.6R costs most of it (Rs 16,009), because a tight "
                "trail exits during the same wobble the 10% stop exists to absorb.",
    },
    {
        "key": "premium_min", "label": "Minimum premium", "unit": "Rs",
        "kind": "strategy", "step": 5, "decimals": 0,
        "shipped": None, "hard": (10, 1000),
        "help": "The cheapest contract the engine will buy. Read the sweep here "
                "with care: it says Rs 50 makes Rs 54,873 against Rs 38,626, and "
                "that number is a trap. The floor is a costs finding, not a quality "
                "one -- below Rs 100 the same signal captures under 2 points a "
                "trade, so a Rs 2 round-trip spread the backtest does not model "
                "eats the entire difference. It is also what keeps most expiry "
                "days out.",
    },
    {
        "key": "volume_ratio", "label": "Volume ratio", "unit": "x",
        "kind": "strategy", "step": 0.25, "decimals": 2,
        "shipped": None, "hard": (0.5, 5.0),
        "help": "How much heavier the signal bar's volume must be than the median "
                "of the previous five. The least consequential control here: every "
                "value from 1.0 to 2.5 lands between Rs 25,418 and Rs 39,510, so "
                "the filter mostly trades quantity for selectivity without moving "
                "the result. Not worth tuning.",
    },
    {
        "key": "minimum_spot_move_percent", "label": "Minimum spot move", "unit": "%",
        "kind": "strategy", "step": 0.05, "decimals": 2,
        "shipped": None, "hard": (0.0, 1.0),
        "help": "How far NIFTY itself must have travelled over five minutes for a "
                "breakout to count. This is the filter that separates a real move "
                "from a drift across the opening range, and it is doing almost all "
                "the work: at 0.10% the strategy takes 92 trades to make Rs 2,624, "
                "and at 0.05% it takes 149 to lose money outright.",
    },
    {
        "key": "max_trades_per_day", "label": "Max trades per day", "unit": "",
        "kind": "strategy", "step": 1, "decimals": 0,
        "shipped": None, "hard": (1, 10),
        "help": "How many entries a single session may take. 3 is where this "
                "saturates -- 4 and 5 produce the identical 51 trades, because the "
                "other filters never let a fourth signal through. Lowering it does "
                "cost: 1 trade a day makes Rs 27,916.",
    },
    {
        "key": "daily_loss_limit_r", "label": "Daily loss limit", "unit": "R",
        "kind": "strategy", "step": 0.5, "decimals": 2,
        "shipped": None, "hard": (0.5, 10.0),
        "help": "The engine stops entering for the day once losses reach this many "
                "times the per-trade risk. A circuit breaker on a bad session, not "
                "a profit control. The sweep shows 3R making Rs 50,982 against 2R's "
                "Rs 38,626 -- but that is one single extra trade that happened to "
                "win big, over 246 sessions. It is noise, and not a reason to widen "
                "the breaker.",
    },
)

BY_KEY = {spec["key"]: spec for spec in TUNABLES}


def _shipped_value(key):
    """The validated setting, read from the real config so it cannot drift."""
    spec = BY_KEY[key]
    if spec["shipped"] is not None:
        return spec["shipped"]
    return getattr(nifty_trail_config(), key)


def defaults():
    """The validated setting for each knob -- what research actually measured.

    This is the comparison the panel draws against, so it deliberately ignores
    the environment. A lot cap of 1 set for day one is a *deviation* from the
    measured strategy and should be shown as one, not quietly treated as normal.
    """
    return {spec["key"]: _shipped_value(spec["key"]) for spec in TUNABLES}


def _environment_seed():
    """Deployment defaults, used only until the dashboard sets a value.

    `NIFTY_LIVE_FIXED_LOTS=1` and `NIFTY_LIVE_CAPITAL` are already app settings on
    the host. Honouring them here means the panel opens showing the size the
    engine is genuinely running, instead of a validated default it is not using.
    """
    seed = {}
    for spec in TUNABLES:
        name = spec.get("env")
        if not name:
            continue
        raw = os.getenv(name, "").strip()
        if not raw:
            continue
        try:
            seed[spec["key"]] = clamp(spec["key"], float(raw))
        except (TypeError, ValueError):
            continue
    return seed


@lru_cache(maxsize=1)
def risk_surface():
    """The measured sweep, or an empty surface if it has not been built."""
    if not SURFACE.exists():
        return {}
    try:
        return json.loads(SURFACE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def tested_range(key):
    """`(low, high)` of what the sweep actually measured, or None."""
    surface = risk_surface().get("surface", {}).get(key)
    if not surface or not surface.get("points"):
        return None
    values = [point["value"] for point in surface["points"]]
    return min(values), max(values)


def stored_overrides():
    raw = AppSetting.objects.filter(key=RISK_KEY).values_list("value", flat=True).first()
    try:
        stored = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return {key: value for key, value in stored.items() if key in BY_KEY}


def live_settings():
    """Effective values: validated config, then environment seed, then overrides."""
    values = defaults()
    values.update(_environment_seed())
    values.update(stored_overrides())
    return values


def clamp(key, value):
    """Hold a value inside the bounds the arithmetic requires. Not the tested range."""
    low, high = BY_KEY[key]["hard"]
    number = float(value)
    if BY_KEY[key]["decimals"] == 0:
        number = round(number)
    return max(low, min(high, number))


def warnings_for(values):
    """Which settings sit outside what research measured, and by how much."""
    notes = []
    shipped = defaults()
    for key, value in values.items():
        spec = BY_KEY.get(key)
        if not spec:
            continue
        bounds = tested_range(key)
        if bounds and not (bounds[0] <= float(value) <= bounds[1]):
            notes.append(
                f"{spec['label']} {_format(key, value)} is outside the "
                f"{_format(key, bounds[0])}-{_format(key, bounds[1])} range the "
                f"backtest covered. Nothing has measured this setting."
            )
        elif float(value) != float(shipped[key]):
            notes.append(
                f"{spec['label']} is {_format(key, value)} against the validated "
                f"{_format(key, shipped[key])}."
            )
    return notes


def _format(key, value):
    spec = BY_KEY[key]
    number = float(value) * 100 if spec.get("percent") else float(value)
    text = f"{number:,.{spec['decimals']}f}".rstrip("0").rstrip(".") if spec["decimals"] else f"{number:,.0f}"
    unit = spec["unit"]
    if unit == "Rs":
        return f"Rs {text}"
    return f"{text}{unit}" if unit else text


def save_live_settings(values, actor="dashboard"):
    """Persist a settings change, clamped and logged. Returns (settings, warnings)."""
    current = live_settings()
    updated = dict(current)
    changed = {}
    for key, raw in values.items():
        if key not in BY_KEY or raw in (None, ""):
            continue
        try:
            value = clamp(key, raw)
        except (TypeError, ValueError):
            continue
        if float(value) != float(current[key]):
            changed[key] = {"from": current[key], "to": value}
        updated[key] = value

    AppSetting.objects.update_or_create(
        key=RISK_KEY, defaults={"value": json.dumps(updated)},
    )
    live_strategy_config.cache_clear()
    if changed:
        # Permanent record. A live account's risk settings changing is exactly
        # the kind of thing you want a timestamp for when reading back a bad day.
        DhanOrderEvent.objects.create(
            order_id="", correlation_id="", status="RISK_CHANGE",
            payload_json={"at": timezone.localtime().isoformat(), "by": actor,
                          "changed": changed, "settings": updated},
        )
    return updated, warnings_for(updated)


def reset_live_settings(actor="dashboard"):
    """Back to the validated config in one action."""
    return save_live_settings(defaults(), actor=actor)


@lru_cache(maxsize=1)
def live_strategy_config():
    """`nifty_trail_config()` with the dashboard's strategy overrides applied.

    Cached because the engine asks for it several times a tick; the cache is
    cleared whenever settings are saved. The backtest never calls this.
    """
    values = live_settings()
    overrides = {
        key: values[key] for key in BY_KEY
        if BY_KEY[key]["kind"] == "strategy" and key in values
    }
    overrides["max_trades_per_day"] = int(overrides["max_trades_per_day"])
    return replace(nifty_trail_config(), **overrides)


def live_sizing():
    """The four numbers `size_position` needs, as currently tuned."""
    values = live_settings()
    return {
        "capital": float(values["capital"]),
        "risk_per_trade": float(values["risk_per_trade"]),
        "max_cash_fraction": float(values["max_cash_fraction"]),
        "fixed_lots": int(values["fixed_lots"]),
    }


def panel_rows():
    """Everything the risk tab needs to render one control per setting."""
    values = live_settings()
    shipped = defaults()
    rows = []
    for spec in TUNABLES:
        key = spec["key"]
        bounds = tested_range(key)
        surface = risk_surface().get("surface", {}).get(key, {})
        rows.append({
            **spec,
            "value": values[key],
            "display": float(values[key]) * 100 if spec.get("percent") else float(values[key]),
            "shipped_value": shipped[key],
            "shipped_display": float(shipped[key]) * 100 if spec.get("percent") else float(shipped[key]),
            "is_default": float(values[key]) == float(shipped[key]),
            "tested": bounds,
            "outside_tested": bool(bounds) and not (bounds[0] <= float(values[key]) <= bounds[1]),
            "hard_display": (
                spec["hard"][0] * 100 if spec.get("percent") else spec["hard"][0],
                spec["hard"][1] * 100 if spec.get("percent") else spec["hard"][1],
            ),
            "points": surface.get("points", []),
        })
    return rows
