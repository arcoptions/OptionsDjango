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
* [x] Add a staff-only in-app release dashboard with explicit production gates.

## Release 2 pending

* [x] Build the Home Command Centre shell.
* [x] Add source-aware NIFTY and SENSEX briefing cards.
* [x] Add fresh-quote market breadth.
* [x] Add deterministic regimes and session scenarios.
* [x] Add ranked actions and personal open-position summary.
* [x] Add provider-attributed global markets and macro evidence ingestion.
* [x] Add sector, FII, and DII evidence with explicit freshness.
* [x] Add an attributed seven-day economic and earnings calendar.
* [x] Add a traceable evidence-only Azure OpenAI briefing workflow with prompt, model, source, and hash retention.
* [x] Add an opt-in 8:45 AM briefing worker that remains disabled until provider configuration is approved.
* [x] Add a staff-only research and risk assistant with evidence retention, citations, uncertainty, and broker isolation.
* [x] Add provider freshness and Telegram outcome analytics with explicit data limitations.
* [x] Complete journal reflection fields with emotion and HTTPS screenshot evidence.
* [x] Gate strategy promotion to paper observation using immutable validation evidence.
* [x] Add configurable rate limiting to the scoped Telegram ingestion endpoint.
* [x] Add bounded stored-history IV percentile context to the F&O research desk.
* [x] Persist manual versus legacy automatic provenance on live execution records.
* [x] Enforce complete derivative risk and cost contracts before planning or approval.
* [x] Add ranked long-term investment research from current, sufficiently covered provider evidence.
* [x] Define neutral canonical instrument identity.
* [x] Build dry-run and explicit-apply identity reconciliation.
* [x] Define common provider, source, receipt, validity, freshness, and issue metadata.
* [x] Version browser polling JSON payloads in body and response headers.
* [x] Add provider contract tests for attribution, future timestamps, freshness, and idempotency.

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
* [x] Add provider-attributed fundamental score and confidence scanner fields with stale-evidence rejection.
* [x] Add an opening-gap scanner field from fresh day-open and previous-close evidence.
* [x] Add approaching and confirmed scanner labels with cooldown and deduplication.
* [x] Add browser alert permission, local suppression, and POST-only acknowledgement.
* [x] Add private Telegram scanner delivery with alert-version deduplication and bounded retries.
* [x] Add the advisory scanner evaluator to the App Service worker schedule.
* [x] Add a separate Investment Desk for theses, invalidation, and accumulation zones.
* [x] Add dedicated holdings, allocation, dividends, and review reminders.
* [x] Keep investment records separate from paper positions and broker execution records.
* [x] Add safe supported-template strategy definitions and bounded cost models.
* [x] Add immutable reproducible research manifests with data and input hashes.
* [x] Add deterministic template execution, explicit-cost metrics, chronological validation, and manifest comparison.
* [x] Add unified broker and paper performance without mixing evidence classes.
* [x] Add win rate, expectancy, profit factor, paper drawdown, and grouped cuts.
* [x] Add Trade Journal evidence search, date and state filters, and formula-safe CSV export.
* [x] Add fail-closed staging settings with isolated PostgreSQL naming and code-forced preview execution.
* [x] Add bounded post-deployment web and static-asset warm-up checks to CI.
* [ ] Automate approved research publication checks.
* [ ] Move workers only after ownership and idempotency proof.
* [ ] Retire duplicate identity fields only after verified backfill.

## Expansion R10-R18

The [master expansion plan](./MASTER_EXPANSION_PLAN.md) reconciles the new
Market Pulse, breakout, technical, event, intelligence, and personal-desk
requirements with existing ARC capabilities.

* [x] Reconcile the R10 market-data and universe foundation with verified
  activation evidence and explicit provider blockers.
* [x] Add five evidence-aware Indian market-session states to the Command
  Centre without claiming an unverified exchange holiday calendar.
* [x] Add fresh-universe coverage, advance/decline percentages, opening gaps,
  price distribution, and industry breadth.
* [x] Add an idempotent offline F&O-universe snapshot for EMA breadth, Wilder
  RSI distribution, complete-session volume surge, and 52-week context.
* [x] Require 80% complete-history coverage and reject stale snapshots before
  displaying historical breadth.
* [ ] Add authoritative configurable NIFTY 50, NIFTY 100, and NIFTY 500
  universe membership.
* [x] Add S1 failed-break, S2 compression-breakout, and S3 trend-continuation
  taxonomy to the unified Action List with transparent rank components.
* [x] Keep radar setups manual-only with trigger, stop, analytical 2R target,
  validity, and evidence reason.
* [x] Add classification and active-state filters plus measured 30-day
  lifecycle outcome cards with an explicit 30-trigger sample threshold.
* [ ] Add broader breakout families and verified-history acceptance.
* [x] Add deterministic stored-bar patterns, prior-structure
  support/resistance, ATR-based analytical levels, and fail-closed
  multi-timeframe agreement.
* [x] Add an attributed stock event timeline and deterministic one-session
  and five-session observed reactions over normalized stored evidence.
* [ ] Activate event ingestion and an expected-results calendar after an
  approved provider is available.
* [x] Add staff-only Opportunity Inbox validation, complete derivative risk
  planning, approval, and rejection with actor and reason audit.
* [x] Keep execution outside the Opportunity Inbox and reject direct execution
  requests without creating trade-execution records.
* [x] Add provider-neutral suggested questions, credential-safe readiness,
  traceable evidence generation, and fail-closed unavailable behavior.
* [ ] Activate governed ARC Intelligence after approved Azure OpenAI
  configuration.
* [ ] Complete personal-desk broker reconciliation with approved statements.
* [ ] Complete R18 integration, restore, independent security, and approved
  production gates.

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
| ARC 2.0 migrations | Rehearse `0024`-`0037` against a production-sized copy |
| Production deployment | Explicit user approval |
