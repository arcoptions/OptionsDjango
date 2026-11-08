---
title: ARC 2.0 Feature Status
description: Maintained checklist of completed, pending, blocked, and legacy-gated ARC 2.0 work
---

## Status legend

The [release dashboard](./release-dashboard.md) is the authoritative record for
implementation, testing, and deployment. This file remains the compact
checklist.

* `[x]` Implemented with automated verification.
* `[ ]` Pending.
* Blocked items name the required decision or dependency.
* Legacy-gated items remain operational but are not the target ARC 2.0 workflow.
* Deployment is tracked separately and is never implied by `[x]`.

## Release 0

* [x] Audit backend, frontend, database, integrations, jobs, deployment, and tests.
* [x] Inventory current routes and navigation.
* [x] Inventory 37 models and major relationships.
* [x] Inventory 40 management commands.
* [x] Inventory external and internal data providers.
* [x] Audit research publication and experiment workspace boundaries.
* [x] Identify authentication and execution-policy gaps.
* [x] Define incremental implementation roadmap.
* [x] Confirm no runtime or schema change in Release 0.
* [x] Release 1 manual-execution boundary approved and started.

## Existing capabilities to retain

* [x] Options and Equities in left navigation.
* [x] Original light application theme.
* [x] Staff-only paper radar.
* [x] S1/S2/S3 paper opportunity lifecycle.
* [x] Per-user watches, preferences, and price alerts.
* [x] Index OI and option-chain intelligence.
* [x] Stock and option data coverage.
* [x] Daily shortlist with live/backfill separation.
* [x] Swing stock and option paper books.
* [x] Trade journal and broker P&L imports.
* [x] Explicit research publication boundary.
* [x] App Service-to-VM deployment synchronization.

## Release 1 pending

* [x] Protect browser application routes with authentication.
* [x] Require staff permission for broker, settings, and operations actions.
* [x] Make Telegram ingestion fail closed.
* [x] Convert legacy GET mutations to POST and remove read-side expiry writes.
* [x] Add ARC 2.0 manual-default execution mode.
* [x] Add protected manual order review and confirmation.
* [x] Introduce task-oriented navigation over existing destinations.
* [x] Preserve direct Options and Equities destinations.
* [x] Complete route authorization and enforced-CSRF tests.
* [x] Add request query performance budgets.
* [x] Consolidate provider and worker health.
* [x] Add post-deployment activation smoke checks.

## Release 2 pending

* [x] Build the Home Command Centre shell.
* [x] Add source-aware NIFTY and SENSEX briefing cards.
* [x] Add fresh-quote market breadth.
* [x] Add deterministic regimes and session scenarios.
* [x] Add ranked actions and personal open-position summary.
* [x] Add provider-attributed global markets and macro evidence ingestion.
* [x] Add sector, FII, and DII evidence with explicit freshness.
* [x] Add an attributed seven-day economic and earnings calendar.
* [ ] Add traceable AI daily briefing.
* [x] Add ranked long-term investment research from current, sufficiently covered provider evidence.
* [x] Define neutral canonical instrument identity.
* [x] Build dry-run and explicit-apply identity reconciliation.
* [ ] Define common provenance and freshness contract.
* [ ] Version polling JSON payloads.
* [ ] Add provider contract tests.

## Later releases

* [x] Add canonical Opportunity Inbox persistence and read UI.
* [x] Add source provenance and immutable evidence revisions.
* [x] Add guarded opportunity lifecycle and actor audit.
* [x] Add dry-run/apply synchronization from current legacy sources.
* [x] Consolidate stock overview, technical, fundamental, result, news, action, score, and derivative sections.
* [x] Add normalized provider-neutral stock intelligence records.
* [x] Add versioned Investment, Swing, and F&O scores with missing-data penalties.
* [x] Add multi-timeframe stock candlesticks and deterministic pattern labels.
* [x] Consolidate index, radar, score, opportunity, and position evidence in the F&O Desk.
* [x] Add safe configurable scanner definitions over fresh stored quotes.
* [x] Add approaching and confirmed scanner labels with cooldown and deduplication.
* [x] Add browser alert permission, local suppression, and POST-only acknowledgement.
* [x] Add a separate Investment Desk for theses, invalidation, and accumulation zones.
* [x] Add dedicated holdings, allocation, dividends, and review reminders.
* [x] Keep investment records separate from paper positions and broker execution records.
* [x] Add safe supported-template strategy definitions and bounded cost models.
* [x] Add immutable reproducible research manifests with data and input hashes.
* [x] Add unified broker and paper performance without mixing evidence classes.
* [x] Add win rate, expectancy, profit factor, paper drawdown, and grouped cuts.
* [ ] Automate approved research publication checks.
* [ ] Move workers only after ownership and idempotency proof.
* [ ] Retire duplicate identity fields only after verified backfill.

## Legacy-gated

* NIFTY automatic live order placement behind explicit `legacy_auto` mode.
* Legacy SQLite synchronization.
* Unversioned status APIs.
* Web-started background daemons.

## Blocked decisions

| Item | Blocker |
|---|---|
| Legacy NIFTY engine future | User decision to retain, convert, or retire |
| Worker migration | Choose target only after ownership and singleton tests |
| ARC 2.0 migrations | Rehearse `0024`-`0030` against a production-sized copy |
| Production deployment | Explicit user approval |
