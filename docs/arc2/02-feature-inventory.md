---
title: ARC 2.0 Feature Inventory
description: Inventory of existing ARC routes, pages, workers, research capabilities, and proposed task-oriented destinations
---

## Inventory conventions

Status values in this document mean:

* Retain: useful current behavior that should survive ARC 2.0.
* Harden: retain behavior after access-control, provenance, or reliability work.
* Consolidate: combine overlapping user journeys without deleting underlying data.
* Legacy-gated: preserve only behind an explicit compatibility boundary.
* Redesign: keep the user goal but replace the current interaction.

## Current navigation and proposed destinations

| Current item or route | Current purpose | Status | Proposed ARC 2.0 destination |
|---|---|---|---|
| Home to `/radar/` | Redirect to trading workspace | Consolidate | Today |
| Action List | Evidence-aware queue | Retain | Today |
| Options | Track option signals | Retain | Ideas > Options |
| Equities | Track equity signals | Retain | Ideas > Equities |
| Stock & Option Data | Coverage and stock drill-down | Retain | Market Data > Instruments |
| Trading Workspace | Paper radar | Retain | Today and Radar |
| NIFTY Execution | Automated strategy controls | Legacy-gated | Operations > Legacy Engine |
| Trade Journal | Open and closed execution records | Retain | Portfolio > Journal |
| Research & Archive | Published strategy evidence | Retain | Research |
| Signal Archive | Archived signal records | Consolidate | Ideas > Archive |
| Dhan API Setup | Broker credentials | Harden | Operations > Integrations |
| Scanners | Chartink triggers and promotion | Harden | Ideas > Sources |
| Telegram | Message ingestion and parsing | Harden | Ideas > Sources |
| Recommendations | Ranked signals | Consolidate | Today and Ideas |
| Index OI | OI, option chain, jumps, and charts | Retain | Market > Index Intelligence |
| NIFTY CAS | Closing auction nowcast | Retain | Market > Closing Auction |
| Dhan Orders | Manual live order placement | Redesign | Portfolio > Manual Execution |
| Daily Shortlist | Ranked stock shortlist | Retain | Ideas > Shortlist |
| Swing Alerts | Strength-dip paper strategy | Retain | Ideas > Swing |
| Swing Options | Option translation and paper book | Retain | Ideas > Swing Options |
| Front-month Options Lab | Research-to-paper candidate view | Retain | Research > Strategy Labs |
| Breakeven | Broker P&L analysis | Retain | Analytics > Performance |

## Proposed task-oriented navigation

The navigation should organize user intent rather than implementation modules:

1. Today
   * Action queue
   * Market health and stale-data warnings
   * New opportunities
   * Open positions and required journal actions
2. Radar
   * F&O workspace
   * Watchlist
   * Personal price alerts
   * Paper opportunities and decisions
3. Ideas
   * Options
   * Equities
   * Shortlist
   * Swing
   * Ingestion sources
   * Archive
4. Market
   * Index intelligence
   * Closing auction
   * Instrument and candle coverage
5. Portfolio
   * Positions
   * Manual execution
   * Journal
   * Broker imports
6. Analytics
   * Performance
   * Risk
   * Strategy outcomes
7. Research
   * Published evidence
   * Strategy labs
   * Protocol and provenance
8. Operations
   * Integrations
   * Feed and worker health
   * Legacy engine
   * Admin

Options and Equities remain first-class destinations in the left navigation. Responsive navigation must expose the same destinations and labels.

## Feature inventory by domain

### Today and operating workspace

| Capability | Current state | ARC 2.0 action |
|---|---|---|
| Action list | Implemented as a separate page | Make the default task queue |
| Market ticker | Shared-shell API with 30-second polling | Retain with source and stale state |
| Global counters | Implemented in a context processor | Cache or consolidate into one summary query |
| Radar health | Available inside radar payload | Promote actionable failures to Today |
| Deployment drift | Available on NIFTY dashboard | Move to Operations |

### Radar

| Capability | Current state | ARC 2.0 action |
|---|---|---|
| F&O reference universe | Production-populated `RadarInstrument` | Retain as canonical candidate |
| Live quote and bar collection | VM-supervised | Retain |
| S1/S2/S3 strategies | Deterministic paper strategies | Retain with versioned protocol |
| Opportunity lifecycle | Armed through exited | Retain |
| Paper acknowledgement | Take or skip, no broker order | Retain |
| Workspace preferences | Per-user JSON | Retain, later type the contract |
| Watchlist and notes | Per-user | Retain |
| Personal price alerts | Per-user, Telegram-routed | Retain |
| Sector mapping | Persisted with source URL | Retain |
| Catalyst/news records | Persisted | Retain, improve provider policy |
| Assistant snapshot | Persisted JSON | Clarify generation and expiration |
| History conflict audit | Immutable conflict record | Retain |

### Signals and ingestion

| Capability | Current state | ARC 2.0 action |
|---|---|---|
| Manual signal entry | Implemented | Harden authentication |
| Telegram ingestion | Webhook and Telethon paths | Fail closed and consolidate |
| Chartink import | Legacy synchronization and promotion | Harden and make provenance visible |
| Legacy SQLite synchronization | Available from web actions | Move to an operations command |
| Scoring | Rule-based score and recommendation | Retain as a derived assessment |
| Signal archive | Automatic and user-triggered | Move mutations to protected POST |

### Market data

| Capability | Current state | ARC 2.0 action |
|---|---|---|
| Dhan instrument master | Used by stock and radar flows | Retain, publish refresh status |
| Equity candles | Persisted by symbol | Retain, map to canonical instrument |
| Rolling option candles | Persisted by relative strike | Retain but label survivorship limitation |
| Fixed-strike ladder candles | Persisted with `K` identity | Retain separately |
| Index OI snapshots | Persisted with strike children | Retain |
| Index rolling candles | Persisted | Retain |
| Quote cache | Database and process cache paths | Define source priority and age contract |
| Download jobs | Resumable persisted status | Retain and expose in Operations |

### Portfolio and execution

| Capability | Current state | ARC 2.0 action |
|---|---|---|
| Dhan Super Order placement | Live broker call from web route | Redesign as protected manual execution |
| NIFTY automated entry | VM engine can place orders | Legacy-gate; exclude from ARC 2.0 default |
| Order-event log | Persisted generic JSON | Retain, add actor and event typing later |
| Trade execution | Links a signal to broker IDs | Retain |
| Journal close | Requires journal text | Retain, add authentication and actor |
| Broker P&L imports | Dhan, Zerodha, Sahi | Retain |
| Period summaries | Persisted | Retain |

### Strategy and analytics

| Capability | Current state | ARC 2.0 action |
|---|---|---|
| NIFTY backtest | Deterministic strategy simulation | Retain |
| Dynamic strategy research | Configurable experimental engine | Keep in Research |
| Daily shortlist | Live and backfill populations separated | Retain |
| Swing stock strategy | Paper book with live/backfill split | Retain |
| Swing option translation | Paper option book | Retain |
| Jump detector | Historical and live candidate views | Retain |
| Risk surface | Persisted/generated analysis | Retain |
| Charge estimation | Shared calculations | Retain and centralize |
| Breakeven analysis | Broker performance dashboard | Retain |

### Research publication

The `research/` workspace contains more than 200 experiment-oriented scripts plus reports and generated artifacts. Production does not read it directly. Sixteen approved CSVs are copied to `options_tracker/data/research` by `publish_research_tables`.

Retain:

* Explicit publication.
* `--check` comparison of source and published tables.
* Fixed production bytes.
* Reports that document invalidated claims and limitations.
* Held-out and walk-forward reporting.
* `simlib.py` execution, cost, control, and ledger primitives.

Redesign:

* Add immutable manifests and hashes.
* Separate raw, derived, report, and production-approved outputs.
* Remove invocation-directory dependence.
* Capture configuration, code version, provider, coverage, and generation time.
* Fail loudly when a required approved table is missing or stale.

## Management command inventory

The application has 40 management commands.

### Collection and synchronization

* `collect_index_oi`
* `backfill_index_oi_history`
* `download_stock_data`
* `track_telegram`
* `sync_to_deployed_commit`
* `radar_sync`
* `radar_backfill`
* `radar_import_bars`
* `radar_reconcile`
* `radar_sectors`
* `radar_news`
* `refresh_jump_reports`

### Runtime workers

* `run_nifty_live`
* `run_cas_nowcast`
* `radar_run`
* `radar_evaluate`
* `radar_deliver`
* `radar_supervise`
* `radar_monitor`
* `radar_health`
* `radar_preflight`

### Scheduled analysis

* `build_daily_shortlist`
* `build_swing_alerts`
* `build_risk_surface`

### Credentials and setup

* `renew_dhan_token`
* `configure_telegram`
* `configure_telegram_qr`
* `seed_reference_data`
* `record_deployment`

### Import, repair, and publication

* `ingest_broker_pnl`
* `reparse_telegram_tips`
* `publish_research_tables`

### Research and reporting

* `replay_nifty_live`
* `report_strategy_pnl`
* `report_trail_strategy`
* `diagnose_dual_strategy_features`
* `research_dual_strategies`
* `research_dynamic_strategy`
* `research_expiry_day_strategy`
* `research_normal_day_strategy`

Release 1 should document an owner, host, schedule, idempotency guarantee, and failure signal for each production command. It should not move commands between hosts until that matrix is approved.

## Feature disposition summary

* Retain the radar, market data, journal, published research, shortlists, swing strategies, P&L imports, and deployment synchronization.
* Harden every write path and external-ingestion boundary.
* Consolidate the action queue, recommendations, and overlapping archives.
* Keep automated NIFTY execution behind a legacy compatibility boundary.
* Build ARC 2.0 around manual decisions, explicit provenance, and task-oriented navigation.
