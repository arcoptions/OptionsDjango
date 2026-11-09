---
title: ARC 2.0 Stock Universe Status
description: Verified stock universe, canonical identity, historical coverage, activation steps, and remaining gaps
---

## Current production state

| Source | Coverage | Production rows | State |
|---|---:|---:|---|
| Radar F&O equity universe | 218 active NSE equities | 218 `RadarInstrument` equity rows | Live and verified |
| Radar option contracts | Current Dhan stock-option master | 74,699 `RadarInstrument` option rows | Live reference data verified |
| Shipped reviewed stock fixture | 197 stocks | 0 `TrackedStock` rows | Ready for create-only activation |
| Canonical instruments | Radar and provider-neutral identity | 0 | Reconciliation not yet applied |
| Stock equity history | Selected tracked stocks | 0 `StockEquityCandle` rows | Downloader implemented, not activated |
| Stock option history | Rolling and fixed-strike selected stocks | 0 `StockOptionCandle` rows | Downloader implemented, not activated |
| Radar minute bars | F&O equities | 314,001 `RadarBar` rows | Populated |

The Stock and Option Data page is empty because it reads the tracked-stock
models, while the production collector writes radar models. The source data is
not absent; the activation and consolidation boundary is incomplete.

## Supported universe layers

### Canonical provider universe

`CanonicalInstrument` is the provider-neutral registry. It supports:

* Exchange segment and provider security ID
* Equity, index, future, and option kinds
* Underlying symbol
* Expiry, strike, and option type
* Lot size
* Provider aliases

The Dhan detailed instrument list supplies the identity fields required to
populate this registry for NSE, BSE, and derivatives.

### Live radar universe

The radar registry currently derives the stock F&O universe from active NSE
`OPTSTK` contracts and the matching cash equities. This is the verified live
trading-research universe, not the full NSE or BSE cash market.

### Tracked research universe

`TrackedStock` is the reviewed subset used for stock history, shortlist,
technical research, and stock-option studies. The repository ships 197 rows
derived from prior broker and research activity.

The activation command now supports create-only loading:

```text
python manage.py seed_reference_data --create-missing
```

Create-only mode preserves existing broker-derived turnover, realised P&L,
contract counts, and local identity corrections.

## Historical data capability

The existing Dhan downloader supports:

* Equity intraday candles
* Equity daily candles
* Rolling ATM-relative stock-option history
* Current fixed-strike option ladders
* Resumable `DownloadJob` windows
* Duplicate prevention
* Bounded retries and explicit failed windows
* Source-specific request sizing

The official Dhan intraday intervals are 1, 5, 15, 25, and 60 minutes. ARC can
derive 3-minute and 30-minute display bars only from complete lower-interval
source bars. It must not claim that Dhan supplies native 3-minute or 30-minute
history.

## Safe activation sequence

1. Load the reviewed fixture in staging with `--create-missing`.
2. Run `download_stock_data --feed master` to refresh security IDs, lot sizes,
   and strike steps.
3. Backfill five reviewed stocks for ten trading days.
4. Verify timestamps, duplicates, gaps, and page coverage.
5. Expand to the 197-stock set in bounded resumable batches.
6. Reconcile radar instruments into canonical identity.
7. Add full NSE cash instruments to canonical identity from the Dhan master.
8. Add BSE cash instruments only after exchange-aware stock-history keys are
   introduced.

## Remaining structural gaps

### BSE and dual-listed identity

`TrackedStock` and stock candle models use `symbol` as their identity and do
not include exchange segment. The canonical registry can represent both NSE
and BSE, but the stock-history tables cannot safely store two instruments that
share a symbol.

A non-destructive migration is required before full BSE history activation:

* Add canonical instrument identity to tracked stocks and stock candles.
* Preserve current symbol joins during backfill.
* Reconcile existing rows before making canonical identity mandatory.
* Add exchange-aware uniqueness.
* Retire symbol-only identity only after verification.

### Full cash-market breadth

The 218-equity radar set covers F&O-eligible stocks. It does not represent the
complete NSE or BSE cash universe. Full breadth and search must read the
canonical Dhan master, while live subscriptions remain bounded to selected
watchlists and workflows.

### Data retention and cost

Downloading five years of minute data for every cash instrument is not a
responsible default. ARC should:

* Store daily history broadly.
* Store intraday history for active watchlists, F&O stocks, scanners, and
  reviewed research sets.
* Track requested, available, missing, and failed ranges.
* Expand coverage through explicit jobs rather than unbounded startup work.
