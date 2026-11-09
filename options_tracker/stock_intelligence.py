"""Provider-neutral stock intelligence and explainable score previews."""

from statistics import mean, pstdev

from django.utils import timezone

from .models import (
    StockCorporateAction,
    StockEquityCandle,
    StockFinancialResult,
    StockFundamentalSnapshot,
    StockNewsItem,
    StockOptionCandle,
    StockScoreSnapshot,
    UnifiedOpportunity,
    OpportunityStatus,
)


SCORE_VERSION = "ARC-STOCK-1"
TIMEFRAMES = (
    (15, "15 minute"),
    (60, "1 hour"),
    (1440, "Daily"),
)


def _clamp(value, low=0.0, high=100.0):
    return max(low, min(high, float(value)))


def _weighted_score(components, expected):
    available = {name: value for name, value in components.items() if value is not None}
    missing = [name for name in expected if name not in available]
    coverage = len(available) / len(expected) if expected else 0
    raw = mean(available.values()) if available else None
    return {
        "score": round(raw * coverage, 1) if raw is not None else None,
        "confidence": round(coverage, 2),
        "components": available,
        "missing_data": missing,
        "version": SCORE_VERSION,
    }


def investment_score(fundamental):
    expected = ("return_on_equity", "debt_to_equity", "revenue_growth", "earnings_growth", "pe_ratio")
    if fundamental is None:
        return _weighted_score({}, expected)
    pe_score = None
    if fundamental.pe_ratio is not None and fundamental.pe_ratio > 0:
        pe_score = _clamp(100 - abs(fundamental.pe_ratio - 20) * 2.5)
    components = {
        "return_on_equity": _clamp((fundamental.return_on_equity or 0) * 4)
        if fundamental.return_on_equity is not None else None,
        "debt_to_equity": _clamp(100 - fundamental.debt_to_equity * 50)
        if fundamental.debt_to_equity is not None else None,
        "revenue_growth": _clamp(50 + fundamental.revenue_growth * 2)
        if fundamental.revenue_growth is not None else None,
        "earnings_growth": _clamp(50 + fundamental.earnings_growth * 2)
        if fundamental.earnings_growth is not None else None,
        "pe_ratio": pe_score,
    }
    return _weighted_score(components, expected)


def technical_snapshot(symbol):
    rows = list(
        StockEquityCandle.objects.filter(symbol=symbol, interval_minutes=1440)
        .order_by("-timestamp")
        .values("timestamp", "close", "high", "low", "volume")[:220]
    )
    if len(rows) < 20:
        rows = list(
            StockEquityCandle.objects.filter(symbol=symbol, interval_minutes=15)
            .order_by("-timestamp")
            .values("timestamp", "close", "high", "low", "volume")[:220]
        )
    rows.reverse()
    closes = [float(row["close"]) for row in rows if row["close"] is not None]
    volumes = [float(row["volume"] or 0) for row in rows]
    latest = closes[-1] if closes else None
    sma20 = mean(closes[-20:]) if len(closes) >= 20 else None
    sma50 = mean(closes[-50:]) if len(closes) >= 50 else None
    momentum20 = (latest / closes[-21] - 1) * 100 if len(closes) >= 21 and closes[-21] else None
    returns = [
        closes[index] / closes[index - 1] - 1
        for index in range(max(1, len(closes) - 20), len(closes))
        if closes[index - 1]
    ]
    volatility20 = pstdev(returns) * 100 if len(returns) > 1 else None
    average_volume = mean(volumes[-20:]) if len(volumes) >= 20 else None
    volume_ratio = volumes[-1] / average_volume if average_volume and volumes else None
    recent_lows = [float(row["low"]) for row in rows[-20:] if row["low"] is not None]
    recent_highs = [float(row["high"]) for row in rows[-20:] if row["high"] is not None]
    prior_rows = rows[-21:-1] if len(rows) >= 21 else []
    prior_lows = [float(row["low"]) for row in prior_rows if row["low"] is not None]
    prior_highs = [float(row["high"]) for row in prior_rows if row["high"] is not None]
    true_ranges = []
    for index in range(max(1, len(rows) - 20), len(rows)):
        current = rows[index]
        previous_close = rows[index - 1]["close"]
        if None in (
            current["high"],
            current["low"],
            previous_close,
        ):
            continue
        high = float(current["high"])
        low = float(current["low"])
        previous_close = float(previous_close)
        true_ranges.append(max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        ))
    return {
        "latest": latest,
        "updated_at": rows[-1]["timestamp"] if rows else None,
        "sma20": sma20,
        "sma50": sma50,
        "momentum20": momentum20,
        "volatility20": volatility20,
        "volume_ratio": volume_ratio,
        "support": min(prior_lows) if prior_lows else None,
        "resistance": max(prior_highs) if prior_highs else None,
        "current_range_low": min(recent_lows) if recent_lows else None,
        "current_range_high": max(recent_highs) if recent_highs else None,
        "atr20": mean(true_ranges) if len(true_ranges) >= 14 else None,
        "bar_count": len(rows),
    }


def swing_score(technical):
    expected = ("trend20", "trend50", "momentum20", "volume")
    latest = technical["latest"]
    components = {
        "trend20": 75 if latest is not None and technical["sma20"] is not None and latest > technical["sma20"] else 25
        if latest is not None and technical["sma20"] is not None else None,
        "trend50": 75 if latest is not None and technical["sma50"] is not None and latest > technical["sma50"] else 25
        if latest is not None and technical["sma50"] is not None else None,
        "momentum20": _clamp(50 + technical["momentum20"] * 4)
        if technical["momentum20"] is not None else None,
        "volume": _clamp(technical["volume_ratio"] * 50)
        if technical["volume_ratio"] is not None else None,
    }
    return _weighted_score(components, expected)


def fno_score(symbol):
    rows = list(
        StockOptionCandle.objects.filter(symbol=symbol)
        .order_by("-timestamp")
        .values("oi", "volume", "implied_volatility")[:200]
    )
    expected = ("open_interest", "volume", "implied_volatility")
    if not rows:
        return _weighted_score({}, expected)
    average_oi = mean(float(row["oi"] or 0) for row in rows)
    average_volume = mean(float(row["volume"] or 0) for row in rows)
    iv_values = [float(row["implied_volatility"]) for row in rows if row["implied_volatility"]]
    components = {
        "open_interest": _clamp(25 + average_oi / 1000),
        "volume": _clamp(25 + average_volume / 500),
        "implied_volatility": _clamp(mean(iv_values) * 2) if iv_values else None,
    }
    return _weighted_score(components, expected)


def stock_personality(technical):
    if technical["bar_count"] < 20:
        return {
            "label": "INSUFFICIENT HISTORY",
            "confidence": 0.0,
            "evidence": "At least 20 stored bars are required.",
        }
    momentum = technical["momentum20"]
    volatility = technical["volatility20"]
    if momentum is not None and abs(momentum) >= 8:
        label = "TRENDING"
        evidence = f"Absolute 20-period momentum is {abs(momentum):.1f}%."
    elif volatility is not None and volatility >= 3:
        label = "HIGH VOLATILITY"
        evidence = f"Twenty-period return volatility is {volatility:.1f}%."
    else:
        label = "RANGE / MEAN REVERTING"
        evidence = "Momentum and return volatility are below the trending and high-volatility thresholds."
    return {
        "label": label,
        "confidence": round(min(technical["bar_count"] / 100, 1.0), 2),
        "evidence": evidence,
    }


def multi_timeframe_evidence(symbol):
    evidence = []
    for interval, label in TIMEFRAMES:
        rows = list(
            StockEquityCandle.objects.filter(
                symbol=symbol,
                interval_minutes=interval,
            )
            .order_by("-timestamp")
            .values("timestamp", "open", "high", "low", "close", "volume")[:80]
        )
        rows.reverse()
        closes = [
            float(row["close"])
            for row in rows
            if row["close"] is not None
        ]
        average = mean(closes[-20:]) if len(closes) >= 20 else None
        latest = closes[-1] if closes else None
        state = (
            "BULLISH"
            if latest is not None and average is not None and latest > average
            else "BEARISH"
            if latest is not None and average is not None and latest < average
            else "NEUTRAL"
            if latest is not None and average is not None
            else "INSUFFICIENT"
        )
        evidence.append({
            "interval_minutes": interval,
            "label": label,
            "rows": rows,
            "updated_at": rows[-1]["timestamp"] if rows else None,
            "latest": latest,
            "sma20": average,
            "state": state,
        })
    return evidence


def timeframe_agreement(timeframes):
    qualified = [
        row["state"]
        for row in timeframes
        if row["state"] != "INSUFFICIENT"
    ]
    if len(qualified) != len(TIMEFRAMES):
        return {
            "label": "INSUFFICIENT TIMEFRAME HISTORY",
            "tone": "warning",
            "evidence": (
                f"{len(qualified)} of {len(TIMEFRAMES)} timeframes have "
                "at least 20 stored closes."
            ),
        }
    if len(set(qualified)) == 1:
        return {
            "label": f"{qualified[0]} AGREEMENT",
            "tone": (
                "success"
                if qualified[0] == "BULLISH"
                else "error"
                if qualified[0] == "BEARISH"
                else "warning"
            ),
            "evidence": "All tracked timeframes agree relative to their 20-period averages.",
        }
    return {
        "label": "MIXED TIMEFRAMES",
        "tone": "warning",
        "evidence": ", ".join(
            f"{row['label']}: {row['state']}"
            for row in timeframes
        ),
    }


def automatic_trade_levels(technical):
    required = (
        technical["latest"],
        technical["sma20"],
        technical["sma50"],
        technical["support"],
        technical["resistance"],
        technical["atr20"],
    )
    if any(value is None for value in required):
        return {
            "available": False,
            "reason": (
                "At least 50 closes, 20 prior highs/lows, and 14 true ranges "
                "are required."
            ),
        }
    latest = technical["latest"]
    if latest > technical["sma20"] > technical["sma50"]:
        direction = "LONG"
        stop = max(
            technical["support"],
            latest - 1.5 * technical["atr20"],
        )
        risk = latest - stop
        target = latest + 2 * risk
    elif latest < technical["sma20"] < technical["sma50"]:
        direction = "SHORT"
        stop = min(
            technical["resistance"],
            latest + 1.5 * technical["atr20"],
        )
        risk = stop - latest
        target = latest - 2 * risk
    else:
        return {
            "available": False,
            "reason": "Latest close, SMA20, and SMA50 do not form a directional stack.",
        }
    if risk <= 0:
        return {
            "available": False,
            "reason": "Stored support or resistance does not create positive risk.",
        }
    return {
        "available": True,
        "direction": direction,
        "entry": round(latest, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "risk": round(risk, 2),
        "reward_risk": 2.0,
        "basis": "Prior 20-bar structure and 1.5x ATR, with a 2R analytical target.",
    }


def pattern_labels(symbol):
    rows = list(
        StockEquityCandle.objects.filter(symbol=symbol, interval_minutes=1440)
        .order_by("-timestamp")
        .values("timestamp", "open", "high", "low", "close", "volume")[:22]
    )
    if len(rows) < 3:
        rows = list(
            StockEquityCandle.objects.filter(symbol=symbol, interval_minutes=15)
            .order_by("-timestamp")
            .values("timestamp", "open", "high", "low", "close", "volume")[:22]
        )
    rows.reverse()
    if len(rows) < 3:
        return [{
            "label": "INSUFFICIENT PATTERN HISTORY",
            "tone": "warning",
            "evidence": "At least three complete stored bars are required.",
        }]
    latest = rows[-1]
    previous = rows[-2]
    prior = rows[-21:-1]
    labels = []
    if prior and latest["close"] is not None:
        prior_highs = [float(row["high"]) for row in prior if row["high"] is not None]
        prior_lows = [float(row["low"]) for row in prior if row["low"] is not None]
        average_volume = mean(float(row["volume"] or 0) for row in prior)
        relative_volume = (
            float(latest["volume"] or 0) / average_volume
            if average_volume else None
        )
        if prior_highs and float(latest["close"]) > max(prior_highs):
            labels.append({
                "label": "CONFIRMED BREAKOUT",
                "tone": "success",
                "evidence": (
                    f"Close exceeded prior resistance {max(prior_highs):.2f}; "
                    f"relative volume {relative_volume:.2f}x."
                    if relative_volume is not None
                    else f"Close exceeded prior resistance {max(prior_highs):.2f}."
                ),
            })
        if prior_lows and float(latest["close"]) < min(prior_lows):
            labels.append({
                "label": "CONFIRMED BREAKDOWN",
                "tone": "error",
                "evidence": f"Close fell below prior support {min(prior_lows):.2f}.",
            })
    required = (latest["open"], latest["close"], previous["open"], previous["close"])
    if all(value is not None for value in required):
        latest_open, latest_close, previous_open, previous_close = map(float, required)
        if (
            previous_close < previous_open
            and latest_close > latest_open
            and latest_open <= previous_close
            and latest_close >= previous_open
        ):
            labels.append({
                "label": "BULLISH REVERSAL",
                "tone": "success",
                "evidence": "Latest candle bullishly engulfed the previous candle body.",
            })
        if (
            previous_close > previous_open
            and latest_close < latest_open
            and latest_open >= previous_close
            and latest_close <= previous_open
        ):
            labels.append({
                "label": "BEARISH REVERSAL",
                "tone": "error",
                "evidence": "Latest candle bearishly engulfed the previous candle body.",
            })
    return labels or [{
        "label": "NO CONFIRMED PATTERN",
        "tone": "warning",
        "evidence": "Stored bars do not satisfy the breakout, breakdown, or engulfing rules.",
    }]


def stock_intelligence_context(symbol, now=None):
    symbol = symbol.upper()
    now = now or timezone.now()
    fundamental = StockFundamentalSnapshot.objects.filter(symbol=symbol).first()
    technical = technical_snapshot(symbol)
    previews = {
        "INVESTMENT": investment_score(fundamental),
        "SWING": swing_score(technical),
        "FNO": fno_score(symbol),
    }
    persisted = {}
    for row in StockScoreSnapshot.objects.filter(symbol=symbol).order_by("score_type", "-source_at"):
        persisted.setdefault(row.score_type, row)
    timeframes = multi_timeframe_evidence(symbol)
    return {
        "fundamental": fundamental,
        "results": StockFinancialResult.objects.filter(symbol=symbol)[:12],
        "news": StockNewsItem.objects.filter(symbol=symbol)[:20],
        "corporate_actions": StockCorporateAction.objects.filter(symbol=symbol)[:20],
        "technical": technical,
        "personality": stock_personality(technical),
        "timeframes": timeframes,
        "timeframe_agreement": timeframe_agreement(timeframes),
        "trade_levels": automatic_trade_levels(technical),
        "patterns": pattern_labels(symbol),
        "active_opportunities": UnifiedOpportunity.objects.select_related("instrument").filter(
            instrument__canonical_symbol=symbol,
        ).exclude(
            status__in=[OpportunityStatus.CLOSED, OpportunityStatus.REJECTED],
        )[:10],
        "scores": [
            {
                "type": score_type,
                "preview": preview,
                "persisted": persisted.get(score_type),
            }
            for score_type, preview in previews.items()
        ],
        "generated_at": now,
    }


def persist_stock_scores(symbol, source_at=None):
    source_at = (source_at or timezone.now()).replace(second=0, microsecond=0)
    context = stock_intelligence_context(symbol, source_at)
    saved = []
    for row in context["scores"]:
        preview = row["preview"]
        snapshot, _ = StockScoreSnapshot.objects.update_or_create(
            symbol=symbol.upper(),
            score_type=row["type"],
            version=preview["version"],
            source_at=source_at,
            defaults={
                "score": preview["score"],
                "confidence": preview["confidence"],
                "components": preview["components"],
                "missing_data": preview["missing_data"],
            },
        )
        saved.append(snapshot)
    return saved
