---
title: ARC 2.0 Master Expansion Plan
description: Current capability audit, gap matrix, provider dependencies, database impact, release plan, and test strategy for R10 through R18
---

## Evidence baseline

This plan reconciles the master expansion specification with the verified
activation branch through deployed commit `b65f211`. The current expansion
branch has 631 non-browser and 646 total passing tests. A rendered page
or HTTP 200 response is not accepted as functional evidence.

The current baseline has:

* 631 passing non-browser tests
* 646 passing tests including browser-tagged journeys
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
| Dhan live quote collection | REST continuity deployed; production WebSocket currently silent | Handshake and subscription succeed without frames; rate-safe full-quote fallback is active | Verify market-open freshness and continue provider investigation |
| Canonical instrument identity | Functional for the retained registry | Historical mappings remain immutable and sync fails closed on identity conflict | Add temporal identities for provider-reused option security IDs |
| Equity historical ingestion | Partially functional with verified real data | 860 imported 15-minute candles with matching hash and idempotent rerun | Expand intervals and universe through monitored, restartable jobs |
| Rolling option history | Blocked by provider or entitlement | Twelve official-contract requests returned Dhan `DH-905` while equity history succeeded | Dhan account or backend resolution |
| Market breadth | Partially functional | Fresh-quote metrics plus idempotent offline EMA, RSI, volume-surge, and 52-week snapshots with 80% coverage gates | Authoritative named-universe membership and enough complete history to qualify each metric |
| Market session awareness | Backend implemented only as a coarse weekday clock | Existing collectors use weekday and time windows | Exchange-calendar evidence, holidays, and five user-facing session states |
| Breakout intelligence | Partially functional | Classified S1/S2/S3 setups, transparent ranking, manual review, filters, and measured 30-day persisted lifecycle outcomes | Broader breakout families and verified-history acceptance |
| Technical charting | Functional for stored-bar scope | Multi-timeframe candlesticks, deterministic patterns, prior-structure support/resistance, ATR-based analytical levels, and timeframe agreement | Broader live-provider acceptance and additional pattern families |
| Event intelligence | Functional for normalized stored evidence | Attributed stock timeline and deterministic one-session/five-session observed price reactions over results, news, and corporate actions | Approved announcement ingestion and verified expected-results calendar |
| Unified opportunities | Fully functional for current sources | Canonical persistence, immutable revisions, state guards, audit, and synchronization | General trade-setup entity and full user-executed lifecycle across all sources |
| ARC Intelligence | Production foundation active | Managed-identity inference returned `READY`; evidence hashes, citations, and shared request budget are deployed | Activate evidence only when approved provider inputs exist |
| Watchlists and alerts | Fully functional in current radar scope | Per-user watches, price alerts, scanner deduplication, browser acknowledgement, and Telegram retries | Generalize to all new opportunity and event sources |
| Personal trading analytics | Partially functional | Journal, paper performance, broker importers, and evidence-class separation | Approved current statements and broker reconciliation |
| Release and operations evidence | Production deployed | Release Dashboard, Operations Health, exact-commit deployment health, CI, DAST, browser acceptance, and VM parity | Independent restore drill and human penetration test |

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
| R16 ARC Intelligence | Evidence package, prompt/model/hash retention, citations, managed-identity client | Production activation and bounded acceptance snapshot | Deployed-application verification |
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
| AI analysis | Azure OpenAI | Production managed-identity inference passed; local authentication is disabled | Remain unavailable when approved evidence inputs are absent |
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

The Opportunity Inbox now supports staff-only validation, complete derivative
risk planning, approval, and rejection with CSRF-protected POST actions,
mandatory reasons, authenticated actor audit, and atomic plan transitions.
Execution remains outside this workflow, and the endpoint rejects execution
requests without creating broker or trade-execution records.

### R16 ARC Intelligence

The staff-only assistant exposes provider-neutral evidence questions and a
credential-safe provider-readiness state. It records exact evidence, prompt,
provider, model, and hashes for generated analysis. When the approved provider
is unavailable, generation controls are disabled and ARC neither fabricates nor
saves a substitute response. Live activation remains gated on approved Azure
OpenAI configuration.

### R17 Personal Trading Desk

A staff-only, read-only Personal Trading Desk now unifies planned and approved
setups, user-scoped price alerts, open execution records, journal state,
performance navigation, and imported broker-statement availability. The page
has an explicit query budget, cannot mutate orders, and labels reconciliation
unavailable when no approved statement has been imported.

### R18 Integration and production hardening

Automated integration journeys, query budgets, provider-health visibility,
exact-commit CI, migration drift checks, dependency audit, source scanning,
passive staging DAST, and deployment smoke gates are implemented. A
production-only Dhan token reminder now checks the daily Apps UI update,
respects quiet hours, deduplicates successful Telegram delivery, retries failed
delivery, and never includes the token value. Restore approval, independent
human penetration testing, live-session acceptance, and production deployment
approval remain external gates.

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

The third slice adds an offline `FNO` breadth snapshot over immutable complete
one-minute sessions. It persists a deterministic input hash, source timestamp,
coverage, metrics, and unavailable reasons. EMA 20/50/200, Wilder RSI(14),
20-session average-volume surge, and 252-session high/low context appear only
when at least 80% of the active universe qualifies. Stale snapshots are
rejected, and the request path performs no historical scan.

The first R12 slice routes current persisted radar opportunities into the
unified Action List. S1, S2, and S3 retain explicit taxonomy, strategy and
state rank components, trigger, stop, analytical 2R target, evidence reason,
and validity. Triggered rows still require manual review and are never
labelled buy-ready.

The next R12 slice adds classification and active-state filters plus 30-day
persisted lifecycle outcome cards. Failure is defined narrowly as rejected,
expired, or invalidated after a recorded trigger. Rates remain labelled
insufficient until 30 triggered observations exist, so a small sample is never
presented as strategy validation.

R13 corrects support and resistance to use prior structure rather than the
current bar, calculates 20-period true-range evidence, produces fail-closed
long or short analytical entry/stop/2R target levels, and labels agreement only
when all 15-minute, hourly, and daily timeframes have 20 stored closes.

R14 combines normalized news, results, and corporate actions into an attributed
timeline. When stored daily bars bracket an event, it shows observed
one-session and five-session returns from the prior close. The UI explicitly
states that temporal reaction is not causation and fails unavailable when bars
do not bracket the event.

The current branch passed 628 non-browser tests in 137.054 seconds. All 15
browser-tagged tests passed separately in 120.779 seconds, covering 643 tests
without treating a stalled combined-run teardown as success evidence. The R15
journey proves that validation, complete planning, and approval preserve actor
and reason audit while direct execution requests remain blocked. R16 coverage
proves that suggested questions remain provider-neutral and unavailable
configuration cannot produce or persist substitute analysis. R17 coverage
proves user alert isolation, staff and method controls, read-only broker safety,
and the Personal Desk query budget. R18 reminder coverage proves production
confinement, quiet hours, UI-token freshness, successful-delivery deduplication,
retryable failure, and secret-free Telegram content. The latest candidate also
covers silent-WebSocket REST continuity, shared Dhan throttling, Telegram
monitor stability, managed-identity GPT-5 requests, and a shared ARC request
budget.

Trading Workspace coverage also verifies that a persisted display-metrics
snapshot is reusable across application workers after the process-local cache
is cleared. The production deployment workflow now promotes the exact Oryx
artifact accepted in staging instead of rebuilding it during production
promotion.

## Production activation record

Commits `9f2edb1`, `ae4babd`, and `b65f211` are active in staging, production,
and the worker VM. The latest exact-commit CI run `42826935105`, DAST run
`42829442685`, and production promotion run `42829673287` passed. A forced cold
start returned exact-commit health in 355 seconds after the synchronous metrics
warm-up was bounded to 60 seconds. Production browser acceptance covered twelve
authenticated routes, while current Trading Workspace warm checks completed in
2.0 to 3.2 seconds.

Azure OpenAI returned `READY` through the App Service managed identity after
local authentication was disabled. The VM registered prospective protocol
`stock-trap-forward-v23` without changing v22 evidence. The supervisor fix
prevented healthy workers from being recycled after 600 seconds; all six child
heartbeats remained fresh beyond that boundary. Manual execution remains
mandatory, and no live broker order was placed.

The remaining release gates require external or time-bound evidence:

* Configure the Telegram bot token and alert chat ID, then verify one safe delivery.
* Verify the Dhan REST fallback and fresh persisted timestamps during market hours.
* Resolve Dhan `DH-905` for rolling-option history.
* Add temporal provider identities before accepting reused option security IDs.
* Supply approved market-data feeds and broker statements.
* Complete an independent restore drill and human penetration test.
