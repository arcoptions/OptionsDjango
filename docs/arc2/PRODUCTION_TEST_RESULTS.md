---
title: ARC 2.0 Production Activation Test Results
description: Automated, staging, browser, data, safety, and performance evidence for the production activation branch
---

## Result summary

The activation branch is code-validated and staging-activated. It is not
approved or deployed to production.

| Area | Result | Evidence |
|---|---|---|
| Application commit | Passed | `67cfa95eef47f4ada1359311b8a83ef9117761c8` |
| GitHub Actions | Passed | Run `42621150014` |
| Django tests | Passed | 571 non-browser tests in 61.388 seconds |
| Django checks | Passed | No issues |
| Migration drift | Passed | No changes detected |
| Static collection | Passed | Production collection completed |
| Staging safety | Passed | Preview mode, token owner off, background workers off |
| Staging deployment | Passed for tested commit | Commit `67cfa95` reported through deployment health |
| Canonical activation | Passed | 74,917 instruments and 149,834 aliases |
| Opportunity activation | Passed with identity gap | 152 opportunities and 152 revisions; 40 canonical links |
| Stock universe | Passed | 197 reviewed rows and 195 active rows |
| Dhan master | Passed with alias fix | 194 direct matches and one verified provider rename |
| Stock history | Blocked in staging | No trading-capable Dhan token copied into staging |
| Browser smoke | Passed with fixes | Five authenticated critical routes rendered without console or overflow errors |
| Production activation | Not run | Explicit operator approval and backup evidence required |

## Automated validation

The branch-only workflow performs:

```text
python manage.py makemigrations --check --dry-run
python manage.py check
python manage.py collectstatic --noinput
python manage.py test --exclude-tag=browser
```

Run `42621150014` completed successfully against commit `67cfa95`.

## Staging safety

The isolated App Service reported:

* `trading_terminal.settings_staging`
* `ARC_EXECUTION_MODE=preview`
* `DHAN_TOKEN_OWNER=0`
* `ARC_BACKGROUND_WORKERS=0`
* `ARC_SCANNER_WORKER=0`
* `ARC_AI_BRIEFING_WORKER=0`
* Staging PostgreSQL rather than production PostgreSQL

No broker order was placed.

## Canonical identity activation

### Equity wave

* Scoped radar equities: 218
* Applied: 218
* Errors: 0
* Remaining: 0

### Option wave

* Scoped radar options: 74,699
* Applied: 74,699
* Errors: 0
* Remaining: 0

The initial row-by-row path was too slow and exceeded the Kudu request window.
The command was replaced with conflict-checked bulk operations while retaining
bounded transactions. The complete staging activation then finished without
alias conflicts.

Final identity evidence:

* Canonical instruments: 74,917
* Provider aliases: 149,834
* Mapped radar instruments: 74,917
* Unmapped radar instruments: 0

## Opportunity activation

Dry-run candidates:

* Parsed tips: 152
* Active radar opportunities: 0
* Scanner triggers: 0
* Unparsed Telegram messages: 2,147

Applied evidence:

* Unified opportunities: 152
* Revisions: 152
* Canonically linked opportunities: 40
* Unparsed Telegram opportunities: 0

Repeating synchronization kept 152 opportunities and 152 revisions. The
fingerprint remained stable while newly resolvable canonical identities were
attached.

The remaining 112 parsed tips do not contain a resolvable canonical exchange
and security identity. They remain visible with source evidence rather than a
fabricated contract.

## Stock universe and Dhan master

The reviewed fixture created 197 rows, of which 195 are active.

The current public Dhan master matched 194 active symbols directly. One
historical broker symbol, `ICICIPRULI`, is now published by Dhan as
`ICICILIFE`. Security ID `18652` is unchanged. An explicit provider alias now
preserves the broker-history symbol while refreshing current Dhan metadata.

## Browser validation

Authenticated staging checks covered:

| Route | Result | Observed browser load | Measurement state |
|---|---|---:|---|
| Release Dashboard | Rendered | 0.56 seconds | Quiet database |
| Operations Health | Rendered | 1.3 seconds | Initial smoke |
| Opportunity Inbox | Rendered | 0.35 seconds | Quiet database |
| Stock Intelligence/Data | Rendered | 2.2 seconds | Initial smoke |
| Trading Workspace | Rendered | 2.40 seconds | Quiet database |

No JavaScript console errors or horizontal overflow were observed.

Two clarity defects were fixed:

* Release Dashboard now reports 195 active stocks, matching Stock Data.
* Opportunity Inbox no longer repeats its page title.

The first smoke ran during tens of thousands of identity writes and produced
13-second outliers. The final quiet-database retest reduced the Release
Dashboard to 0.56 seconds, Opportunity Inbox to 0.35 seconds, and Trading
Workspace to 2.40 seconds.

## Bugs found and fixed

| Defect | Resolution |
|---|---|
| Radar option aliases collided by underlying | Contract-specific aliases |
| Reconciliation used one large transaction | Bounded multi-batch transactions |
| Row-by-row reconciliation was too slow | Conflict-checked bulk operations |
| Unparsed messages flooded opportunity candidates | Explicit opt-in |
| Existing stock fixture values could be overwritten | Create-missing mode |
| Ten-day pilot could not be expressed | Exact `--days` lookback |
| Dhan provider renamed `ICICIPRULI` | Explicit `ICICILIFE` provider alias |
| Release Dashboard implied empty workflows were complete | Live database activation evidence |
| Stock counts disagreed | Active-only dashboard count |
| Opportunity Inbox repeated its heading | Removed duplicate heading |

## Open test gates

* Run the five-stock historical pilot when a staging-safe read-only Dhan
  credential is approved.
* Activate and test one scanner from persisted stock bars.
* Reproduce strategy results from immutable manifests.
* Complete dependency, route-security, and penetration evidence.
* Rehearse production backup and restore for the activation release.
* Obtain explicit production approval.
