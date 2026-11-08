---
title: ARC 2.0 API
description: Maintained inventory of current HTTP endpoints, payload roles, authentication, and target API policy
---

## API status

ARC does not expose a public REST API. It has server-rendered pages plus JSON
helper endpoints used by polling, ingestion, and radar interactions. Browser
polling responses carry `api_version` and `X-ARC-API-Version`.

## JSON endpoints

| Method | Path | Purpose | Current protection |
|---|---|---|---|
| POST | `/api/telegram/ingest/` | Ingest Telegram webhook or custom messages | CSRF-exempt; configured scoped secret required; missing secret fails closed |
| GET | `/api/telegram/status/` | Telegram tracker diagnostics and counts | Diagnostics header token required |
| POST | `/api/options/live/` | Refresh tracked option/equity prices and return rows/counts | Staff-only and CSRF |
| GET | `/api/market-ticker/` | Return NIFTY price, change, source, timestamp, stale state | Authenticated session |
| GET | `/api/nifty-live/status/` | Return legacy engine heartbeat, execution policy, position, rejections, and P&L | Staff-only |
| GET | `/api/nifty-cas/status/` | Return CAS enabled, status, nowcast, stale state, and counts | Staff-only |
| GET | `/radar/data/` | Return live stock-centric radar workspace | Staff-only |
| POST | `/radar/preferences/` | Save per-user workspace preferences | Staff-only and CSRF |
| POST | `/radar/watch/` | Add, remove, or edit watch entries | Staff-only and CSRF |
| POST | `/radar/price-alert/` | Create or cancel personal alerts | Staff-only and CSRF |

## Server-rendered mutation routes

| Method | Path | Mutation | Current protection |
|---|---|---|---|
| POST | `/settings/dhan/` | Save validated Dhan credentials | Staff-only |
| POST | `/options/` | Create manual signal or run legacy Telegram synchronization | Staff-only and CSRF |
| POST | `/options/<id>/edit/` | Edit signal | Staff-only and CSRF |
| POST | `/options/<id>/delete/` | Delete signal | Staff-only and CSRF |
| POST | `/scanners/` | Synchronize legacy triggers or promote scanner trigger | Staff-only and CSRF |
| POST | `/telegram/` | Synchronize or import messages | Staff-only and CSRF |
| POST | `/index-oi/` | Change collector interval | Staff-only and CSRF |
| POST | `/dhan-orders/` | Review and explicitly confirm a live Dhan Super Order | Staff-only, CSRF, signed expiring review |
| POST | `/journal/` | Close trade | Staff-only and CSRF |
| POST | `/archive/` | Archive records | Staff-only and CSRF |
| POST | `/nifty-live/` | Save settings or arm/disarm engine | Staff-only and CSRF |
| POST | `/nifty-cas/` | Start or stop CAS collector | Staff-only |
| POST | `/radar/<id>/acknowledge/` | Take or skip paper opportunity | Staff-only |

## Important response contracts

### Market ticker

Successful fields:

* `ok`
* `symbol`
* `price`
* `change`
* `change_percent`
* `updated_at`
* `stale`
* `source`
* `change_source`

The endpoint can use a live Dhan LTP with change derived from a stored OI snapshot. Consumers must read both source fields.

### NIFTY live status

Fields include:

* Engine state and dry-run mode.
* Execution mode, automatic-entry permission, and live position-management state.
* Visible configuration errors for invalid execution policy.
* Stale and heartbeat age.
* Notes and rejection reasons.
* Session and current position.
* Trade count and realized R.
* Net P&L and observed trade count.

This endpoint exposes operational and position state and is staff-only in the
current local Release 1 work.

### Manual Dhan order

The current local Release 1 flow:

1. Requires a staff session.
2. Validates the selected signal and risk guard.
3. Displays environment, masked broker account, instrument, quantity, entry,
   stop, target, deployed capital, and maximum stop loss.
4. Signs the reviewed terms for 10 minutes.
5. Rejects confirmation if terms or broker account changed.
6. Requires an explicit live-order checkbox.
7. Records the staff actor before calling Dhan.

This change is tested locally and not yet deployed.

### Radar workspace

The radar workspace is a large read model containing:

* Universe and visible rows.
* Selected stock and charts.
* Strategy opportunities.
* Quotes and option readiness.
* Watches and personal alerts.
* Market context.
* Portfolio and worker health.

The workspace payload uses the shared browser polling contract version.

## ARC 2.0 API policy

* Browser APIs require session authentication by default.
* Mutations require CSRF.
* External ingestion uses a scoped secret and fails closed.
* Staff permission is required for credentials, execution, and operations.
* Errors use non-2xx status codes and explicit error fields.
* Responses expose provider, source time, receipt time, and stale state.
* Live, stored, repaired, backfilled, and modeled data are distinguishable.
* Broker actions require idempotency or correlation IDs.
* New payloads are versioned before multiple clients depend on them.

## Release 1 API changes

The first release should change access policy, not payload shape:

Current local progress:

1. Browser application routes require an authenticated session.
2. Known unsafe legacy methods require staff.
3. Telegram ingestion fails closed when no scoped secret is configured.
4. Engine and position status requires staff.
5. Explicit legacy synchronization and archive actions use POST.
6. A route access matrix covers anonymous, non-staff, staff, CSRF, and
   method behavior.
7. `/operations/` consolidates secret-free deployment, provider, worker,
   execution-policy, and engine health for staff.

Read requests no longer archive expired signals as a side effect. Active views
filter expired contracts, Archive includes them without mutation, and actual
status updates remain on protected POST or quote-refresh paths.

Browser polling payload versioning and canonical instrument references are
implemented locally. External ingestion retains its scoped integration contract.
