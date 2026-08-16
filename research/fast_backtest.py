"""Caching for fast backtests when testing many variants.

load_contract_rows costs 160s and is repeated for every variant. This module
caches contracts in memory so repeated backtest_strategy calls skip the
expensive database load. The signal generation (candidate building) still runs
once per variant to allow filters to be monkeypatched into _candidate, but 27s
is acceptable when the 160s load is eliminated.

Usage in your test script:

    from fast_backtest import cached_contracts
    contracts = cached_contracts()
    for variant_name, config, candidate_filter in variants:
        SB._candidate = candidate_filter or ORIGINAL_CANDIDATE
        try:
            trades = backtest_strategy("NIFTY", 1, config, contracts=contracts)
            # process trades
        finally:
            SB._candidate = ORIGINAL_CANDIDATE

This reduces per-variant cost from 187s (160 load + 27 backtest) to ~27s.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django
django.setup()

from options_tracker.strategy_backtest import load_contract_rows

_CONTRACTS_CACHE = None


def cached_contracts():
    """Load NIFTY contracts once, cache to memory.

    Returns:
        dict: {(underlying, expiry_code, strike, option_type, relative_strike): rows}
        Suitable for passing as contracts= to backtest_strategy().
    """
    global _CONTRACTS_CACHE
    if _CONTRACTS_CACHE is None:
        _CONTRACTS_CACHE = load_contract_rows("NIFTY", 1)
    return _CONTRACTS_CACHE
