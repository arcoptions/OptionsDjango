---
title: ARC 2.0 Decisions
description: Architecture decision log for agreed constraints, open choices, and deferred ARC 2.0 decisions
---

## Decision log format

Each decision records status, context, choice, consequences, and revisit condition.

## D-001: Modernize rather than rewrite

Status: accepted.

Context:

The current Django application has mature data, strategies, workers, deployment behavior, and tests.

Decision:

ARC 2.0 evolves the modular monolith through reversible vertical slices.

Consequences:

* Existing routes and models remain available during migration.
* Releases favor service boundaries and read models before schema replacement.
* A frontend or microservice rewrite requires separate evidence.

## D-002: Release 0 is documentation-only

Status: accepted.

Decision:

Release 0 changes only `docs/arc2`.

Consequences:

* No migration.
* No production behavior change.
* No worker, provider, credential, or deployment change.
* Release 1 requires user approval.

## D-003: Preserve the two-host topology

Status: accepted for the initial releases.

Context:

Dhan live order placement requires a static whitelisted IP. App Service has pooled outbound IPs.

Decision:

Keep the web application on App Service and static-IP responsibilities on the VM until an approved infrastructure change.

Consequences:

* VM remains the sole Dhan token-renewal owner.
* App Service remains migration owner.
* Shared PostgreSQL remains the handoff point.

## D-004: New execution is manual

Status: accepted as an ARC 2.0 constraint.

Decision:

New ARC 2.0 workflows produce evidence, opportunity, decision, and order preview. They do not automatically place broker orders.

Consequences:

* Paper radar remains paper-only.
* Manual broker submission requires staff authentication and confirmation.
* Existing NIFTY automation becomes a legacy-gated compatibility path.
* Open legacy positions must not be disrupted by a navigation or policy release.

## D-005: Preserve research publication isolation

Status: accepted.

Decision:

Production reads only explicitly published research artifacts and never reads the exploratory `research/` workspace.

Consequences:

* `research/` remains excluded from the deployment package.
* Publication remains deliberate.
* ARC 2.0 adds manifests and hashes rather than removing the boundary.

## D-006: Source and freshness are part of the data

Status: accepted.

Decision:

Every decision-facing market datum must expose provider and freshness state.

Consequences:

* Stored, repaired, backfilled, modeled, and stale data cannot masquerade as live.
* Provider failures are explicit.
* API and UI components need a shared provenance contract.

## D-007: Use additive schema evolution

Status: accepted.

Decision:

Canonical identity work starts with reconciliation, then additive references and dual reads.

Consequences:

* No destructive migration during introduction.
* High-volume candle backfills are bounded and restartable.
* Existing identity columns remain until verification is complete.

## Open decisions

### D-008: Canonical instrument implementation

Status: open.

Options:

* Promote and generalize `RadarInstrument`.
* Introduce a neutral `Instrument` model and map `RadarInstrument`.

Required evidence:

* Duplicate and unresolved identity report.
* Corporate-action handling.
* Provider alias requirements.
* Production-sized backfill cost.

### D-009: Legacy NIFTY engine future

Status: open.

Options:

* Retain behind a staff-only legacy feature flag.
* Convert to recommendation and manual submission.
* Retire after open-position and historical-record handling.

Required decision owner: user.

### D-010: Public access policy

Status: open.

Question:

Should any market ticker, engine status, signal, or research page be accessible without login?

Default recommendation:

Require authentication for all application pages and operational APIs.

### D-011: Worker hosting

Status: deferred.

Decision trigger:

Revisit after worker ownership, idempotency, heartbeat, and recovery are documented and tested.

### D-012: Frontend framework

Status: deferred.

Default:

Continue server-rendered Django with extracted components and vanilla JavaScript. Adopt a framework only if a measured interaction requirement cannot be met cleanly.
