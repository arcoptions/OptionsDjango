---
title: ARC 2.0 Release Dashboard
description: Permanent source of truth for ARC 2.0 implementation, test evidence, deployment state, and release gates
---

## Product success definition

ARC 2.0 succeeds when the 9 AM workflow no longer requires several websites,
Telegram channels, and charting screens to decide what deserves attention. ARC
must bring traceable evidence together, rank what matters, expose uncertainty
and data age, and leave the final trading or investment decision with the user.

This dashboard is the authoritative status record for that outcome. A coding
summary, rendered page, or passing unit test does not make a feature complete.

## Completion rule

A feature is complete only when all three columns have evidence:

1. Implementation is connected to working backend behavior.
2. Required automated and workflow tests pass.
3. The intended environment is deployed and smoke-tested.

| Status family | Values |
|---|---|
| Implementation | `Verified baseline`, `Implemented locally`, `Partial`, `Not started`, `Blocked`, `Legacy-gated` |
| Testing | `Passed`, `Partial`, `None`, `Not applicable` |
| Deployment | `Production baseline`, `Not deployed`, `Not applicable`, `Blocked`, `Needs verification` |

`Production baseline` means the capability predates ARC 2.0 and has operated in
production. It does not prove that the latest local change is deployed.
`Implemented locally` means code and focused tests exist in the repository
working tree, but production behavior has not changed.

## Evidence snapshot

Last evidence refresh: 2026-11-08.

| Evidence | Current result |
|---|---|
| Django application tests | 544 passed |
| Test files | 28 |
| Test classes | 68 |
| Django system check | Passed with no issues |
| Dependency vulnerability audit | Passed with no known vulnerabilities after Django 5.2.17 upgrade |
| Secret-pattern scan | Passed with no tracked or workspace matches |
| Focused security review | No exploitable vulnerabilities found |
| Current ARC 2.0 schema changes | Migrations `0024`-`0032` applied to test SQLite; not deployed |
| Latest Release 1 deployment | Not performed |
| Latest local browser smoke | Passed for 10 critical authenticated pages; 366-559 ms DOM readiness |
| Latest Release 1 production smoke | Not performed |
| Existing runtime topology | App Service, shared PostgreSQL, static-IP VM |
| Execution default in local Release 1 work | Manual |
| Explicit automatic compatibility mode | `ARC_EXECUTION_MODE=legacy_auto` |

The test result covers the full `options_tracker` suite. It does not replace
browser, staging, security, migration, backup, or production smoke evidence.

## Release roll-up

| Release | Scope | Implementation | Testing | Deployment | Overall |
|---|---|---|---|---|---|
| 0 | Audit and architecture baseline | Verified baseline | Documentation checks passed | Not applicable | Complete |
| 1 | Foundation and safety | Implemented locally except legacy-auto decision | 544 application tests passed; browser and deployment gates remain | Not deployed | In progress |
| 2 | Command Centre | Partial | Passed for implemented local scope | Not deployed | In progress |
| 3 | Opportunity Inbox | Partial | Passed for implemented local scope | Not deployed | In progress |
| 4 | Stock Intelligence | Partial | Passed for implemented local scope | Not deployed | In progress |
| 5 | Market Scanner and alerts | Safe configurable scanner slice implemented locally | Passed for implemented local scope | Not deployed | In progress |
| 6 | Trading and Investment Desks | Investment Desk foundation implemented locally; F&O consolidation remains partial | Passed for implemented local scope | Not deployed | In progress |
| 7 | Strategy Lab | Safe library and immutable manifest foundation implemented locally | Passed for implemented local scope | Not deployed | In progress |
| 8 | Performance and AI | Unified performance foundation implemented locally; AI scope remains open | Passed for implemented local scope | Not deployed | In progress |
| 9 | Integration and production readiness | Existing CI and production baseline only | Partial | Blocked by Releases 1-8 | Not started |

## Release 1 foundation and safety

| ID | Feature | Implementation | Testing | Deployment | Evidence or next gate |
|---|---|---|---|---|---|
| R1-01 | Authentication baseline for all application pages | Implemented locally | Passed | Not deployed | Browser routes require a session; scoped Telegram integration endpoints retain token authentication |
| R1-02 | Staff permission for broker actions | Implemented locally | Passed | Not deployed | Staff-only Dhan order route and anonymous rejection tests |
| R1-03 | Protected manual order review and confirmation | Implemented locally | Passed | Not deployed | Signed 10-minute review, live confirmation, actor audit, stale-term rejection |
| R1-04 | Manual execution mode by default | Implemented locally | Passed | Not deployed | Central `manual`, `preview`, and `legacy_auto` policy |
| R1-05 | Existing-position protection while entries are disarmed | Implemented locally | Passed | Not deployed | Engine manages an open position even when new entries are disabled |
| R1-06 | Emergency execution kill switch | Partial | Passed | Not deployed | Entry switch exists; preview blocks broker mutations; operator workflow needs browser smoke |
| R1-07 | No unattended live orders | Blocked | Partial | Not deployed | Default is safe, but explicit `legacy_auto` remains capable of automatic entry pending final legacy-engine decision |
| R1-08 | Telegram ingestion fails closed | Implemented locally | Passed | Not deployed | Missing secret returns 503; invalid secret returns 401; valid Telegram secret is tested |
| R1-09 | GET mutations converted to POST | Implemented locally | Passed | Not deployed | Legacy sync and archive controls are POST-only; read views filter expired contracts without changing records |
| R1-10 | Route authorization matrix | Implemented locally | Passed | Not deployed | Anonymous, authenticated reader, staff, method, and enforced-CSRF behavior are covered |
| R1-11 | Nine-area task navigation | Implemented locally | Passed | Not deployed | Shared shell groups existing destinations under the ARC task areas; empty future areas remain intentionally hidden |
| R1-12 | Options and Equities remain directly accessible | Verified baseline | Passed | Production baseline | Navigation regression coverage exists |
| R1-13 | Original light visual system | Verified baseline | Partial | Production baseline | Shared shell and radar use the restored site palette |
| R1-14 | Shared design components | Partial | Partial | Production baseline | Shared shell exists; inline page styles and duplicate components remain |
| R1-15 | Canonical instrument mapping design | Implemented locally | Passed | Not deployed | Neutral identity, provider aliases, and protected nullable mappings are implemented |
| R1-16 | Worker ownership matrix | Implemented locally | Passed | Not deployed | Operations Health lists owners, state, heartbeat, age, and detail for every recorded worker |
| R1-17 | Worker and provider health dashboard | Implemented locally | Passed | Not deployed | Staff-only consolidated execution, deployment, provider, worker, and engine health page |
| R1-18 | Request and query budgets | Implemented locally | Passed | Not deployed | Action List, Options, and Operations Health have explicit regression-tested query ceilings |
| R1-19 | Post-deployment activation smoke | Implemented locally | Passed | Not deployed | `arc_activation_smoke --require-healthy` checks routes, assets, policy, commits, workers, and providers |

## Release 2 Command Centre

| ID | Feature | Implementation | Testing | Deployment | Evidence or next gate |
|---|---|---|---|---|---|
| R2-01 | Home Command Centre shell | Implemented locally | Passed | Not deployed | Home is a unified 30-second market, opportunity, risk, and health overview |
| R2-02 | Indian index briefing | Implemented locally | Passed | Not deployed | NIFTY and SENSEX cards expose price, change, PCR, source, age, regime, and stale state |
| R2-03 | Global index and macro board | Implemented locally | Passed | Not deployed | Provider-attributed ingestion and stale-state UI are ready; approved production feeds remain unconfigured |
| R2-04 | Breadth, sector, and FII/DII context | Implemented locally | Passed | Not deployed | Fresh Dhan breadth and provider-neutral sector and flow evidence retain provenance and freshness |
| R2-05 | Economic and earnings calendar | Implemented locally | Passed | Not deployed | Seven-day attributed events import idempotently; approved production feed remains unconfigured |
| R2-06 | Traceable AI daily briefing | Not started | None | Not deployed | Must retrieve evidence and retain source, timestamp, prompt, and model metadata |
| R2-07 | Bullish, bearish, and sideways scenarios | Implemented locally | Passed | Not deployed | Deterministic NIFTY and breadth thresholds select one current scenario and response |
| R2-08 | Market regime and approach | Implemented locally | Passed | Not deployed | Bullish, bearish, sideways, mixed, and insufficient-data states have explicit approaches |
| R2-09 | Ranked trading opportunities | Implemented locally | Passed | Not deployed | Home reuses the evidence-gated Action List ranking and retains review destinations |
| R2-10 | Ranked investment opportunities | Implemented locally | Passed | Not deployed | Home ranks current provider-attributed investment scores with confidence, evidence, and a research-only next action |
| R2-11 | Personal trading desk | Implemented locally | Passed | Not deployed | Home combines action counts, open positions, manual mode, and operations readiness |
| R2-12 | Source, age, confidence, and next action on every card | Partial | Passed for Home | Not deployed | Home index and opportunity cards expose evidence fields; a cross-workspace contract remains |

## Release 3 Opportunity Inbox

| ID | Feature | Implementation | Testing | Deployment | Evidence or next gate |
|---|---|---|---|---|---|
| R3-01 | Unified Opportunity Inbox | Implemented locally | Passed | Not deployed | Authenticated bounded inbox supports lifecycle filters and evidence revision counts |
| R3-02 | Telegram source intake | Implemented locally | Passed | Not deployed | Existing secure intake plus dry-run/apply synchronization of unparsed tip messages |
| R3-03 | Manual opportunity intake | Implemented locally | Passed | Not deployed | Active manual and tracked signals synchronize into canonical opportunities |
| R3-04 | Technical scanner intake | Implemented locally | Passed | Not deployed | Monitoring Chartink triggers synchronize with source provenance and review action |
| R3-05 | Fundamental, news, and announcement intake | Partial | Partial | Production baseline | Radar catalyst records exist; complete provider coverage is missing |
| R3-06 | AI and watchlist discovery | Partial | Partial | Production baseline | Radar assistant snapshots and personal watches exist |
| R3-07 | Target lifecycle from NEW through CLOSED | Implemented locally | Passed | Not deployed | Guarded NEW, VALIDATED, PLANNED, APPROVED, EXECUTED, CLOSED, and REJECTED transitions |
| R3-08 | REJECTED state and reason | Implemented locally | Passed | Not deployed | Rejection requires a reason and authenticated actor |
| R3-09 | Duplicate detection with independent setups | Implemented locally | Passed | Not deployed | Same setup deduplicates while strategy, direction, or timeframe preserves independence |
| R3-10 | Status history and authenticated actor | Implemented locally | Passed | Not deployed | Creation and transitions are audited; approval and rejection require an actor |
| R3-11 | Signal, opportunity, plan, and execution separation | Implemented locally | Passed | Not deployed | Common opportunity stores plan separately from source signals and execution records |
| R3-12 | Canonical underlying reference | Implemented locally | Passed | Not deployed | Migration `0024`, resolver, uniqueness constraints, and idempotent radar reconciliation |

## Release 4 Stock Intelligence

| ID | Feature | Implementation | Testing | Deployment | Evidence or next gate |
|---|---|---|---|---|---|
| R4-01 | Consolidated stock workspace | Implemented locally | Passed | Not deployed | Existing stock detail now exposes Overview, Technicals, Fundamentals, Results, News, Actions, Scores, and Derivatives |
| R4-02 | Overview and active opportunities | Partial | Passed for overview | Not deployed | Evidence overview is consolidated; canonical opportunity linking remains |
| R4-03 | Multi-timeframe candlestick charts | Implemented locally | Passed | Not deployed | Stock Intelligence renders stored 15-minute, hourly, and daily OHLC candlesticks |
| R4-04 | Standard technical indicators | Implemented locally | Passed | Not deployed | Stored bars produce SMA20, SMA50, momentum, volatility, and relative volume |
| R4-05 | Support, resistance, breakout, and reversal analysis | Implemented locally | Passed | Not deployed | Deterministic breakout, breakdown, bullish reversal, bearish reversal, and insufficient-history labels expose evidence |
| R4-06 | Fundamentals | Implemented locally | Passed | Not deployed | Provider-neutral normalized snapshots exist; no approved provider data is fabricated |
| R4-07 | Quarterly and annual results | Implemented locally | Passed | Not deployed | Normalized provider, period, revenue, profit, EPS, growth, and provenance records |
| R4-08 | News and corporate actions | Implemented locally | Passed | Not deployed | Deduplicated normalized news and corporate-action records render in the stock workspace |
| R4-09 | Derivatives research tab | Partial | Partial | Production baseline | Index OI and option data exist; stock-level unified derivatives view is incomplete |
| R4-10 | Statistical stock personality | Implemented locally | Passed | Not deployed | Deterministic trending, high-volatility, range, and insufficient-history labels expose confidence and evidence |
| R4-11 | Investment score | Implemented locally | Passed | Not deployed | Versioned explainable score remains unavailable when approved fundamentals are missing |
| R4-12 | Swing score | Implemented locally | Passed | Not deployed | Trend, momentum, and volume components produce a versioned confidence-aware score |
| R4-13 | F&O score | Implemented locally | Passed | Not deployed | OI, volume, and IV components produce a versioned confidence-aware score |
| R4-14 | Score explanation and missing-data penalty | Implemented locally | Passed | Not deployed | Every score exposes components, missing fields, confidence, and coverage penalty |

## Release 5 Market Scanner and alerts

| ID | Feature | Implementation | Testing | Deployment | Evidence or next gate |
|---|---|---|---|---|---|
| R5-01 | Breakout and breakdown scanners | Partial | Partial | Production baseline | Chartink, radar, and jump candidates provide subsets |
| R5-02 | Support and resistance proximity scanners | Partial | Partial | Production baseline | Strategy-specific implementations exist |
| R5-03 | Reversal and momentum scanners | Partial | Partial | Production baseline | Swing and radar cover subsets |
| R5-04 | Unusual volume and relative strength scanners | Partial | Partial | Production baseline | Used by existing candidate builders |
| R5-05 | Fundamental screener | Implemented locally | Passed | Not deployed | Safe scanner fields use current provider-attributed investment score and confidence evidence while requiring a fresh market quote |
| R5-06 | F&O scanner | Verified baseline | Passed | Production baseline | Paper radar has deterministic F&O readiness and opportunity rules |
| R5-07 | Gap and delivery-volume scanners | Partial | Passed for gap scope | Not deployed | Fresh quote day-open versus previous-close gap scanning is implemented; delivery-volume evidence still requires an approved provider |
| R5-08 | Configurable scanner definitions | Implemented locally | Passed | Not deployed | Staff-only builder accepts numeric fields and whitelisted operators without evaluating arbitrary code |
| R5-09 | In-app and Telegram alerts | Implemented locally | Passed | Not deployed | Scanner matches use private staff notification routes, alert-version deduplication, bounded retries, and explicit advisory-only text |
| R5-10 | Browser notifications | Implemented locally | Partial | Not deployed | Permission-based notifications use alert-version local suppression; browser journey evidence remains |
| R5-11 | Acknowledgement, suppression, and duplicate prevention | Implemented locally | Passed | Not deployed | Scanner matches deduplicate by definition and symbol, reset on state or cooldown, and support POST-only acknowledgement |
| R5-12 | Approaching versus confirmed trigger labels | Implemented locally | Passed | Not deployed | Configurable numeric conditions classify current matches without broker execution |

## Release 6 Trading and Investment Desks

| ID | Feature | Implementation | Testing | Deployment | Evidence or next gate |
|---|---|---|---|---|---|
| R6-01 | F&O research workspace | Implemented locally | Passed | Not deployed | Dedicated desk combines index OI, canonical derivatives opportunities, radar setups, readiness scores, and open records |
| R6-02 | Option chain, quotes, OI, IV, and Greeks | Partial | Partial | Production baseline | Core chain and selected Greeks exist; IV percentile and full futures context are incomplete |
| R6-03 | Underlying charts and indicator context | Partial | Partial | Production baseline | Radar and index pages provide subsets |
| R6-04 | Complete trade recommendation contract | Partial | Partial | Production baseline | Existing signals include many fields; three targets, charges, and common rationale are inconsistent |
| R6-05 | Risk-based option position sizing | Verified baseline | Passed | Production baseline | Existing engine and radar sizing include lot and risk constraints |
| R6-06 | Liquidity and executable-quote rejection | Verified baseline | Passed | Production baseline | Radar readiness and live-engine gates exist |
| R6-07 | Manual live execution confirmation | Implemented locally | Passed | Not deployed | Signed staff review and explicit live checkbox |
| R6-08 | Paper and live execution separation | Partial | Passed | Production baseline | Radar is paper-only; legacy engine and generic execution records still need stronger boundaries |
| R6-09 | Long-term investment workspace | Implemented locally | Passed | Not deployed | Dedicated theses, holdings, dividends, allocation, and review reminders remain separate from short-term signals |
| R6-10 | Fundamental investment screener and fair value | Not started | None | Not deployed | Depends on Release 4 fundamentals |
| R6-11 | Thesis, invalidation, and accumulation zones | Implemented locally | Passed | Not deployed | Staff-only Investment Desk validates thesis, invalidation, allocation target, accumulation range, and review date |
| R6-12 | Holdings, dividends, allocation, and review reminders | Implemented locally | Passed | Not deployed | Dedicated investment records calculate allocation and unrealized value without reusing broker execution records |

## Release 7 Strategy Lab

| ID | Feature | Implementation | Testing | Deployment | Evidence or next gate |
|---|---|---|---|---|---|
| R7-01 | Strategy library | Implemented locally | Passed | Not deployed | Staff-only library persists versioned supported-template definitions |
| R7-02 | Safe strategy builder | Implemented locally | Passed | Not deployed | Whitelisted templates reject unknown parameters, unsupported intervals, and unbounded cost inputs |
| R7-03 | Reproducible backtesting | Implemented locally | Passed | Not deployed | Manifests execute supported templates and snapshot parameters, explicit costs, metrics, code version, data range, row count, and source-data hash |
| R7-04 | Brokerage, taxes, charges, slippage, and spread | Partial | Passed | Production baseline | Shared charge and selected slippage models exist |
| R7-05 | Options contract and liquidity realism | Partial | Partial | Production baseline | Historical data limitations remain material |
| R7-06 | Bias controls and walk-forward validation | Implemented locally | Passed | Not deployed | Every new manifest stores a chronological 70/30 training and validation result using signal-before-entry template rules |
| R7-07 | Paper trading | Verified baseline | Passed | Production baseline | Radar and swing paper books exist |
| R7-08 | Strategy comparison and performance dashboard | Implemented locally | Passed | Not deployed | Strategy Lab compares immutable runs by trades, net return, drawdown, validation state, data hash, and code version |
| R7-09 | Immutable research manifests and hashes | Implemented locally | Passed | Not deployed | Identical inputs deduplicate; stored manifests reject update and deletion |
| R7-10 | Evidence-based promotion gate | Partial | Partial | Production baseline | Manual publication exists; CI approval checks are pending |

## Release 8 Performance and AI

| ID | Feature | Implementation | Testing | Deployment | Evidence or next gate |
|---|---|---|---|---|---|
| R8-01 | Unified performance dashboard | Implemented locally | Passed | Not deployed | One staff dashboard presents broker-realized and paper-radar evidence without combining them |
| R8-02 | Daily through annual P&L and equity curve | Partial | Partial | Production baseline | Several periods and curves exist, not one complete dashboard |
| R8-03 | Win rate, expectancy, profit factor, and drawdown | Implemented locally | Passed | Not deployed | Drawdown is calculated only for timestamped paper rows; unsupported broker drawdown remains explicitly unavailable |
| R8-04 | Strategy, stock, instrument, broker, and provider cuts | Partial | Passed for implemented scope | Not deployed | Broker, underlying, instrument kind, paper strategy, and paper symbol cuts are unified; provider scoring remains open |
| R8-05 | Complete journal fields, search, filters, and export | Partial | Passed for implemented scope | Not deployed | Evidence search, state and date filters, and formula-safe filtered CSV export are implemented; screenshots and emotion fields remain |
| R8-06 | Telegram provider analytics | Partial | Partial | Production baseline | Signal tracking exists; full execution-time-fair metrics are incomplete |
| R8-07 | General ARC AI assistant | Not started | None | Not deployed | Requires structured retrieval, citations, uncertainty, and reproducibility |
| R8-08 | Radar assistant | Partial | Partial | Production baseline | Persisted assistant snapshots exist with limited scope |
| R8-09 | AI market briefing | Not started | None | Not deployed | Depends on Command Centre provider coverage |
| R8-10 | AI research and risk explanations | Not started | None | Not deployed | Must never bypass deterministic controls or initiate orders |

## Cross-cutting data, operations, security, and experience

| ID | Feature | Implementation | Testing | Deployment | Evidence or next gate |
|---|---|---|---|---|---|
| X-01 | Common provider interface | Partial | Partial | Production baseline | Dhan services exist; vendor-neutral contracts are incomplete |
| X-02 | Source timestamp and freshness envelope | Partial | Partial | Production baseline | Strongest in radar and ticker; not universal |
| X-03 | LIVE, DELAYED, STALE, HISTORICAL, MISSING states | Partial | Partial | Production baseline | Several labels exist without one shared enum and contract |
| X-04 | Bounded retries, rate limits, and explicit failures | Partial | Partial | Production baseline | Provider-specific behavior exists |
| X-05 | Central operations dashboard | Not started | None | Not deployed | Health is fragmented across radar, NIFTY, CAS, and deployment views |
| X-06 | Worker heartbeats and singleton ownership | Partial | Partial | Production baseline | Radar leases and selected heartbeats exist |
| X-07 | Job locks and duplicate protection | Partial | Partial | Production baseline | Implemented in selected workers, not standardized |
| X-08 | Canonical instrument and aliases | Blocked | None | Not deployed | Requires D-008 decision and reconciliation evidence |
| X-09 | Additive migration and backfill framework | Partial | Passed for identity | Not deployed | Nullable identity mappings and dry-run/apply reconciliation exist; production-sized rehearsal remains |
| X-10 | Audit trail for material actions | Partial | Partial | Production baseline | Order, radar, and settings events exist; actor coverage is incomplete |
| X-11 | Secret management | Verified baseline | Partial | Production baseline | Azure settings and root-only VM environment are established |
| X-12 | Input validation and secure errors | Partial | Partial | Production baseline | Django forms and service checks exist; route audit remains |
| X-13 | Rate limiting | Not started | None | Not deployed | Apply selectively after endpoint threat assessment |
| X-14 | Responsive terminal UX | Partial | Partial | Production baseline | Shared responsive shell exists; target nine-area UX is pending |
| X-15 | Fast search, filters, and pagination | Partial | Partial | Production baseline | Implemented in selected tracker and radar pages |
| X-16 | No raw JSON or developer logs in normal flows | Partial | Partial | Production baseline | Most pages comply; operations and legacy views need review |
| X-17 | Functional control and empty-state audit | Partial | Partial | Production baseline | No repository-wide browser evidence yet |

## Release 9 production readiness

| ID | Gate | Current state | Required evidence |
|---|---|---|---|
| G-01 | Complete automated suite | 544 tests passed locally | Retain green CI at the release commit |
| G-02 | Requirements-to-tests matrix | Partial | Link each completed row to named tests and critical browser journeys |
| G-03 | Full route security assessment | Partial | Focused review found no exploitable vulnerabilities and route tests pass; production penetration evidence remains |
| G-04 | Dependency vulnerability assessment | Passed locally | `pip-audit` reports no known vulnerabilities on the patched requirements; secret-pattern scan found no matches |
| G-05 | Staging isolation | Open | Separate database, secrets, workers, domain, and broker-safe settings |
| G-06 | Migration rehearsal | Partial | `0024`-`0032` pass an empty test database; existing-data PostgreSQL rehearsal and rollback evidence remain |
| G-07 | Production-sized data validation | Open | Query, lock, duration, and backfill-resume evidence |
| G-08 | Performance budgets | Partial | Query ceilings pass; local browser DOM readiness was 366-559 ms across 10 critical pages, while production load and polling measurements remain |
| G-09 | Backup and restore rehearsal | Open | Fresh backup plus verified restore |
| G-10 | Staging workflow smoke | Open | All critical journeys pass with deterministic evidence |
| G-11 | Production approval | Blocked | Explicit user approval after staging report |
| G-12 | Production deployment report | Open | Commit, migrations, assets, feeds, workers, auth, execution mode, and rollback status |

## Critical workflow acceptance

| Workflow | Current status | Main gap |
|---|---|---|
| Login to Home to market briefing | Partial | Deterministic sourced briefing exists; approved production providers and traceable AI briefing remain |
| Discover stock to research to score to opportunity | Implemented locally | Provider-neutral evidence, scores, opportunities, and ranked investment research await deployment and production feeds |
| Telegram tip to parse to validate to outcome | Implemented locally | Secure intake and canonical lifecycle pass locally; production deployment and provider outcome analytics remain |
| Scanner alert to research to trade plan | Partial | Existing scanners are not connected to one trade-plan workflow |
| F&O recommendation to risk to manual approval to execution record | Implemented locally | Deployment and browser smoke are pending |
| Investment idea to thesis to holding | Implemented locally | Deployment and browser smoke evidence remain |
| Strategy to backtest to paper trade to review | Partial | Several strategy-specific paths exist without one reproducible workflow |
| Close trade to journal to performance | Partial | Core records exist; unified analytics and complete journal fields remain |

## Current blockers and decisions

| Decision | Status | Effect |
|---|---|---|
| Canonical instrument implementation | Resolved locally | Neutral identity selected; migration `0024` remains undeployed pending rehearsal |
| Legacy NIFTY automatic engine | Open | Prevents full compliance with the no-unattended-order acceptance criterion |
| Public route policy | Open | Blocks the final authentication matrix |
| Worker hosting changes | Deferred | Operations must first prove ownership, idempotency, and recovery |
| Production deployment | Approval required | Local Release 1 work remains intentionally undeployed |

## Dashboard maintenance protocol

Update this file in the same change that materially changes a feature:

1. Change implementation status only when the working behavior exists.
2. Change testing status only after recording the command or named automated
   evidence.
3. Change deployment status only after the target environment is updated.
4. Mark a production feature complete only after smoke evidence verifies the
   active runtime rather than merely the Azure resource.
5. Link design-only work as `Partial` or `Not started`, never `Complete`.
6. Preserve open failures and blockers until the evidence changes.
7. Refresh the evidence date, test counts, and release roll-up.

Release notes and Copilot summaries must link to this dashboard instead of
claiming a broader completion state.

## Evidence index

* [Existing system audit](./01-existing-system-audit.md)
* [Feature inventory](./02-feature-inventory.md)
* [Architecture assessment](./03-architecture-assessment.md)
* [Database impact analysis](./04-database-impact-analysis.md)
* [Implementation roadmap](./05-implementation-roadmap.md)
* [Data provider inventory](./06-data-provider-inventory.md)
* [Architecture](./architecture.md)
* [API](./api.md)
* [Database](./database.md)
* [Decisions](./decisions.md)
* [Feature status](./feature-status.md)
* [Testing](./testing.md)
