---
title: ARC 2.0 Existing System Audit
description: Release 0 audit of the current ARC trading application, runtime topology, risks, and modernization boundaries
---

## Release 0 scope

This audit records the system that exists before ARC 2.0 implementation begins. Release 0 is documentation-only:

* Preserve all current data and production behavior.
* Make no database or migration changes.
* Make no broker, worker, deployment, or scheduling changes.
* Do not treat exploratory research as production evidence.
* Do not deploy ARC 2.0 work without explicit approval.

The supporting inventories are [feature inventory](./02-feature-inventory.md), [architecture assessment](./03-architecture-assessment.md), [database impact analysis](./04-database-impact-analysis.md), [implementation roadmap](./05-implementation-roadmap.md), and [data provider inventory](./06-data-provider-inventory.md).

## Executive assessment

ARC is a mature Django application rather than a greenfield prototype. It already includes signal ingestion, stock and option market data, paper radar, live index execution, a trade journal, research publication, broker P&L analysis, shortlists, swing strategies, and production operations split across Azure App Service and a static-IP VM.

The strongest parts are:

* Deterministic radar logic with persisted evidence and decisions.
* A deliberate separation between the production research page and the large exploratory `research/` workspace.
* Shared PostgreSQL state across the web host and trading VM.
* Durable deployment-to-VM synchronization after migrations succeed.
* Explicit source labels that separate backfilled results from forward live records.
* A substantial automated test suite.

The highest-priority risks are:

* Several legacy mutation routes have no visible login or staff decorator.
* The Dhan order page can place a live broker order from an unprotected POST route.
* The NIFTY engine can automatically place and manage live orders, which conflicts with the ARC 2.0 requirement that execution remain manual.
* Telegram ingestion accepts unauthenticated writes when no ingest token is configured.
* Multiple model families represent instruments and trades without one canonical identity.
* Runtime behavior is distributed across web startup shell processes and systemd services without one operational control plane.
* The global context processor performs database queries on every shared-shell request.
* Styling and JavaScript are split between large templates, inline CSS, and page-specific static assets.

Release 1 should first protect the existing system and make execution policy explicit. It should not start with a schema rewrite or a broad user-interface replacement.

## Current technology baseline

| Area | Current implementation |
|---|---|
| Backend | Django 5.2.15 on Python 3.13 |
| Production database | Azure Database for PostgreSQL through `DATABASE_URL` |
| Development database | SQLite with WAL, 60-second timeout, normal synchronous mode, and immediate transactions |
| Frontend | Django templates, inline CSS, vanilla JavaScript, custom SVG and canvas-like chart rendering |
| Authentication | Django admin and session authentication, applied inconsistently outside admin and radar |
| Static assets | WhiteNoise compressed manifest storage |
| Web process | Gunicorn with two workers and two threads per worker |
| Market and broker integration | Dhan REST, WebSocket, instrument master, and order APIs |
| Messaging | Telegram Bot API, Telethon client, and public Telegram web pages |
| Deployment | GitHub Actions, Azure OIDC login, ZipDeploy/Oryx, Azure App Service |
| Dedicated workers | Azure VM with systemd for token renewal, NIFTY execution, radar, and commit synchronization |
| Automated validation | 13 test files, 52 test classes, and 472 discovered test methods |

## Functional system map

| Domain | Current capability | Primary implementation |
|---|---|---|
| Signal intake | Manual, Telegram, Chartink, and engine-created signals | `views.py`, `services.py`, `models.py` |
| Options and equities | Track, edit, score, price, filter, paginate, and archive signals | `views.py`, tracker templates |
| Paper radar | F&O universe, live quotes, S1/S2/S3 evaluation, paper lifecycle, alerts, watchlists, and charts | `radar*.py`, `radar_models.py` |
| NIFTY execution | Opening-range strategy, sizing, Dhan Super Order placement, trailing, and replay | `live_engine.py`, `live_dashboard.py` |
| Index intelligence | Option-chain snapshots, OI history, strike depth, jump candidates, and candles | `index_oi_services.py`, `views.py` |
| Trade journal | Broker-linked executions, open/close state, and journal text | `TradeExecution`, journal views |
| Stock data | Equity and option history, resumable downloads, coverage, stock detail | `stock_data.py`, `stock_views.py` |
| P&L analysis | Dhan, Zerodha, and Sahi imports and period summaries | `pnl_ingest.py`, broker models |
| Daily shortlist | Ranked stock candidates and separated backfill/live outcomes | `shortlist.py`, `shortlist_views.py` |
| Swing research | Strength-dip stock and option paper books | `swing*.py`, swing views |
| CAS | NIFTY closing auction capture, nowcast, and staff switch | `cas_nowcast.py`, `cas_views.py` |
| Research publication | Sixteen approved CSV tables rendered in production | `research_views.py`, `publish_research_tables` |
| Operations | Token renewal, feed collectors, health, reconciliation, and deployment synchronization | management commands, startup script, systemd |

## Runtime topology

### Azure App Service

App Service owns:

* Django migrations.
* Deployment commit publication after migrations.
* Web requests and static assets.
* Index OI collection.
* Daily shortlist building.
* Swing alert building.
* CAS collection.
* Optional Telegram tracking.

The startup script launches background processes beside Gunicorn. This works, but process health is not controlled by an independent scheduler and web restarts also restart these jobs.

### Static-IP Azure VM

The VM owns:

* Dhan access-token renewal.
* Live NIFTY execution.
* F&O paper radar supervision.
* Synchronization to the commit App Service successfully migrated.

This split is necessary because Dhan order placement requires a whitelisted static IP. Token renewal must have exactly one owner because renewing invalidates the previous account token globally.

### Shared state

Both hosts use the same PostgreSQL database. The database is also used as:

* Application persistence.
* Worker lease coordination.
* Deployment handoff through `AppSetting`.
* Token publication.
* Engine heartbeat and operational status storage.

This is pragmatic, but operational state and domain data are not clearly separated.

## Authentication and authorization audit

### Protected surfaces

* Dhan credential settings are staff-only.
* All radar pages and mutation endpoints are staff-only.
* CAS dashboard and status API are staff-only.
* Django admin is protected by standard Django authentication.

### Unprotected or conditionally protected surfaces

| Surface | Capability | Current boundary | Release 1 requirement |
|---|---|---|---|
| `/dhan-orders/` | Places a live Dhan Super Order | No visible login or staff decorator | Staff-only, explicit manual confirmation, audit actor |
| `/nifty-live/` | Saves risk settings and arms or disarms automatic execution | No visible login or staff decorator | Staff-only; default manual-only ARC 2.0 mode |
| `/options/` | Creates signals and can trigger legacy synchronization by GET | No visible login or staff decorator | Authenticated mutations; POST-only synchronization |
| `/options/<id>/edit/` | Changes signal and instrument fields | No visible login or staff decorator | Authenticated, authorized mutation |
| `/options/<id>/delete/` | Deletes a signal | No visible login or staff decorator | Authenticated, authorized mutation |
| `/scanners/` | Promotes scanner triggers into signals | No visible login or staff decorator | Authenticated mutation |
| `/telegram/` | Imports and creates messages/signals | No visible login or staff decorator | Staff-only or dedicated scoped API |
| `/index-oi/` | Changes collection interval | No visible login or staff decorator | Staff-only settings mutation |
| `/journal/` | Closes trades | No visible login or staff decorator | Authenticated mutation with actor audit |
| `/archive/?run=1` | Archives records from a GET request | No visible login or staff decorator | Staff-only POST |
| `/api/telegram/ingest/` | Writes Telegram messages and signals | Token is optional when not configured | Fail closed if token is absent |
| Status and ticker APIs | Expose engine and market state | Public | Decide and document intended audience |

CSRF middleware protects normal form POSTs. It does not replace authentication or authorization. The Telegram ingest endpoint is intentionally CSRF-exempt and therefore must always require a separate secret.

## Execution-policy mismatch

The paper radar is correctly paper-only. Its acknowledgement explicitly records that no broker order was placed.

The legacy execution surfaces are different:

* `/dhan-orders/` calls `place_super_order`.
* `live_engine.open_position` creates a signal and sends a Dhan Super Order unless dry-run is enabled.
* The NIFTY page can arm the engine through shared application settings.
* The VM service runs `run_nifty_live` continuously.

ARC 2.0 states that execution must remain manual. Release 1 must therefore define a compatibility policy before changing behavior:

1. Keep the existing engine intact behind an explicit legacy feature flag.
2. Make ARC 2.0 pages recommendation-only by default.
3. Require staff authentication and a deliberate confirmation for any live broker action.
4. Record the authenticated actor and immutable request/response evidence.
5. Do not silently disable the production engine during a documentation or navigation release.

## Data and identity audit

The system has multiple overlapping identity schemes:

* `RadarInstrument` uses `(segment, security_id)` and is the strongest canonical instrument record.
* `TipSignal` stores symbol, security ID, exchange segment, expiry, and direction directly.
* `TrackedStock` stores symbol and security ID independently.
* `IndexOptionStrikeSnapshot` stores security ID and contract attributes per snapshot.
* `StockOptionCandle` identifies contracts through symbol, relative strike or strike, option type, expiry code, interval, and timestamp.
* `SwingOptionTrade` repeats symbol, security ID, contract text, expiry, strike, and lot size.
* Broker P&L rows use parsed broker symbols and optional normalized fields.

This duplication is manageable today but creates reconciliation risk. The detailed analysis is in [database impact analysis](./04-database-impact-analysis.md).

## User experience audit

### Strengths

* Options and equities are present in desktop and mobile navigation.
* The shared shell uses the original light gray, white, and gold application theme.
* The radar has a stock-centric workspace, filters, charts, watchlists, price alerts, and health state.
* Large tracker lists are paginated.
* Expensive stock coverage aggregates are cached.

### Gaps

* Navigation exposes only part of the application. Shortlist, swing, OI, CAS, scanners, Telegram, recommendations, breakeven, and Dhan orders are discoverable mainly through direct routes or page links.
* The label “NIFTY Execution” makes automated execution prominent despite the new manual-only requirement.
* “Research & Archive” and “Signal Archive” overlap conceptually.
* Global shell counters query the database on every shared-shell request.
* Inline CSS in `dashboard_base.html` and page-level scripts make design-system changes expensive.
* Several templates contain large embedded scripts and chart data payloads.
* There is no consistent empty, stale, degraded, or provenance component across pages.

## Performance and reliability audit

### Existing safeguards

* Tracker pagination limits display work to 100 rows.
* Stock coverage aggregates are cached for 15 minutes.
* Gunicorn recycles workers with jitter.
* Dhan quote refresh has throttling and market-closed handling.
* Radar uses worker leases and stale-data checks.
* Research publication is comparison-based and explicit.
* Deployment synchronization follows the post-migration deployed commit rather than the branch head.

### Risks

* The shell context processor performs several counts and latest-row queries for every shared page.
* App Service background processes are tied to the web container lifecycle.
* Several pages build large Python lists before slicing or serializing.
* Market ticker requests can synchronously call Dhan on a cache miss.
* Broad exception handling exists at some external provider boundaries.
* Status is persisted through loosely typed JSON in `AppSetting`.
* Some pages intentionally fall back to stored or backfilled data, but the presentation contract is not uniform.

ARC 2.0 must preserve explicit stale and provenance labels. It must not turn a fallback into a success-shaped response.

## Documentation and operational debt

* The root `README.md` describes an earlier, smaller system.
* Runtime ownership is documented in the VM README but not in one application architecture source.
* Environment variables are spread across settings, commands, services, and startup shell logic.
* The command inventory is large and lacks a single ownership and schedule matrix.
* API payloads are implementation details rather than versioned contracts.
* Research reproduction depends on local paths, local caches, scientific dependencies, and manual publication.

## Release 0 conclusion

ARC 2.0 should modernize the current system incrementally. The first implementation release should secure the legacy control surfaces, enforce the manual-execution policy for new ARC 2.0 workflows, and introduce task-oriented navigation over existing features. Canonical data changes should follow only after identity mapping and backfill rehearsals are proven.
