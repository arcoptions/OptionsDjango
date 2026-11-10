---
title: ARC 2.0 Database
description: Maintained database reference for current model families, invariants, identity, and migration policy
---

## Current database platforms

* Production: Azure PostgreSQL configured through `DATABASE_URL`.
* Development: SQLite with WAL and contention settings.
* Legacy import: separate SQLite data used by selected synchronization functions.

Production commands on the VM must load `/etc/optionsdjango.env`. Without it, Django falls back to local SQLite, which is not the production dataset.

## Model inventory

| Group | Models |
|---|---|
| Signals and execution | `TipSignal`, `ChatMessage`, `ChartinkTrigger`, `TradeExecution`, `DhanOrderEvent`, `AppSetting` |
| Index analytics | `IndexOISnapshot`, `IndexOptionStrikeSnapshot`, `IndexOptionCandle` |
| Stock data | `TrackedStock`, `StockEquityCandle`, `StockOptionCandle`, `DownloadJob` |
| Broker performance | `BrokerPnlEntry`, `BrokerPeriodSummary` |
| Shortlist and swing | `DailyPick`, `SwingSignal`, `SwingOptionTrade` |
| Closing auction | `CasAuctionEvent` |
| Radar | `RadarInstrument`, `RadarBar`, `RadarQuote`, `RadarOptionPoint`, `RadarOpportunity`, `RadarAlert`, `RadarDecision`, `RadarWorker`, `RadarAssistantSnapshot`, `RadarResearchProtocol`, `RadarResearchObservation`, `RadarCatalyst`, `RadarHistoryConflict` |
| Personal radar | `RadarWatch`, `RadarWorkspace`, `RadarNotificationRoute`, `RadarPriceAlert`, `RadarSector` |

Total: 37 application models.

## Enforced invariants

* Telegram chat and message IDs are unique when both are present.
* A trade execution protects its source signal from deletion.
* Index OI snapshots are indexed by underlying and newest timestamp.
* Radar instruments are unique by segment and security ID.
* Radar bars are unique by instrument and timestamp.
* Radar quotes are one-to-one with instruments.
* Radar option points are unique by instrument and timestamp.
* Radar watches are unique by user and instrument.
* Radar workspaces and notification routes are one-to-one with users.
* Radar sectors are one-to-one with instruments.
* Radar opportunity keys are unique.
* Radar research observations are one-to-one with opportunities.

## Canonical identity policy

Current canonical candidate:

* `RadarInstrument` for Dhan instrument identity.

Required canonical key:

* Provider or exchange segment.
* Provider security ID.
* Instrument kind.
* Underlying.
* Expiry for derivatives.
* Strike for options.
* Option side.
* Effective lifecycle.

Symbols remain display and lookup aliases, not durable identity.

## Record-classification policy

ARC 2.0 must keep these records distinct:

| Record | Meaning |
|---|---|
| Message | External or manual source text |
| Signal | Parsed or entered market idea |
| Opportunity | Strategy-specific setup with evidence |
| Decision | User or policy response to an opportunity |
| Paper position | Simulated position lifecycle |
| Broker order | Request sent to a broker |
| Fill | Broker-confirmed execution |
| Trade execution | Journal-facing position record |
| P&L import | Broker statement observation |
| Research observation | Frozen outcome measurement under a protocol |

## Provenance policy

Data that influences a decision should preserve:

* Source provider.
* Source identifier.
* Provider timestamp.
* Receipt timestamp.
* Completeness.
* Backfill or repair state.
* Code or strategy version.
* Original raw payload where appropriate.

## Migration policy

* Use additive migrations first.
* Test the migration graph from an empty database.
* Test against a production-sized PostgreSQL copy.
* Avoid table rewrites on high-volume candle tables.
* Backfill in restartable batches.
* Keep legacy columns through at least one verified release.
* Add constraints only after unresolved mappings are zero.
* Confirm App Service and VM code compatibility.
* Advance the deployed commit marker only after migration success.

## R11 Market Pulse database impact

The first session-awareness slice has no migration. It reads attributed
`EconomicEvent` rows with event type `MARKET_HOLIDAY` or
`EXCHANGE_HOLIDAY` when available. A weekday without such evidence remains
explicitly schedule-only rather than being recorded as a verified trading
day.

Migration `0040_marketbreadthsnapshot` adds one append-only historical breadth
snapshot table. Each row retains:

* Universe and calculation version
* Deterministic input hash
* Source and receipt timestamps
* Active and historically covered instrument counts
* Versioned metric payload
* Explicit unavailable reasons

The unique universe, version, and input-hash constraint makes reruns
idempotent. The universe, version, and descending source-time index supports
the single latest-snapshot Command Centre lookup. Minute bars remain immutable,
and historical aggregation runs only through the offline
`build_market_breadth` command.

Breakout, technical-pattern, and event-reaction models remain deferred until
their write paths, retention, indexes, and query volumes are measured.

## Planned work

No ARC 2.0 schema is approved. The next database activity is read-only identity reconciliation. See [database impact analysis](./04-database-impact-analysis.md).
