"""Deterministic indicator calculations shared by offline analytics."""

from statistics import mean


def ema_latest(values: list[float], period: int) -> float | None:
    if period < 1:
        raise ValueError("EMA period must be positive.")
    if not values:
        return None
    average = values[0]
    multiplier = 2 / (period + 1)
    for value in values[1:]:
        average += (value - average) * multiplier
    return average


def wilder_rsi_latest(values: list[float], period: int = 14) -> float | None:
    if period < 1:
        raise ValueError("RSI period must be positive.")
    if len(values) <= period:
        return None
    changes = [
        current - previous
        for previous, current in zip(values, values[1:])
    ]
    gains = [max(change, 0) for change in changes[:period]]
    losses = [max(-change, 0) for change in changes[:period]]
    average_gain = mean(gains)
    average_loss = mean(losses)
    for change in changes[period:]:
        average_gain = (average_gain * (period - 1) + max(change, 0)) / period
        average_loss = (average_loss * (period - 1) + max(-change, 0)) / period
    if average_gain == average_loss == 0:
        return 50.0
    if average_loss == 0:
        return 100.0
    return 100 - 100 / (1 + average_gain / average_loss)
