---
title: ARC 2.0 Strategy Forensic Validation
description: Reproducibility, bias, cost, validation, and publication assessment for ARC strategy results
---

## Validation status

ARC has two separate strategy evidence families:

1. Strategy Lab creates executable, immutable manifests from persisted candles.
2. Strategy Report renders manually approved static research tables.

The Strategy Lab framework is suitable for paper-validation evidence. The
published research tables are not yet forensically linked to executable
manifests. No published return claim should be treated as independently
reproduced until that linkage exists.

## Strategy Lab controls

| Control | Implementation | Evidence status |
|---|---|---|
| Supported templates | Explicit template allowlist | Implemented and tested |
| Parameter validation | Exact fields and bounded numeric ranges | Implemented and tested |
| Cost model | Brokerage, tax, slippage, and spread | Implemented and tested |
| Data boundary | Symbol, interval, start, end, and maximum row count | Implemented and tested |
| Data identity | Ordered candle hash | Implemented and tested |
| Definition identity | Frozen definition and cost snapshots | Implemented and tested |
| Run identity | Immutable manifest hash and code version | Implemented and tested |
| Validation | 70/30 chronological split | Implemented and tested |
| Promotion | Minimum trades, positive validation expectancy, drawdown cap, owner reason | Implemented and tested |
| Broker isolation | Promotion changes paper status only | Implemented and tested |

## Lookahead assessment

### Equity momentum

Signals are evaluated after reading the current candle. Entry occurs at the
next candle open through a pending-entry state. This prevents a strategy from
entering at a price that existed before the signal was known.

The rolling close and volume windows contain only rows already processed.
Future rows do not contribute to signal generation.

### Opening range

The opening range is built from chronological bars before breakout evaluation.
Entries and exits must be checked against the exact implementation and its
tests before promotion. Intrabar target and stop ordering needs a documented
conservative rule wherever both can occur in one candle.

### NIFTY option research

The option engine reads stored one-minute observations in timestamp order.
Its live path includes data-coverage checks that can withhold a trade. A live
coverage gate cannot be credited to a historical result unless the replay
applies the same rule.

## Bias assessment

| Bias | Current control | Remaining gap |
|---|---|---|
| Lookahead | Chronological rows and next-open momentum entry | Add explicit tests for every template and exit ambiguity |
| Survivorship | None for symbol-only stock history | Full historical constituent membership is not stored |
| Moneyness recentering | Rolling option data is labeled ATM-relative | Do not present it as fixed-contract history |
| Selection | Definitions freeze symbol and parameters | Published tables lack immutable selection manifests |
| Overfitting | Chronological holdout and promotion gates | No walk-forward or multiple-testing correction |
| Data revisions | Candle hash freezes the queried rows | Provider revision history and dataset version are not stored |
| Costs | Explicit four-part cost model | Market-impact calibration is not provider-backed |
| Liquidity | Template-specific filters | Fill probability and queue position are not modeled |

## Metric assessment

Strategy Lab calculates:

* Trade count
* Win rate
* Per-trade expectancy
* Profit factor
* Compounded net return
* Maximum drawdown

These metrics are deterministic for the frozen ordered return sequence.
Promotion requires at least 20 total trades, at least five validation trades,
positive validation expectancy, no more than 25 percent validation drawdown,
and a complete chronological validation record.

These are minimum paper-observation gates, not evidence of live profitability.

## Published research boundary

Production reads only explicitly packaged research tables. This protects the
site from accidentally rendering exploratory files, but it does not prove the
published numbers.

Every promoted publication needs:

* Source dataset identity and hash
* Generation command
* Code commit
* Complete parameter and cost snapshot
* Training and validation boundaries
* Trade-level output hash
* Aggregate metric hash
* Independent rerun result
* Reviewer and approval timestamp

Until these fields exist, the report must remain labeled static approved
research rather than reproducible strategy evidence.

## Required forensic reruns

For each candidate strategy:

1. Freeze the exact source rows and compute their ordered hash.
2. Recreate the definition and cost snapshot.
3. Execute at the recorded code commit.
4. Compare trade-level rows, not only aggregate metrics.
5. Verify the chronological split and all timestamps.
6. Challenge entry and exit prices for lookahead or intrabar ambiguity.
7. Recalculate charges independently.
8. Run a later unseen period.
9. Record failures and differences.
10. Permit paper observation only after all checks pass.

## Current conclusion

The Strategy Lab implementation provides a credible deterministic foundation,
but production contains no strategy definitions or run manifests. Published
research results have not been independently reproduced from immutable
manifests. Strategy functionality is therefore backend implemented, not
production validated.

