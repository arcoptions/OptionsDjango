"""The strategy report, rendered from the files the research scripts wrote.

Everything numeric on this page is read back out of research/*.csv rather than
typed into the template, so the page cannot drift from what was actually
measured. If a script is re-run with more data, the page moves with it.
"""
import csv
import datetime as dt
import os

from django.conf import settings
from django.shortcuts import render

from .stock_views import _rupees

RESEARCH = os.path.join(settings.BASE_DIR, "research")


def _read(name):
    path = os.path.join(RESEARCH, name)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _strategy_rows():
    """Every signal x exit combination, worst last, with the sample split."""
    rows = []
    for raw in _read("option_strategy.csv"):
        net = _number(raw.get("net_mean"))
        if net is None:
            continue
        rows.append({
            "exit": raw["exit"],
            "signal": raw["signal"],
            "trades": int(_number(raw.get("trades"), 0)),
            "net": net,
            "edge_pct": (net - 1) * 100,
            "win_rate": _number(raw.get("win_rate"), 0) * 100,
            "median": _number(raw.get("net_median")),
            "first_half": _number(raw.get("first_half")),
            "second_half": _number(raw.get("second_half")),
            "profitable": net > 1.0,
        })
    return sorted(rows, key=lambda row: -row["net"])


def _big_runs(limit=25):
    rows = []
    for raw in _read("option_big_runs.csv"):
        multiple = _number(raw.get("multiple"))
        if multiple is None:
            continue
        start = raw.get("start", "")[:10]
        try:
            start = dt.date.fromisoformat(start)
        except ValueError:
            start = None
        rows.append({
            "symbol": raw["symbol"], "type": raw["type"],
            "strike": _number(raw.get("strike")),
            "from_price": _number(raw.get("from")),
            "to_price": _number(raw.get("to")),
            "multiple": multiple,
            "dte": _number(raw.get("dte_at_low")),
            "moneyness": _number(raw.get("moneyness_at_low")),
            "start": start,
            "bars": _number(raw.get("bars")),
        })
    rows.sort(key=lambda row: -row["multiple"])
    return rows[:limit], len(rows)


def _chartink():
    """Excess return over the same-day universe, per scan and horizon."""
    horizons = ["15m", "30m", "1h", "2h", "EOD", "2d", "5d"]
    out = []
    for name, label in [("ARC15MIN", "15-minute scan"), ("NARC1HR", "1-hour scan")]:
        rows = _read(f"chartink_{name}.csv")
        if not rows:
            continue
        days = {row["when"][:10] for row in rows}
        entry = {"scan": name, "label": label, "triggers": len(rows), "days": len(days),
                 "symbols": len({row["symbol"] for row in rows}), "horizons": []}
        for horizon in horizons:
            excess = [_number(row.get(f"exc_{horizon}")) for row in rows]
            raw_returns = [_number(row.get(f"ret_{horizon}")) for row in rows]
            excess = [value for value in excess if value is not None]
            raw_returns = [value for value in raw_returns if value is not None]
            if not excess:
                continue
            mean = sum(excess) / len(excess)
            entry["horizons"].append({
                "horizon": horizon,
                "raw": sum(raw_returns) / len(raw_returns) if raw_returns else 0,
                "excess": mean,
                "beat": sum(1 for value in excess if value > 0) / len(excess) * 100,
                "n": len(excess),
            })
        signal_move = [_number(row.get("signal_move")) for row in rows]
        signal_move = [value for value in signal_move if value is not None]
        entry["signal_move"] = sum(signal_move) / len(signal_move) if signal_move else None
        out.append(entry)
    return out


def _chartink_ce():
    """The two-stage test: Chartink trigger -> stock -> CE, and the control.

    Written by `research/chartink_options.py` and `research/chartink_control.py`.
    Everything here is pooled across both scans ("BOTH") except stage 1, which is
    worth seeing per scan.
    """
    def rows(name):
        return _read(f"chartink_ce_{name}.csv")

    stage1 = [
        {"scan": row["scan"], "horizon": row["horizon"],
         # Stored as fractions; the rest of this page speaks percent.
         "raw": (_number(row["raw"]) or 0) * 100,
         "excess": (_number(row["excess"]) or 0) * 100,
         "t": _number(row["t"]), "n": int(_number(row["n"], 0)),
         "days": int(_number(row["days"], 0))}
        for row in rows("stage1")
    ]
    stage2 = [
        {"scan": row["scan"], "exit": row["exit"], "mean": _number(row["mean"]),
         "median": _number(row["median"]), "win": _number(row["win"]),
         "t": _number(row["t"]), "n": int(_number(row["n"], 0))}
        for row in rows("stage2")
    ]
    money = [
        {"exit": row["exit"], "trades": int(_number(row["trades"], 0)),
         "deployed": _rupees(_number(row["deployed"])),
         "pnl": _rupees(_number(row["pnl"])),
         "pct": _number(row["pct"]), "mean": _rupees(_number(row["mean"])),
         "median": _rupees(_number(row["median"])),
         "worst": _rupees(_number(row["worst"]))}
        for row in rows("money")
    ]
    hurdle = [
        {"horizon": row["horizon"], "need": _number(row["need_pct"]),
         "delivered": _number(row["median_delivered_pct"]),
         "cleared": _number(row["cleared_pct"])}
        for row in rows("hurdle")
    ]
    control = [
        {"exit": row["exit"], "trigger": _number(row["trigger"]),
         "control": _number(row["control"]), "difference": _number(row["difference"]),
         "t": _number(row["t"]), "n": int(_number(row["n"], 0)),
         "control_n": int(_number(row["control_n"], 0))}
        for row in rows("control")
    ]

    if not stage2:
        return None

    pooled1 = [row for row in stage1 if row["scan"] == "BOTH"]
    trail = next((row for row in money if row["exit"] == "30% trail"), None)
    two_day = next(
        (row for row in control if row["exit"] == "2d"),
        None,
    )
    # The strongest t-statistic anywhere in stage 1, in either direction --
    # the honest headline for "did the trigger move the stock at all".
    best_t = max((row["t"] for row in stage1 if row["t"] is not None), default=None)

    return {
        "stage1": stage1,
        "stage1_pooled": pooled1,
        "stage1_scans": [row for row in stage1 if row["scan"] != "BOTH"],
        "stage2": [row for row in stage2 if row["scan"] == "BOTH"],
        "money": money,
        "hurdle": hurdle,
        "control": control,
        "trail": trail,
        "base_rate": two_day["control"] if two_day else None,
        "best_t": best_t,
        "triggers": max((row["n"] for row in pooled1), default=0),
        "days": max((row["days"] for row in pooled1), default=0),
    }


def _correlation(xs, ys):
    """Pearson, written out rather than imported -- this page has no numpy."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    n = len(pairs)
    mx = sum(x for x, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    cov = sum((x - mx) * (y - my) for x, y in pairs)
    vx = sum((x - mx) ** 2 for x, _ in pairs)
    vy = sum((y - my) ** 2 for _, y in pairs)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx * vy) ** 0.5


def _spreads():
    """Short ATM/ATM+1 call spreads, quoted-only against exits repaired.

    Written by `research/option_spreads.py` (pooled),
    `research/spread_near_expiry.py` (0-7 DTE) and `research/spread_tables.py`
    (the two derived tables). Every table here is reported twice, because the
    difference between the two columns IS the finding.
    """
    summary = _read("spread_summary.csv")
    if not summary:
        return None

    def num(row, key):
        return _number(row.get(key))

    # spread_summary.csv carries both passes; pair them up by exit.
    quoted = {row["exit"]: row for row in summary if row.get("prefix") != "i_"}
    paired = []
    for row in summary:
        if row.get("prefix") != "i_":
            continue
        was = quoted.get(row["exit"], {})
        n_imputed = int(_number(row.get("n"), 0))
        n_quoted = int(_number(was.get("n"), 0))
        paired.append({
            "exit": row["exit"],
            "quoted": num(was, "roi"), "imputed": num(row, "roi"),
            "win": num(row, "win"), "p10": num(row, "p10"), "t": num(row, "t"),
            "n": n_imputed, "quoted_n": n_quoted,
            "coverage": 100 * n_quoted / n_imputed if n_imputed else None,
        })

    dte = [{"bucket": row["bucket"], "quoted": num(row, "quoted"),
            "imputed": num(row, "imputed"), "coverage": num(row, "quoted_pct"),
            "n": int(_number(row.get("n"), 0))}
           for row in _read("spread_dte.csv")]
    exits = [{"exit": row["exit"], "quoted": num(row, "quoted"),
              "imputed": num(row, "imputed"), "win": num(row, "win"),
              "p5": num(row, "p5"), "coverage": num(row, "quoted_pct"),
              "n": int(_number(row.get("n"), 0))}
             for row in _read("spread_exits.csv")]
    cycles = [{"expiry": row["expiry"], "imputed": num(row, "roi"),
               "quoted": num(row, "quoted"), "win": num(row, "win"),
               "n": int(_number(row.get("n"), 0))}
              for row in _read("spread_cycles.csv")]
    slippage = [{"cost": num(row, "cost"), "roi": num(row, "roi"),
                 "win": num(row, "win"), "rupees": num(row, "rs"),
                 "t": num(row, "t"), "openable": num(row, "openable")}
                for row in _read("spread_slippage.csv")]
    months = [{"month": row["month"], "roi": num(row, "roi"),
               "move": num(row, "move")} for row in _read("spread_months.csv")]
    money = [{"exit": row["exit"], "trades": int(_number(row.get("trades"), 0)),
              "risk": _rupees(num(row, "risk")), "pnl": _rupees(num(row, "pnl")),
              "pct": num(row, "pct")} for row in _read("spread_money.csv")]

    five = next((row for row in paired if row["exit"] == "5d"), None)
    headline = next((row for row in exits if row["exit"] == "expiry day 14:00"), None)
    return {
        "summary": paired, "dte": dte, "exits": exits, "cycles": cycles,
        "slippage": slippage, "money": money,
        "five_day": five, "headline": headline,
        "months": len(months),
        "months_up": sum(1 for row in months if (row["roi"] or 0) > 0),
        "months_down": sum(1 for row in months if (row["roi"] or 0) <= 0),
        "correlation": _correlation([row["roi"] for row in months],
                                    [row["move"] for row in months]),
        "cycles_up": sum(1 for row in cycles if (row["imputed"] or 0) > 0),
        "cycles_quoted_up": sum(1 for row in cycles if (row["quoted"] or 0) > 0),
    }


def research_report(request):
    strategies = _strategy_rows()
    runs, run_count = _big_runs()
    profitable = [row for row in strategies if row["profitable"]]
    robust = [row for row in profitable
              if (row["first_half"] or 0) > 1 and (row["second_half"] or 0) > 1]

    best_exit = {}
    for row in strategies:
        current = best_exit.get(row["exit"])
        if current is None or row["net"] > current["net"]:
            best_exit[row["exit"]] = row

    context = {
        "title": "Strategy Report",
        "strategies": strategies,
        "tested": len(strategies),
        "profitable": profitable,
        "robust": robust,
        "exits": sorted(best_exit.values(), key=lambda row: -row["net"]),
        "runs": runs,
        "run_count": run_count,
        "chartink": _chartink(),
        "chartink_ce": _chartink_ce(),
        "spreads": _spreads(),
        "generated": dt.datetime.now(),
    }
    return render(request, "options_tracker/research_report.html", context)
