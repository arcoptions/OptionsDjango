---
title: ARC 2.0 Master Expansion Plan
description: Current capability audit, gap matrix, provider dependencies, database impact, release plan, and test strategy for R10 through R18
---

## Evidence baseline

This plan reconciles the master expansion specification with the verified
activation branch at commit `a6ac2b2`. The current expansion branch raises the
baseline to 593 non-browser and 608 total passing tests. A rendered page
or HTTP 200 response is not accepted as functional evidence.

The current baseline has:

* 593 passing non-browser tests
* 608 passing tests including browser-tagged journeys
* 74,917 canonical instruments and 149,834 provider aliases in staging
* 197 reviewed tracked stocks, of which 195 are active
* 860 real Dhan equity candles for a bounded five-stock pilot
* Five persisted-bar scanner matches and five immutable strategy manifests
* Passive OWASP ZAP evidence with zero high and zero medium findings
* Manual execution as the default, with no automatic ARC 2.0 order placement

## Current feature audit

| Capability | Current classification | Verified evidence | Genuine gap |
|---|---|---|---|
| Dhan REST quotes and option chain | Fully functional in bounded existing workflows | Quote, index OI, option-chain, freshness, and rate-limit tests | Consolidate remaining legacy quote contracts |
| Dhan WebSocket collection | Fully functional on the production VM | Collector heartbeat, reconnect state, packet liveness, and quote freshness | Browser server push is not justified until polling load requires it |
| Canonical instrument identity | Fully functional in staging | 74,917 mapped instruments and zero unmapped radar rows | Named index-universe membership and exchange trading status need maintained sources |
| Equity historical ingestion | Partially functional with verified real data | 860 imported 15-minute candles with matching hash and idempotent rerun | Expand intervals and universe through monitored, restartable jobs |
| Rolling option history | Blocked by provider or entitlement | Twelve official-contract requests returned Dhan `DH-905` while equity history succeeded | Dhan account or backend resolution |
| Market breadth | Partially functional | Fresh-quote coverage, advance/decline percentages, equal-weight change, opening gaps, price distribution, and industry ranking in Command Centre | Configurable universe, EMA breadth, RSI distribution, verified volume baseline, and 52-week context |
| Market session awareness | Backend implemented only as a coarse weekday clock | Existing collectors use weekday and time windows | Exchange-calendar evidence, holidays, and five user-facing session states |
| Breakout intelligence | Partially functional | Opening-range radar strategies and configurable scanners | Full breakout taxonomy, reproducible ranking, evidence cards, and false-positive analysis |
| Technical charting | Partially functional | Multi-timeframe candlesticks, RSI, overlays, and deterministic labels | Verified pattern geometry, support/resistance engine, automatic levels, and timeframe agreement |
| Event intelligence | Backend implemented but disconnected | Normalized results, actions, news, and economic-event models | Approved announcement source, timeline, waiting list, and price-reaction engine |
| Unified opportunities | Fully functional for current sources | Canonical persistence, immutable revisions, state guards, audit, and synchronization | General trade-setup entity and full user-executed lifecycle across all sources |
| ARC Intelligence | Backend implemented but provider-blocked | Evidence hashes, citations, prompt/model retention, and fail-closed behavior | Approved Azure OpenAI endpoint, key, deployment, and production snapshots |
| Watchlists and alerts | Fully functional in current radar scope | Per-user watches, price alerts, scanner deduplication, browser acknowledgement, and Telegram retries | Generalize to all new opportunity and event sources |
| Personal trading analytics | Partially functional | Journal, paper performance, broker importers, and evidence-class separation | Approved current statements and broker reconciliation |
| Release and operations evidence | Fully functional in staging | Release Dashboard, Operations Health, exact-commit deployment health, CI, DAST | Restore rehearsal, independent human penetration test, and production approval |

## Reference UI feature mapping

The specification describes interaction patterns rather than a replacement
brand. Existing ARC destinations remain the foundation.

| Requested experience | Existing ARC surface | Expansion direction |
|---|---|---|
| Market Pulse | Command Centre and Operations Health | Add five session states and configurable evidence widgets |
| Breakout Intelligence | Trading Workspace, Scanner Builder, and Action List | Add breakout taxonomy, ranking, filters, and evidence cards |
| Technical Intelligence | Trading Workspace and Stock Intelligence | Add persisted patterns, levels, and multi-timeframe agreement |
| Event Intelligence | Stock Intelligence and Command Centre calendar | Add verified announcement timeline and reaction analysis |
| Unified setups | Opportunity Inbox and Action List | Extend lifecycle without merging ideas, paper positions, and broker executions |
| ARC Intelligence | Research Assistant and traceable briefing | Reuse evidence governance after provider activation |
| Personal Trading Desk | Trade Journal, Performance, and Investment Desk | Add a unified manual workflow over existing records |

The original light ARC theme remains the default. Dark mode is optional future
work and must not replace the readable application-wide theme.

## Functional gap matrix

| Release | Implemented foundation | Missing internal work | External dependency |
|---|---|---|---|
| R10 Market data and universe | Dhan adapters, WebSocket worker, canonical identity, freshness, rate limits, bounded history transfer | Named universes, monitored interval expansion, data-quality coverage report | Dhan rolling-option resolution and authoritative membership data |
| R11 Market Pulse | Index cards, basic breadth, market context groups, scenarios | Session model, advanced breadth widgets, configurable universe, sector and distribution views | Licensed global, macro, flow, and calendar providers |
| R12 Breakout Intelligence | Opening-range strategies and configurable scanner | Breakout rules, confirmation, ranking, filters, evidence cards, failure analysis | Sufficient verified history for each rule |
| R13 Technical Intelligence | Candles, RSI, overlays, deterministic labels | Pattern geometry, support/resistance, automatic levels, multi-timeframe agreement | Historical depth and quality thresholds |
| R14 Event Intelligence | Results, actions, news, and economic-event schema | Ingestion adapters, timeline, waiting list, reaction calculations | Exchange or licensed corporate-event source |
| R15 Unified setups | Canonical opportunities, revisions, status audit, manual decisions | Cross-source setup lifecycle and user-executed trade handoff | None for the internal model; broker reconciliation remains external |
| R16 ARC Intelligence | Evidence package, prompt/model/hash retention, citations | New analysis workflows and suggested questions | Azure OpenAI configuration |
| R17 Personal Trading Desk | Journal, paper performance, broker imports, alerts | Unified task flow and reconciliation UX | Approved broker statements |
| R18 Hardening | CI, browser tests, staging, DAST, exact-commit health | End-to-end expansion journeys, performance budgets, rollback rehearsal | Restore approval, human penetration test, production approval |

## Provider dependency matrix

| Evidence | Preferred source | Current state | Failure behavior |
|---|---|---|---|
| Indian live quotes, depth, OI, instruments | Dhan | Active in existing bounded workflows | Stale or unavailable, never fabricated |
| Equity history | Dhan | Five-stock real-data pilot active | Explicit failed jobs and incomplete coverage |
| Rolling option history | Dhan | Blocked by `DH-905` | No option-history claim |
| Index membership and sectors | NSE reference data or licensed equivalent | Partial sector refresh only | Retain last verified mapping with age and unmatched count |
| Global markets | Licensed provider | Not selected | Widget unavailable with provider requirement |
| Macro and commodities | Licensed provider | Not selected | Widget unavailable |
| FII and DII flow | Authoritative provider | Not selected | Flow evidence unavailable |
| Corporate announcements and results | Exchange or licensed provider | Not selected | Event workflow unavailable |
| Company news | Licensed provider | Advisory-only partial sources | Cannot become trade evidence |
| AI analysis | Azure OpenAI | Not configured | No generated analysis |
| Broker reconciliation | User-approved statements and read-only broker records | Importers implemented, current evidence absent | Performance remains explicitly incomplete |

## Database impact assessment

R10 and the first R11 slice require no migration. They reuse:

* `CanonicalInstrument` and `InstrumentAlias` for identity
* `RadarInstrument`, `RadarQuote`, and `RadarBar` for current market evidence
* `RadarSector` for current sector mapping
* `MarketContextSnapshot` and `EconomicEvent` for attributed context
* `UnifiedOpportunity`, `OpportunityRevision`, and
  `OpportunityStatusEvent` for opportunity provenance

Future additive models are justified only after their write path and query
volume are measured:

* A market-breadth snapshot when historical breadth is required
* A breakout detection and immutable evidence revision
* A technical pattern and support/resistance observation
* An event-price reaction linked to a verified event
* A trade setup only where the current opportunity and execution boundary
  cannot represent the lifecycle without ambiguity

No high-volume candle table receives a populated non-null foreign key during a
web deployment. No existing research or broker record is deleted.

## Release-by-release implementation plan

### R10 Market data and universe foundation

Reconcile provider capabilities, canonical identity, live ownership, interval
support, named universes, data-quality states, and monitored backfills. Treat
rolling option history as blocked until Dhan resolves `DH-905`.

### R11 Market Pulse and breadth

Add exchange-session context first, then advanced breadth widgets over current
fresh evidence. Every widget includes universe, source, timestamp, refresh,
and unavailable state.

### R12 Breakout Intelligence

Implement one deterministic breakout family at a time with known-answer
fixtures, failure cases, ranking, and evidence cards. Do not mix independent
rules into an unexplained score.

### R13 Technical Intelligence

Persist reproducible pattern and level calculations only after geometry and
multi-timeframe tests pass.

### R14 Event Intelligence

Activate only after an approved provider can supply stable event identity,
source timestamps, revisions, and attribution.

### R15 Unified Opportunity and Trade Setup Engine

Extend the existing canonical opportunity lifecycle while preserving separate
idea, paper-position, broker-order, fill, and journal concepts.

### R16 ARC Intelligence

Reuse the existing evidence-governance boundary. No provider means no
generated analysis.

### R17 Personal Trading Desk

Unify manual planning, alerts, journal, performance, and reconciliation
without changing broker execution behavior.

### R18 Integration and production hardening

Complete end-to-end journeys, measured performance, provider-health
visibility, restore rehearsal, independent security validation, and approved
production deployment.

## Test strategy

Each release requires:

* Deterministic unit tests for calculations and state transitions
* Database tests for constraints, idempotency, and immutable evidence
* Provider contract tests for authentication, rate limit, stale, partial,
  empty, invalid, and recovery states
* Route tests for authentication, staff permission, method, and CSRF behavior
* Browser journeys for the complete user task, not only page rendering
* Performance budgets for query count, payload size, warm response, and render
* Migration rehearsal against an empty database and a production-sized copy
  before any schema deployment
* Exact-commit staging verification and production deployment only after
  explicit approval

Fixtures are labelled test evidence. Live-provider acceptance never uses mock
or fabricated values.

## First implementation scope

The first unblocked code slice is R11 exchange-session context on the existing
Command Centre:

* Five explicit session states
* Indian timezone boundaries
* Weekend closure
* Verified holiday closure when an attributed market-holiday event exists
* A visible warning when only schedule evidence is available
* No schema change and no provider claim

The second slice adds fresh-universe coverage, advance/decline percentages,
opening-gap fill state, price distribution, and verified industry ranking.
EMA breadth, RSI distribution, volume surge, and 52-week context remain
explicitly unavailable until their complete baselines are validated.

The current branch passed 593 non-browser tests in 85.683 seconds and 608
total tests in 173.536 seconds.
