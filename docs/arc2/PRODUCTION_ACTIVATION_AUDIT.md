---
title: ARC 2.0 Production Activation Audit
description: Evidence-based functional audit and prioritized activation backlog for the deployed ARC 2.0 application
---

## Audit scope

This audit compares the deployed application with the production activation
requirements. It distinguishes rendered user interfaces from populated data,
verified integrations, end-to-end workflows, and production runtime evidence.

Evidence was collected from:

* Production commit `ec8dee2`
* Azure App Service and static-IP VM runtime state
* Shared production PostgreSQL counts and latest timestamps
* Django models, views, services, management commands, templates, tests, and
  deployment configuration
* Existing ARC 2.0 architecture, provider, release, and testing records

The audit was refreshed on 9 November 2026 after market close. A zero fresh
quote count at that time is expected and is not evidence of an intraday feed
failure.

## Classification scale

| Level | Classification | Meaning |
|---|---|---|
| 1 | UI ONLY | A page or control exists without a connected backend workflow |
| 2 | UI + MOCK DATA | The page depends on fixtures, fabricated values, or test-only data |
| 3 | UI + STATIC DATA | The page renders packaged or manually published data |
| 4 | BACKEND IMPLEMENTED | Models and services exist, but live integration or production data is incomplete |
| 5 | API INTEGRATED | A real provider API is connected with bounded failure handling |
| 6 | LIVE DATA VERIFIED | Provider data and timestamps have been observed in production |
| 7 | END-TO-END TESTED | The complete user and data workflow has automated verification |
| 8 | PRODUCTION VERIFIED | The workflow, data, deployment, safety, and runtime have production evidence |

The assigned level is the highest level supported for the complete workflow.
A page can contain lower-level subsections even when its core workflow reaches
a higher level.

## Production runtime evidence

### Runtime and safety

| Check | Result |
|---|---|
| App Service deployed commit | `ec8dee2` |
| VM deployed commit | `ec8dee2` |
| Execution mode | `manual` |
| Automatic entries | Disabled |
| Django checks and critical routes | Healthy |
| Core radar workers | Collector, evaluator, and delivery alive |
| Dhan token ownership | VM only |
| Automated validation | 575 tracked tests; 561 non-browser tests passed in production CI |

### Populated production data

| Data family | Rows | Latest observed evidence |
|---|---:|---|
| Dhan order events | 6,147 | Production broker event history |
| Telegram tip signals | 276 | Persisted signal intake |
| Trade executions | 2 | Existing journal records |
| Index OI snapshots | 23,004 | 9 November 2026 at 10:09 UTC |
| Index strike snapshots | 9,705,592 | Persisted Dhan option-chain history |
| Index option candles | 692,868 | Persisted index derivative history |
| Radar instruments | 74,917 | 218 equities and 74,699 options |
| Radar bars | 314,001 | 9 November 2026 at 09:44 UTC |
| Radar quotes | 233 | Latest source timestamp 10:29 UTC |
| Radar option points | 190 | Persisted option observations |
| Radar opportunities | 2 | Paper radar lifecycle |
| Radar assistant snapshots | 2,321 | Latest snapshot 11:12 UTC |
| Radar research protocols | 5 | Immutable protocol history through v22 |

### Empty production data

The following tables were empty during the audit:

* Canonical instruments and aliases
* Unified opportunities, revisions, and status events
* Tracked stocks, stock equity candles, and stock option candles
* Stock fundamentals, results, news, corporate actions, and scores
* Scanner definitions, runs, and matches
* Investment ideas, holdings, and dividends
* Strategy definitions and run manifests
* Broker P&L entries and period summaries
* Market context and economic events
* AI briefing and research snapshots

This explains why several deployed pages render correctly but have no useful
production content.

## Functional module audit

| Module | Level | What works | Current gap | Primary files and data |
|---|---:|---|---|---|
| Command Centre | 4 | Deterministic regime, ranked actions, positions, provider-aware cards, freshness labels | Market context, global, macro, flow, sector, calendar, and AI snapshot tables are empty | `command_centre.py`, `ai_briefing.py`, `MarketContextSnapshot`, `EconomicEvent`, `AIBriefingSnapshot` |
| Action List | 4 | Existing tips are ranked with entries, stops, targets, scores, and source labels | Legacy tips are not synchronized into canonical opportunities; readiness validation is inconsistent by source | `views.py`, `services.py`, `TipSignal`, `sync_opportunities.py` |
| Options and Equities | 8 | Authentication, filtering, scoring, pricing, pagination, archive, and manual review are deployed | Legacy identity fields remain and canonical backfill has not run | `views.py`, `services.py`, `TipSignal`, `CanonicalInstrument` |
| Opportunity Inbox | 4 | Canonical persistence, evidence revisions, lifecycle guards, actor audit, and UI exist | All canonical opportunity tables are empty in production | `opportunities.py`, `opportunity_views.py`, `UnifiedOpportunity` |
| Trading Workspace | 6 | Dhan WebSocket collection, 218-equity universe, bars, quotes, option points, charting, paper lifecycle, freshness, and recovery are live | Coverage is limited to the configured F&O universe; browser push still uses polling rather than a server push channel | `radar_*.py`, `radar_models.py`, radar static assets |
| Signal Archive | 8 | Existing signal state, expiry, and archive controls are deployed | Canonical archive linkage is absent | `views.py`, `TipSignal` |
| Stock and Option Data | 4 | Dhan master, resumable downloads, equity, rolling-option, and strike-ladder services exist | No tracked stocks or stock candles are populated; the page is operationally empty | `stock_data.py`, `stock_views.py`, `download_stock_data.py` |
| Scanner Builder | 4 | Definitions, validation, execution, cooldown, deduplication, matches, alerts, and tests exist | No production definitions, runs, or matches exist | `scanner_engine.py`, `scanner_views.py`, scanner models |
| NIFTY execution | 8 | Dhan integration, state, sizing, position management, replay, and runtime service are deployed | Automatic entry is intentionally unavailable unless the operator explicitly selects `legacy_auto` | `live_engine.py`, `run_nifty_live.py`, execution policy |
| F&O Research | 6 | Dhan chain, OI, PCR, strike history, candles, IV context, and support/resistance have substantial live history | Canonical derivative opportunities and persisted readiness scores are not populated | `index_oi_services.py`, `fno_views.py`, index models |
| Trade Journal | 4 | Manual execution records, reflections, filters, evidence, and CSV export exist | Only two execution records exist and broker reconciliation is not populated | `views.py`, `TradeExecution` |
| Long-term Portfolio | 4 | Thesis, invalidation, accumulation, holdings, dividends, allocation, and review models exist | All investment records and provider-backed stock evidence are empty | `investment_views.py`, investment models |
| Strategy Builder | 4 | Supported templates, deterministic manifests, costs, validation, comparison, and promotion guards exist | No production strategy definitions or manifests are stored | `strategy_lab.py`, strategy models |
| Strategy Report | 3 | Approved research tables are packaged and rendered behind a publication boundary | Reported results are not linked to stored reproducible manifests; independent forensic reruns remain incomplete | `research_views.py`, packaged research data |
| Research and Archive | 3 | Approved static research tables are separated from exploratory files | Publication checks and immutable publication manifests are not automated | `publish_research_tables.py`, packaged research data |
| Performance | 4 | Unified broker/paper calculations, expectancy, profit factor, drawdown, grouping, and tests exist | Broker statement and summary tables are empty; actual net P&L is therefore unavailable | `performance.py`, `pnl_ingest.py`, broker models |
| Research Assistant | 4 | Retrieval contract, citations, persistence, uncertainty, rate limiting, and broker isolation exist | Azure OpenAI is not configured and no production research snapshots exist | `ai_views.py`, `azure_openai.py`, `AIResearchSnapshot` |
| Release Dashboard | 8 | Staff-only evidence dashboard, gates, counts, blockers, and production status are deployed | The previous release model overstates feature completion when the backing production tables are empty | `release_status.py`, release dashboard template and tests |
| Operations Health | 8 | Deployment parity, execution policy, workers, providers, token ownership, and activation smoke are deployed | Overall health is false because index ticker, radar quote, and market-context providers are stale or unavailable after market close | `operations_health.py`, `arc_activation_smoke.py` |
| Admin | 8 | Authenticated Django administration is deployed | Operational configuration remains spread across admin, environment variables, and service files | Django admin registrations and Azure settings |
| Telegram intelligence | 6 | 276 tips, secure ingestion, Telethon/public collection, parsing, source identity, and alert delivery exist | Provider performance and canonical opportunity synchronization are incomplete | `track_telegram.py`, `services.py`, Telegram models |

## Provider and credential assessment

| Provider | Integration | Production evidence | Credential state | Gap |
|---|---|---|---|---|
| Dhan REST | 5 | Millions of option-chain rows and current OI history | Configured through VM-owned token publication | Stock master and stock-history workflows have not been activated |
| Dhan WebSocket | 6 | Live collector, persisted quotes, bars, reconnect evidence | Configured | Frontend updates use polling; full NSE/BSE coverage is not subscribed |
| Dhan broker records | 5 | 6,147 order events | Configured, read-only reconciliation permitted | Statement, trade, charge, and P&L reconciliation is not populated |
| Telegram | 6 | 276 tip signals and active radar news worker | Core ingestion configured | Source analytics and canonical synchronization remain incomplete |
| NSE reference files | 5 | F&O sector and symbol enrichment code exists | Public source | Full canonical stock universe refresh is incomplete |
| Global markets | 4 | Provider-neutral schema and ingestion path exist | No approved provider configured | Select and configure a production provider |
| Macro and economic calendar | 4 | Provider-neutral schema and rendering exist | No approved provider configured | Select and configure a production provider |
| FII, DII, and sector flows | 4 | Provider-neutral schema and rendering exist | No approved provider configured | Select and configure a production provider |
| Fundamentals and corporate events | 4 | Normalized models and scoring exist | No approved provider configured | Select and configure a production provider |
| News | 4 | Normalized model and radar public-news path exist | No comprehensive approved stock-news provider | Select and configure a production provider |
| Azure OpenAI | 4 | Retrieval, citations, persistence, and tests exist | Endpoint, key, or deployment is not configured | Configure Azure OpenAI deployment and credentials |

No credential value is recorded in this audit.

## Calculation verification

| Calculation | State | Evidence still required |
|---|---|---|
| Index OI and PCR | Live data verified | Cross-check selected expiries and strikes against provider responses |
| Radar S1/S2/S3 | End-to-end tested and live observed | Continue forward outcome monitoring; no claim of proven edge |
| IV percentile | Backend tested | Validate stored-series coverage and compare selected contracts with provider output |
| Greeks | Partial | Confirm whether Dhan supplies each value or document the exact pricing model and assumptions |
| Support and resistance | Backend tested | Cross-check representative sessions against persisted chain evidence |
| Tip and investment scores | Backend tested | Recalibrate against provider-complete evidence and observed outcomes |
| Scanner scores | Backend tested | Activate definitions and verify live matches |
| Broker charges and net P&L | Backend implemented | Import statements and reconcile against broker totals |
| Strategy performance | Static report only | Reproduce each promoted result from immutable manifests and executable data |
| AI briefing and research | Backend tested | Configure provider and verify citations against stored evidence |

## Root causes

### Parallel data models were never activated together

The legacy and radar paths contain real production data, while the newer
canonical and stock-intelligence tables are empty. The implementation created
safe migration commands but did not schedule or complete their production
application.

### Feature completion was inferred from code and tests

Several release rows were marked implemented because models, views, and tests
exist. Production rows, provider configuration, and complete user workflows
were not required by the previous completion rule.

### Provider-neutral interfaces have no selected providers

Global, macro, flow, fundamental, event, news, and AI modules fail unavailable
as designed. They cannot become operational until a provider is approved and
credentials or subscriptions are supplied.

### Existing data collectors are split across model families

Radar has a large Dhan-backed universe and history, but Stock and Option Data
reads `TrackedStock`, `StockEquityCandle`, and `StockOptionCandle`. The missing
bridge makes one part of ARC data-rich and another appear empty.

## Prioritized implementation backlog

### Priority 0: Restore coherent production data

1. Correct and run canonical instrument reconciliation for the full radar
   universe with conflict reporting and resumable batches.
2. Seed `TrackedStock` from verified active radar equities without inventing
   fundamentals or history.
3. Backfill selected tracked-stock history through existing Dhan services with
   resumable jobs and explicit failures.
4. Synchronize active tips, scanner findings, and eligible radar opportunities
   into canonical opportunities.
5. Add operational counts and last-success evidence for every activation job.

### Priority 1: Make existing modules usable

1. Activate a small, reviewed scanner set and verify scheduled execution.
2. Persist canonical F&O readiness records from verified chain and radar data.
3. Import read-only broker trades and charges, then reconcile totals.
4. Connect Action List and Opportunity Inbox through one validated lifecycle.
5. Make the stock-data page report real coverage, failures, and queued work.

### Priority 2: Complete provider-dependent intelligence

1. Obtain provider decisions for global, macro, flow, fundamentals, corporate
   events, stock news, and Azure OpenAI.
2. Configure credentials only in Azure or the root-owned VM environment.
3. Run read-only provider contract tests before enabling scheduled ingestion.
4. Keep every unavailable source explicit until live evidence passes.

### Priority 3: Complete forensic and release evidence

1. Reproduce strategy results from immutable manifests.
2. Build the twelve requested end-to-end workflow tests.
3. Complete the requirements-to-tests matrix and production route assessment.
4. Update release status from production row counts and provider health rather
   than implementation presence alone.

## First implementation slice

The first code slice will repair the canonical identity and opportunity
synchronization boundary because it unlocks:

* Opportunity Inbox
* Action List provenance
* Stock universe activation
* F&O readiness
* Provider-neutral identity
* Release evidence based on populated production data

The slice must remain dry-run by default, detect conflicts, preserve existing
records, and require explicit application in staging before production.
