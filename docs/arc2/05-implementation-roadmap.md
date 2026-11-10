---
title: ARC 2.0 Implementation Roadmap
description: Incremental release plan, quality gates, rollback boundaries, and approval points for ARC 2.0
---

## Roadmap rules

* ARC 2.0 modernizes the existing application.
* Each release must be independently deployable and reversible.
* Existing production data is preserved.
* Schema changes are additive before they are subtractive.
* New ARC 2.0 execution flows remain manual.
* Production deployment always requires explicit approval.
* Provider failures and stale data are visible, never silently substituted.
* The [release dashboard](./release-dashboard.md) independently tracks
  implementation, testing, and deployment evidence.

The historical Release 0 through Release 6 sequence remains the foundation.
The [master expansion plan](./MASTER_EXPANSION_PLAN.md) defines the additive
R10 through R18 program requested after activation hardening. The two sequences
must not be treated as duplicate implementations.

## Release 0: audit and architecture baseline

Status: complete in documentation, pending user review.

Deliverables:

* Existing system audit.
* Feature inventory.
* Architecture assessment.
* Database impact analysis.
* Implementation roadmap.
* Data provider inventory.
* Maintained architecture, database, API, feature status, decision, and testing references.

Exit gate:

* Documentation frontmatter and links validate.
* Git changes are limited to `docs/arc2`.
* No model, migration, runtime, environment, or deployment behavior changes.
* User approves or revises Release 1 scope.

## Release 1: secure shell and manual policy

Goal: make the existing system safer and easier to navigate without changing strategy calculations or data shape.

### Slice 1.1: access-control baseline

* Require authentication for application pages by default.
* Require staff permission for credentials, broker actions, worker controls, ingestion administration, and mutable settings.
* Make Telegram ingest fail closed when no secret is configured.
* Convert GET-triggered mutations to CSRF-protected POST actions.
* Add authorization regression tests for every route.

Rollback:

* Revert decorators and middleware policy only.
* No schema or data rollback required.

### Slice 1.2: manual-execution boundary

Current status: implemented and tested locally, not deployed. Full completion
still requires the legacy-engine decision, browser smoke, staging validation,
and production approval.

* Add an explicit ARC 2.0 manual-only mode.
* Remove live-order actions from new ARC 2.0 navigation.
* Keep the legacy NIFTY engine behind a clearly labeled feature flag and staff-only route.
* Require confirmation for manual Dhan order placement.
* Display environment, broker account, quantity, maximum loss, and order payload before submission.
* Record live broker attempts in the existing event log.

Rollback:

* Restore the old navigation and mode gate.
* Do not alter existing open-position management during this slice.

### Slice 1.3: task-oriented navigation

* Introduce Today, Radar, Ideas, Market, Portfolio, Analytics, Research, and Operations groups.
* Keep Options and Equities directly visible.
* Give mobile navigation feature parity.
* Consolidate duplicate archive and recommendation links.
* Preserve old URLs and add redirects only when necessary.

Rollback:

* Revert shell templates and context configuration.
* Existing routes remain unchanged.

### Slice 1.4: operational and performance baseline

* Add measured request budgets for the most-used pages.
* Cache or aggregate shared shell counters.
* Add query-count tests for the shell and radar.
* Expose provider age, worker heartbeat, deployment drift, and stale-data state.
* Add post-deployment smoke checks.

Release 1 exit gate:

* No anonymous state-changing route.
* No ARC 2.0 automatic broker action.
* Existing radar strategies produce identical decisions from the same fixtures.
* Target pages meet agreed warm-response budgets.
* Full non-browser suite passes.
* Critical browser journeys pass.

## Release 2: canonical read model and provenance

Goal: establish consistent identity and data contracts before rewriting storage.

* Define canonical instrument and provider-alias rules.
* Build read-only reconciliation reports across existing models.
* Add a standard freshness and provenance envelope.
* Create typed service-layer DTOs for market data and opportunities.
* Version JSON API payloads used by polling pages.
* Add contract tests and provider fixtures.

Prefer no high-volume schema change in the first slice. If a small additive mapping table is required, review it separately.

## Release 3: opportunities, decisions, and portfolio

Goal: unify the user workflow without conflating ideas and trades.

* Present signals, shortlists, swing candidates, and radar opportunities through a common read model.
* Preserve strategy-specific evidence.
* Standardize take, skip, watch, annotate, and expire decisions.
* Keep paper positions separate from broker executions.
* Build a manual order ticket from an approved opportunity.
* Reconcile broker fills and imported P&L into the journal.

Any new tables must follow the staged migration plan in [database impact analysis](./04-database-impact-analysis.md).

## Release 4: market data and worker operations

Goal: make data ownership, health, and recovery explicit.

* Publish the command ownership and schedule matrix in the application.
* Add dataset coverage and freshness service-level objectives.
* Standardize retries, rate limits, and error states.
* Move web-started daemons only after singleton and idempotency tests exist.
* Add restartable backfill orchestration.
* Preserve immutable conflict and repair records.

## Release 5: reproducible research platform

Goal: promote only evidence that can be reproduced and audited.

* Add immutable data manifests and hashes.
* Capture code commit, configuration, provider, date range, and contract coverage.
* Separate exploratory, validated, and production-approved artifacts.
* Automate known-answer tests and walk-forward reports.
* Make publication atomic and CI-checked.
* Require explicit approval for production-facing results.

## Release 6: legacy retirement

Goal: remove duplication only after adoption and data verification.

Candidates:

* Legacy SQLite synchronization.
* Duplicate navigation and archive pages.
* Unused `AppSetting` keys.
* Duplicate instrument columns after canonical references are complete.
* Automated NIFTY execution, if the user approves retirement.
* Old templates and styles superseded by shared components.

No destructive migration belongs in the same release that introduces its replacement.

## Cross-release quality gates

### Correctness

* Deterministic strategy fixtures remain unchanged unless a reviewed strategy version changes.
* Data source, age, and fallback state are testable.
* Paper and live execution cannot be confused.

### Security

* Route access matrix is tested.
* Secrets are never rendered or logged.
* Ingestion secrets fail closed.
* Broker mutations require staff permission and CSRF or scoped API authentication.

### Performance

* Query counts and response-time budgets exist for key pages.
* High-volume migrations have measured query plans and lock budgets.
* Polling frequency and payload size are bounded.

### Operations

* One owner exists for singleton jobs.
* Every worker exposes heartbeat, last success, last error, and code version.
* Deployment smoke verifies the active runtime, not only the Azure resource state.

### Rollback

* Old code can run against the new additive schema.
* Feature flags default to the safer behavior.
* VM and App Service compatibility are checked together.

## Approval points

Explicit user approval is required before:

1. Starting Release 1 implementation.
2. Changing authentication behavior for public routes.
3. Changing or disabling the live NIFTY engine.
4. Adding the first ARC 2.0 migration.
5. Moving any production worker between hosts.
6. Deploying any ARC 2.0 release to production.
