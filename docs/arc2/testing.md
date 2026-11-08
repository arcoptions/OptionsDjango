---
title: ARC 2.0 Testing
description: Test strategy, current coverage, release gates, and validation requirements for ARC 2.0
---

## Current automated baseline

The application currently has:

* 28 `test*.py` files.
* 68 discovered test classes.
* 539 discovered `test_` methods.
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

## Latest Release 1 validation

The current local Release 1 execution slice has:

* 539 passing `options_tracker` tests.
* A clean Django system check.
* ARC 2.0 migrations `0024`-`0030` applied to local SQLite.
* A passing compile check and secret-free local activation report.
* Ten critical authenticated pages returning HTTP 200 without rendered server errors.
* Local DOM readiness between 366 ms and 559 ms for those ten pages.
* Django 5.2.17 installed after the dependency audit identified advisories in 5.2.15.
* An isolated `pip-audit` run reporting no known vulnerabilities.
* No high-confidence exploitable findings in the focused ARC security review.
* An expected unhealthy local activation result because deployment markers,
  live worker heartbeats, fresh feeds, and verified Dhan credentials are absent.
* No deployment or production smoke evidence.

The [release dashboard](./release-dashboard.md) tracks the remaining security,
browser, staging, and production gates.
