---
title: ARC 2.0 Architecture Assessment
description: Assessment of current architecture quality, coupling, failure modes, and target modernization principles
---

## Assessment summary

The current system is a modular Django monolith with two runtime hosts and a shared PostgreSQL database. This is a reasonable architecture for the product size. ARC 2.0 does not require microservices or a rewrite. It requires clearer module boundaries, consistent authorization, typed contracts, controlled workers, and a canonical data model.

The canonical topology is documented in [architecture](./architecture.md).

## Architecture strengths

### Cohesive core platform

* One Django application provides shared authentication, ORM, migrations, templates, admin, and operational commands.
* Domain modules already exist for radar, index OI, NIFTY execution, shortlists, swing strategies, stock data, CAS, and research.
* Management commands provide executable boundaries for long-running and scheduled work.

### Durable production handoff

* App Service owns migrations.
* The deployed commit marker is written only after migrations.
* The VM follows that marker rather than the branch tip.
* VM synchronization can defer restart while an execution position is open.

### Correct host ownership for Dhan

* The static-IP VM owns token renewal and order-capable processes.
* App Service reads the shared token but does not renew it.
* This avoids account-wide token invalidation races.

### Evidence-aware strategy design

* Paper radar decisions and evidence are persisted.
* Research protocols are versioned.
* History conflicts are recorded before repair.
* Live and backfilled strategy outcomes are separated.
* Production research reads only explicitly published tables.

## Architecture weaknesses

### Inconsistent trust boundary

Authentication policy is implemented per view rather than through an application-wide rule. New radar and CAS views are staff-protected, while older write and broker routes are not. This makes route discovery a security event.

Required direction:

* Default all application pages to authenticated access.
* Explicitly mark the small set of public read endpoints.
* Require staff permission for broker, settings, ingestion administration, and worker controls.
* Keep API-secret authentication separate from browser sessions.

### Mixed manual and automatic execution

Paper workflows and live execution share signals, orders, and navigation, but their guarantees differ sharply. The current NIFTY engine performs automatic broker actions. ARC 2.0 requires manual execution.

Required direction:

* Introduce an explicit execution mode boundary.
* New ARC 2.0 opportunity workflows stop at a manual decision and order preview.
* Legacy automation remains independently gated until a separately approved retirement plan.
* Broker calls require an auditable actor or an explicitly identified legacy engine principal.

### Database as domain store and control bus

`AppSetting` stores credentials, user settings, deployment markers, engine switches, worker state, and JSON status. This avoids extra infrastructure but obscures contracts and access patterns.

Required direction:

* Keep PostgreSQL as the shared coordination store for now.
* Introduce typed accessors and a registry of allowed keys.
* Separate secret references, configuration, commands, heartbeats, and domain records conceptually.
* Add dedicated models only when an additive migration and backfill are justified.

### Worker lifecycle coupling

Several daemons are started by `startup.sh` inside the web container. A web restart also restarts them, and process ownership is not represented in one scheduler.

Required direction:

* First document current ownership and health.
* Add one operational status page before moving workers.
* Consider Azure-native jobs or VM systemd only after idempotency and singleton guarantees are tested.

### Presentation coupling

The shared shell has a large inline stylesheet. Several templates embed sizable scripts and serialized data. Radar has its own static CSS and JavaScript.

Required direction:

* Extract design tokens and shared components incrementally.
* Keep server-rendered pages.
* Define reusable empty, stale, error, provenance, table, filter, and metric components.
* Do not adopt a frontend framework without a demonstrated requirement.

### Provider and fallback inconsistency

Some endpoints fall back to stored data, some render an empty state, and some catch provider failures. The source and age contract is not uniform.

Required direction:

Every market datum should expose:

* Provider.
* Instrument identity.
* Source timestamp.
* Received timestamp.
* Freshness state.
* Whether it is live, delayed, stored, repaired, backfilled, or derived.

No fallback should be silently presented as live success.

## Quality attribute assessment

| Attribute | Current state | Main risk | ARC 2.0 target |
|---|---|---|---|
| Correctness | Strong in radar and research evidence | Identity duplication and mixed provenance | Canonical identity and typed data contracts |
| Security | Strong on admin, radar, and CAS | Unprotected legacy mutations and broker actions | Deny-by-default browser and API policy |
| Reliability | Durable host split and worker leases | Web-started daemons and loose JSON status | Explicit ownership, heartbeats, and alerts |
| Performance | Pagination and selected caching exist | Per-request shell queries and large render payloads | Measured budgets and query profiles |
| Maintainability | Domain modules exist | Large `views.py`, inline UI, broad `AppSetting` | Bounded services and reusable components |
| Auditability | Radar decisions and order events exist | Missing actor on some mutations | Authenticated actor and immutable event trail |
| Testability | 472 test methods | Production topology and browser coverage are limited | Contract, authorization, worker, and browser tests |
| Deployability | CI and post-migration marker are strong | Oryx activation and web/worker coupling | Verified activation and documented rollback |

## Target architecture principles

1. Evolve the modular monolith.
2. Preserve PostgreSQL and existing tables until additive migrations are justified.
3. Use one canonical instrument identity.
4. Separate opportunity, decision, paper trade, broker order, execution, and journal concepts.
5. Make ARC 2.0 execution manual by default.
6. Make trust boundaries explicit and deny by default.
7. Attach source and freshness to market data.
8. Preserve immutable evidence and repair history.
9. Keep exploratory research outside production runtime.
10. Release one reversible vertical slice at a time.

## Recommended module boundaries

| Boundary | Responsibilities | Existing source to reuse |
|---|---|---|
| Identity | Instruments, contracts, broker aliases, sector | `RadarInstrument`, `TrackedStock`, instrument master |
| Market data | Quotes, bars, OI, freshness, provider metadata | radar feed, stock data, index OI |
| Ideas | Ingestion, signals, scoring, source provenance | `TipSignal`, Telegram, Chartink |
| Opportunities | Strategy output and evidence | `RadarOpportunity`, shortlist, swing |
| Decisions | Take, skip, notes, actor | `RadarDecision`, radar acknowledgement |
| Portfolio | Positions, executions, broker imports, journal | `TradeExecution`, broker P&L |
| Research | Protocols, runs, manifests, approved outputs | radar research and publication flow |
| Operations | Credentials, workers, deployment, health | management commands, systemd, `AppSetting` |

## Failure-mode assessment

| Failure | Current effect | Existing mitigation | Needed improvement |
|---|---|---|---|
| Dhan token renewed by two hosts | Previous token invalidated | VM is sole owner | Automated ownership assertion |
| Provider unavailable | Pages may use stored data or fail | Local cache and persisted snapshots | Uniform degraded-state contract |
| Radar worker overlap | Duplicate work | Database leases | Operator view and lease alerts |
| Missing bars | Strategies do not trigger | Reconciliation and conflict audit | Coverage SLO and pre-session checks |
| App deployment incomplete | Web root may be unusable | CI polling and manual restart | Post-deploy smoke and activation verification |
| VM code ahead of schema | Runtime incompatibility | Post-migration commit marker | Retain and test |
| Unprotected mutation request | Unauthorized state or order change | CSRF on normal forms | Authentication and authorization |
| Missing research table | Empty production section | Publication check | CI-enforced approved manifest |

## Decisions required before schema work

* Whether `RadarInstrument` becomes the canonical instrument table or a new neutral table is introduced.
* Whether legacy live execution remains supported, is converted to recommendation-only, or is retired.
* Which browser pages, if any, are intentionally public.
* Whether operations remain on App Service startup and systemd or move to managed jobs.
* Which research artifacts qualify for production publication.

These are tracked in [decisions](./decisions.md).
