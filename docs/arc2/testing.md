---
title: ARC 2.0 Testing
description: Test strategy, current coverage, release gates, and validation requirements for ARC 2.0
---

## Current automated baseline

The application currently has:

* 42 `test*.py` files.
* 82 test classes ending in `Tests`.
* 645 discovered `test_` methods.
* GitHub Actions validation with `manage.py check`, `collectstatic`, and Django tests excluding the browser tag.
* Playwright as a development dependency.

Existing focused coverage includes:

* Core models, services, views, and integrations.
* NIFTY strategy and live dashboard behavior.
* Manual-default execution policy and explicit legacy automatic mode.
* Staff-only signed manual order review, confirmation, and audit behavior.
* Staff enforcement for legacy unsafe methods.
* Fail-closed Telegram ingestion and protected operational status.
* POST-only legacy synchronization and archive controls.
* Session authentication for browser routes and enforced-CSRF rejection.
* Consolidated operations health without credential disclosure.
* Staff-only in-app release status with separate implementation, testing,
  deployment, blocker, and production-gate evidence.
* Critical-page query budgets and activation smoke behavior.
* Command Centre regime, scenarios, freshness, breadth, access, and rendering.
* Canonical instrument uniqueness, alias resolution, and idempotent reconciliation.
* Opportunity deduplication, evidence revisions, lifecycle guards, provenance, synchronization, and inbox access.
* Stock Intelligence technical evidence, normalized provider records, missing-data penalties, rendering, and score persistence.
* Safe scanner validation, fresh-quote evaluation, approaching and confirmed labels, cooldown, deduplication, acknowledgement, enforced CSRF, and broker isolation.
* Investment thesis validation, staff and CSRF controls, holdings, allocation, dividends, review reminders, and broker-record isolation.
* Strategy template validation, bounded parameters and costs, source-data hashing, manifest deduplication, immutability, CSRF, and broker isolation.
* Unified performance calculations, broker and paper separation, evidence limitations, grouped dimensions, and staff access.
* Provider-neutral market-context dry runs, idempotent imports, strict category validation, freshness, and calendar rendering.
* Multi-timeframe candlesticks, deterministic stock-pattern labels, and consolidated F&O desk evidence.
* Authenticated browser smoke for Home, Options, Equities, Stock Intelligence, Opportunity Inbox, Scanner Builder, Investment Desk, Strategy Lab, Performance, and Operations.
* Authenticated browser smoke for Research Assistant, Trade Journal, F&O Desk, Strategy Lab, and Performance returned HTTP 200 with no horizontal overflow.
* CAS nowcast.
* Dynamic strategy calculations.
* Quote throttling.
* Options navigation, editing, pagination, and cache behavior.
* Radar strategy, history, readiness, assistant, workspace, and UI behavior.

## Test pyramid

### Unit tests

Use for:

* Strategy calculations.
* Sizing and charges.
* Parsers and normalization.
* Freshness classification.
* Identity mapping.
* State transitions.
* Configuration validation.

All strategy tests should use fixed clocks and deterministic fixtures.

### Database and service tests

Use for:

* Model constraints and indexes.
* Opportunity and decision persistence.
* Worker leases.
* Idempotent imports.
* Backfill checkpoints.
* Provider response mapping.
* Broker request construction.

External HTTP and WebSocket calls must use fixtures or fakes.

### Route and authorization tests

Release 1 requires a route matrix that verifies:

* Anonymous access.
* Authenticated non-staff access.
* Staff access.
* GET versus POST behavior.
* CSRF enforcement.
* Ingestion-token success, failure, and missing-configuration behavior.
* No broker call occurs on unauthorized or preview requests.

### Contract tests

Add for:

* Market ticker payload.
* NIFTY status payload.
* CAS status payload.
* Radar workspace payload.
* Telegram ingest payload variants.
* Dhan provider mappings.

Versioned contracts should reject unknown or invalid fields where practical.

### Browser tests

Critical journeys:

* Login and task-oriented navigation.
* Options and Equities visibility on desktop and mobile.
* Radar load, filter, chart, watch, and paper decision.
* Manual order preview and cancellation before submission.
* Journal close.
* Provider unavailable and stale-data states.
* Operations health and deployment drift.

Browser tests should run against deterministic fixtures, not live Dhan or Telegram.

### Migration tests

Before any ARC 2.0 schema release:

* Build an empty database from all migrations.
* Upgrade a database from the previous production migration.
* Run against a production-sized PostgreSQL copy.
* Measure locks and duration.
* Verify row counts, uniqueness, and unresolved mappings.
* Verify old and new application versions against the additive schema.
* Rehearse backfill interruption and resume.

## Strategy validation policy

A strategy change is not a normal refactor. It requires:

* A new explicit strategy or protocol version.
* Known-answer tests.
* Chronological split or walk-forward evidence.
* Costs, spread, slippage, latency, and quote availability.
* Data coverage and survivorship analysis.
* Comparison with a zero-skill or appropriate control.
* Separate backtest and forward-live reporting.
* No automatic promotion to live execution.

## Provider failure tests

Each provider integration should test:

* Timeout.
* Authentication failure.
* Rate limit.
* Invalid payload.
* Empty valid response.
* Stale response.
* Partial response.
* Recovery after failure.
* Explicit fallback labeling.

## Performance tests

Initial page budgets should be agreed in Release 1. Measure:

* Server response time.
* Database query count and duration.
* Serialized payload size.
* Browser render and interaction time.
* Polling frequency.
* Cache cold and warm behavior.

Priority pages:

* Today.
* Radar.
* Options.
* Equities.
* Stock and option data.
* Journal.
* Index OI.

## Deployment validation

GitHub Actions should continue to run:

```text
python manage.py check
python manage.py collectstatic --noinput
python manage.py test --exclude-tag=browser
```

Release validation should also verify:

* The active App Service runtime serves the deployed commit.
* `/` and protected routes redirect as intended.
* Static assets match the deployed manifest.
* Database migrations are complete.
* The deployment marker is current.
* The VM follows the marker.
* Singleton services have one owner.
* Critical worker heartbeats are fresh.

## Release 0 validation

Release 0 requires no application test run because it changes documentation only. It does require:

* YAML frontmatter in every ARC 2.0 document.
* No H1 headings when frontmatter supplies the title.
* Valid relative links.
* No runtime or migration changes.
* Git diff limited to `docs/arc2`.

## Latest expansion validation

The current R11 Market Pulse slices passed:

* 31 focused breadth, Command Centre, strategy, and performance tests
* The critical-page database query budget
* 601 non-browser tests in 47.412 seconds
* 616 total tests in 162.395 seconds
* Django system checks during both complete runs

The tests cover all five time windows, weekend closure, attributed holiday
closure, one active session, fail-honest schedule-only behavior, fresh-universe
coverage, advance/decline percentages, opening-gap state, price distribution,
industry ranking, known-answer EMA and RSI calculations, incomplete-session
exclusion, deterministic hashing, idempotent persistence, coverage gates,
stale-snapshot rejection, and unavailable baseline states. Migration `0040`
adds the snapshot table; no JSON API change was introduced.

The first R12 breakout slice additionally passed:

* 9 focused breakout, Action List, and performance-budget tests
* 606 non-browser tests in 51.661 seconds
* 621 total tests in 147.334 seconds

The new tests verify deterministic taxonomy, transparent strategy and state
rank components, CE and PE 2R target direction, expiry filtering, manual-only
trigger actions, classification and state filtering, measured failure counts,
small-sample labelling, and Action List query-budget preservation.

The R13 technical-intelligence slice passed:

* 9 focused Stock Intelligence tests
* 609 non-browser tests in 47.660 seconds
* 15 browser-tagged tests in 93.385 seconds

The combined runner stalled during browser teardown after completing the
non-browser phase, so the browser tests were rerun as an isolated tag. Both
disjoint test sets passed and cover all 624 discovered tests. The new
assertions verify fail-closed history requirements, bullish and bearish
analytical levels, fixed 2R geometry, and complete-timeframe agreement.

The provider-neutral R14 event slice passed:

* 10 focused Stock Intelligence tests
* 610 non-browser tests in 54.199 seconds
* 15 browser-tagged tests in 104.492 seconds

The event-reaction test verifies prior-close selection and exact one-session
and five-session returns. Missing bracketing bars remain unavailable, and the
UI does not claim that observed price movement was caused by the event.

The R15 manual opportunity-decision slice passed:

* 11 focused opportunity tests in 2.252 seconds
* 612 non-browser tests in 45.311 seconds
* 15 browser-tagged tests in 95.425 seconds

The lifecycle journey validates evidence, records a complete derivative risk
plan, and approves a manual setup with actor and reason audit. Direct execution
requests from the inbox remain blocked, and no `TradeExecution` is created.
Django system checks and migration drift checks also passed.

The R16 governed intelligence slice passed:

* 4 focused AI research tests in 0.574 seconds
* 613 non-browser tests in 47.115 seconds
* 15 browser-tagged tests in 93.685 seconds

The tests verify provider-neutral suggested questions, credential-safe readiness,
traceable provider-backed responses, staff access, and fail-closed behavior.
Missing provider configuration produces no substitute snapshot.

The R17 Personal Trading Desk slice passed:

* 2 focused Personal Desk tests in 0.222 seconds
* 10 access, navigation, and query-budget tests in 2.542 seconds
* 615 non-browser tests in 45.766 seconds
* 15 browser-tagged tests in 97.461 seconds

The tests verify staff-only GET access, user-scoped alerts, manual planning
visibility, explicit unavailable reconciliation, bounded queries, and zero
broker-order or trade-execution creation from the desk.

The production-confined daily Dhan token reminder passed:

* 5 focused freshness, delivery, deduplication, and supervisor tests
* 623 non-browser tests in 123.148 seconds
* 15 browser-tagged tests in 101.023 seconds

The reminder skips staging and quiet hours, accepts only a current daily Apps UI
token, sends at most once after successful delivery, retries delivery failures,
and excludes token values from Telegram content. Its supervised loop activates
only when production and Telegram delivery configuration are both explicit.

The shared Trading Workspace metrics cache passed:

* Cross-worker reuse after clearing the process-local Django cache
* A bounded one-query read of the persisted snapshot
* Management-command refresh output

The exact-artifact promotion script passed Bash syntax validation. The
production workflow requires staging to report the target commit, copies the
accepted Oryx files, verifies the production artifact SHA-256, and only then
restarts the production App Service.

The Dhan continuity and managed-identity research candidate passed:

* 628 non-browser tests in 137.054 seconds
* 15 browser-tagged tests in 120.779 seconds
* Django system checks, migration drift checks, and Python compilation
* Diff whitespace validation

The new coverage verifies silent-WebSocket detection, rate-safe full-quote REST
fallback, provider timestamp validation, a database-shared 1.1-second Dhan
quote interval, provider cooldown handling, Telegram-monitor stability without
a chat ID, Azure managed identity bearer authentication, GPT-5 completion-token
payloads, and a database-shared four-request-per-minute ARC budget. Modified
research support files explicitly start `stock-trap-forward-v23`; prior v22
evidence remains immutable.

Exact-commit CI run `42801318087` passed the application suite, dependency
audit, source scan, system checks, migration drift, and compilation for commit
`9f2edb1`. The startup hotfix at `ae4babd` passed exact-commit CI run
`42809422114`.

Production activation then passed:

* Staging deployment `683e76ad-01cb-49f0-9db2-b3717e5771ed`
* Passive DAST run `42810486241`
* Exact-artifact production promotion run `42810846771`
* A forced cold start that returned exact-commit health in 355 seconds
* Twelve authenticated routes with HTTP 200, no page errors, and no horizontal overflow
* Trading Workspace warm checks of 2.0 to 3.2 seconds
* Managed-identity inference returning `READY` after local authentication was disabled
* VM checkout of `ae4babd`, active supervised workers, and v23 protocol registration
* No live broker order or `TradeExecution`

Runtime inspection found that the supervisor's startup timeout remained active
after children had published healthy heartbeats, so it recycled every owned
worker after 600 seconds. Commit `b65f211` limits that timeout to workers that
have not produced their first heartbeat. The focused 25-test supervisor and
release-dashboard run passed, followed by 629 non-browser tests in 122.149
seconds. Exact-commit CI run `42826935105`, staging deployment
`958ec84e-8d8f-4757-a4d9-db5d5b7cf0f0`, DAST run `42829442685`, and production
promotion run `42829673287` passed. All six supervised workers retained fresh
heartbeats beyond 600 seconds, and no `TradeExecution` was created after
promotion.

A one-day equity recovery made 218 bounded provider requests. It retained
78,480 existing bars, preserved 409 conflicts as immutable evidence, and
inserted or overwrote zero bars. After-hours Dhan full-quote and LTP probes
returned HTTP 200 with empty `NSE_EQ` data, so market-open fallback freshness
remains an explicit acceptance gate.

## Latest Release 1 validation

The deployed Release 1 execution slice has:

* 575 tests across the full suite and 561 non-browser tests passing in production CI.
* A clean Django system check and migration drift check.
* ARC 2.0 migrations `0024`-`0039` applied to test SQLite and a restored production PostgreSQL backup.
* Populated SQLite rollback from `0037` to `0033` preserved pre-existing core signal data, and reapplication passed system checks.
* An Azure PostgreSQL point-in-time restore completed on an isolated server; production backup retention is now 14 days.
* A separate staging App Service plan forced preview mode and disabled broker credentials and background workers.
* Fifteen authenticated critical pages returned HTTP 200 without horizontal overflow.
* Steady staging DOM readiness was 220-1,532 ms; indexed series filtering reduced F&O cold-worker requests from 8.2 seconds to 0.9-1.5 seconds.
* Django 5.2.17 installed after the dependency audit identified advisories in 5.2.15.
* An isolated `pip-audit` run reporting no known vulnerabilities.
* No high-confidence exploitable findings in the focused ARC security review.
* Guarded Kudu deployment completed successfully after the workflow was hardened against stale deployment IDs, blocking CLI status tracking, and old-container warm-up responses.
* App Service and the static-IP VM synchronized at the deployed commit; manual execution, Dhan token validity, NIFTY service, collector, delivery, and evaluator progress heartbeats were verified.
* The live evaluator produced assistant snapshots under immutable research protocols through `stock-trap-forward-v22` while retaining its lease through production-sized scans.
* Approved global, macro, flow, sector, and Azure OpenAI providers remain unavailable rather than fabricating evidence.

The [release dashboard](./release-dashboard.md) tracks the remaining provider,
security, and requirements-to-tests gates.
