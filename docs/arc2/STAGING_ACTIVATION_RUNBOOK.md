---
title: ARC 2.0 Staging Activation Runbook
description: Safe procedure for validating canonical identity, stock coverage, opportunities, and historical data in isolated staging
---

## Required staging state

Do not begin activation until all conditions pass:

| Check | Required value |
|---|---|
| Database | Isolated staging PostgreSQL |
| Django settings | `trading_terminal.settings_staging` |
| Execution mode | `preview` |
| Automatic entries | Disabled |
| Dhan token owner | `0` |
| Background workers | `0` |
| Deployed commit | Exact activation branch commit |
| Deployment health | HTTP 200 with the exact commit |

> [!CAUTION]
> Stop if staging points to the production database or if any execution worker
> can place broker orders.

## Phase 1: Application checks

```text
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py arc_activation_smoke
```

Provider freshness and VM worker checks can remain unhealthy in isolated
staging because workers are intentionally disabled. Route, asset, execution
policy, and deployment checks must pass.

## Phase 2: Dry runs

```text
python manage.py seed_reference_data --dry-run
python manage.py reconcile_instruments --kind=EQUITY --limit=500
python manage.py reconcile_instruments --kind=OPTION --limit=1000
python manage.py sync_opportunities
```

Record:

* Candidate and existing counts
* Scope and batch size
* Alias conflicts
* Parsed and unparsed opportunity counts
* Rows that would be created or preserved

## Phase 3: Reviewed stock universe

```text
python manage.py seed_reference_data --create-missing
```

This mode creates only missing reviewed stocks. It preserves broker-derived
turnover, realised P&L, contracts, broker names, and corrected local identity
fields.

Verify:

* 197 reviewed fixture stocks exist
* Existing production-derived fields were not overwritten
* Repeating the command creates no duplicates

## Phase 4: Canonical identity

Apply equities first:

```text
python manage.py reconcile_instruments --apply --kind=EQUITY --limit=500
```

Apply options in bounded waves:

```text
python manage.py reconcile_instruments --apply --kind=OPTION --limit=1000 --batches=10
```

Each batch has its own transaction. If an operational request times out,
inspect committed counts before retrying. Resume without a cursor to process
the lowest remaining unresolved rows, or use the reported `next_after_pk` when
continuing the same ordered wave.

Activation passes when:

* 218 radar equities are mapped
* Required option contracts are mapped
* Radar and Dhan security aliases are unique
* Error reports are empty or reviewed
* `remaining` reaches the approved target

## Phase 5: Canonical opportunities

```text
python manage.py sync_opportunities --apply
```

Do not pass `--include-unparsed`.

Verify:

* Parsed tips and active radar setups are represented
* Repeating the command is idempotent
* Evidence changes append revisions
* Unparsed Telegram messages remain excluded
* No opportunity bypasses lifecycle controls

## Phase 6: Historical data pilot

Keep the Dhan token on its designated VM. Capture a credential-free fixture:

```text
python manage.py capture_stock_history_fixture ^
  C:\arc-transfer\dhan-history-5x10.json ^
  --days=10 ^
  --limit=5 ^
  --interval=15
```

Validate the transferred fixture in staging without changing the database:

```text
python manage.py import_stock_history_fixture ^
  C:\arc-transfer\dhan-history-5x10.json
```

Apply it only after the hash and row counts are reviewed:

```text
python manage.py import_stock_history_fixture ^
  C:\arc-transfer\dhan-history-5x10.json ^
  --apply
```

Run a limited pilot before a full backfill:

1. Refresh the Dhan master.
2. Select five reviewed stocks.
3. Download ten trading days of supported source intervals.
4. Verify source and receipt timestamps.
5. Check duplicate prevention and missing-session reporting.
6. Build one scanner and Stock Intelligence read model from persisted bars.

Do not derive 3-minute or 30-minute bars unless complete lower-interval source
bars exist.

Follow [Historical Data Transfer](./HISTORICAL_DATA_TRANSFER.md) for credential
boundaries, validation rules, idempotency checks, and acceptance evidence.

## Phase 7: Browser and performance checks

Validate authenticated routes:

* Command Centre
* Action List
* Options and Equities
* Opportunity Inbox
* Trading Workspace
* Stock Intelligence
* Scanner Builder
* F&O Research
* Trade Journal
* Investment Desk
* Strategy Lab
* Performance
* Operations Health
* Release Dashboard

Record response times, database query counts, browser errors, layout overflow,
and data freshness labels. Do not accept a rendered empty page as functional
evidence.

## Phase 8: Exit criteria

Staging is ready for production review only when:

* Automated validation passes at the exact commit
* Deployment health reports the exact commit
* Database migrations are current
* Reviewed activation commands are idempotent
* Required staging tables contain attributable records
* Safety mode remains preview
* Provider failures are explicit
* Browser and performance evidence is recorded
* A production backup is planned
* The operator grants explicit production approval
