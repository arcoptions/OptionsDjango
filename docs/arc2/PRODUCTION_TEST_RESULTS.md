---
title: ARC 2.0 Production Activation Test Results
description: Automated, staging, browser, data, safety, and performance evidence for the production activation branch
---

## Result summary

The activation branch is code-validated and staging-activated. It is not
approved or deployed to production.

| Area | Result | Evidence |
|---|---|---|
| Application commit | Passed | `c55c9fb3217f1802be9751bc2c49b9a0f24cd093` |
| GitHub Actions | Passed | Runs `42663557008` and `42663576656` |
| Django tests | Passed | 586 non-browser tests in 106.100 seconds |
| Dependency audit | Passed | `pip-audit` in run `42623063766` |
| Static security scan | Passed | Bandit medium-and-higher gate in run `42623063766` |
| Django checks | Passed | No issues |
| Migration drift | Passed | No changes detected |
| Static collection | Passed | Production collection completed |
| Staging safety | Passed | Preview mode, token owner off, background workers off |
| Staging deployment | Passed for tested commit | Deployment `c7a2084f`; commit `c55c9fb` reported through deployment health |
| Canonical activation | Passed | 74,917 instruments and 149,834 aliases |
| Opportunity activation | Passed with identity gap | 152 opportunities and 152 revisions; 40 canonical links |
| Stock universe | Passed | 197 reviewed rows and 195 active rows |
| Dhan master | Passed with alias fix | 194 direct matches and one verified provider rename |
| Stock history | Equity pilot passed | 860 real candles for five stocks; option endpoint remains blocked by Dhan `DH-905` |
| Safe history transfer | Passed | Matching SHA-256, dry run, atomic apply, and zero-row idempotency rerun |
| Browser smoke | Passed | Nineteen authenticated routes rendered without console or overflow errors |
| Production backup | Passed for backup existence | 14-day retention and 11 visible automatic full backups |
| Restore rehearsal | Not run | Temporary billable server requires explicit approval |
| Production activation | Not run | Explicit operator approval and backup evidence required |

## Automated validation

The branch-only workflow performs:

```text
python manage.py makemigrations --check --dry-run
python manage.py check
python manage.py collectstatic --noinput
python manage.py test --exclude-tag=browser
```

Runs `42663557008` and `42663576656` completed successfully against commit
`c55c9fb`. They audited production requirements with `pip-audit`, scanned
application source with Bandit at medium-and-higher severity, and repeated the
complete application validation.

The current activation branch passed 586 non-browser tests locally after the
credential-confined history capture and scanner synchronization workflow was
added.

Route regression coverage verifies:

* Anonymous and non-staff users cannot mutate legacy routes
* Read-only application pages require authentication
* Staff mutations reject missing CSRF tokens
* Scanner synchronization and archiving reject GET mutations
* NIFTY engine status remains staff-only

Named requirements coverage is recorded in
[Requirements-to-Tests Matrix](./REQUIREMENTS_TEST_MATRIX.md). The mapping
keeps unavailable provider, browser, market-session, restore, and production
evidence separate from automated implementation coverage.

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

Applied baseline evidence:

* Tip-derived opportunities: 152
* Tip-derived revisions: 152
* Canonically linked tip opportunities: 40
* Unparsed Telegram opportunities: 0

Persisted-bar scanner activation added five canonically linked configurable
scanner opportunities. Repeating synchronization retained one revision for
each scanner opportunity, bringing the unified total to 157. The fingerprint
remained stable while newly resolvable canonical identities were attached.

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

Authenticated staging checks covered all 19 left-navigation routes. Every
route returned HTTP 200 without JavaScript console errors, page errors, or
horizontal overflow.

| Route | Result | Observed browser load | Measurement state |
|---|---|---:|---|
| Action List | Rendered | One level-one heading | Regression check |
| Trading Workspace | Rendered | 1.611 seconds | Full sweep |
| Trading Workspace | Rendered | 2.968, 1.382, and 1.379 seconds | Repeat loads |
| F&O Research | Rendered | 1.201 seconds | Full sweep |
| Command Centre | Rendered | 0.929 seconds | Full sweep |

No JavaScript console errors or horizontal overflow were observed.

Three defects were fixed:

* Release Dashboard now reports 195 active stocks, matching Stock Data.
* Opportunity Inbox no longer repeats its page title.
* Action List no longer repeats its level-one heading.

Trading Workspace previously required 2.864 to 6.442 seconds. Reusing the
Action List and CAS context reduced the tested repeat range to 1.379 to 2.968
seconds.

## Historical-data pilot

The production VM retained exclusive Dhan-token ownership. A read-only capture
produced a credential-free fixture for RELIANCE, HDFCBANK, ICICIBANK, INFY,
and TCS.

* Source and staging SHA-256:
  `ef248f5eaa51e0af534c4b711f45ba35f4992050067e9f04b8ed48235fb6b58d`
* Equity rows captured and imported: 860
* Rows inserted on the idempotency rerun: 0
* Option rows: 0

Dhan accepted the equity-history requests but returned `DH-905` for every
documented rolling-option payload tested, including index and stock
instruments. The equity-only fallback preserves real scanner evidence without
claiming that option history is active.

## Persisted-bar scanner activation

The staging scanner evaluated the imported bars twice:

* Five symbols scanned on each run
* Five confirmed matches on each run
* Provider evidence recorded as `DHAN_HISTORY`
* Match identities remained deduplicated
* Cooldown timestamps remained unchanged on the immediate rerun
* Historical notification delivery remained suppressed

Confirmed configurable scanner matches now synchronize into canonical
opportunities with stable source references. Repeated synchronization remains
idempotent.

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
| Historical pilot required token exposure in staging | Credential-confined capture and sanitized fixture import |
| Production had no tracked-stock rows for bounded capture | Explicit reviewed `SYMBOL:SECURITY_ID` inputs |
| Dhan rolling-option history rejected documented payloads | Explicit equity-only fallback; option gate remains open |
| Scanners could not consume imported stock candles | History-backed scanner source with notifications suppressed |
| First post-deploy management connection timed out | Verified PostgreSQL `Ready`, retried with bounded connection recovery, and completed synchronization |
| Release Dashboard implied empty workflows were complete | Live database activation evidence |
| Release gates mixed the prior production baseline with current activation | Activation-specific pending states |
| Stock counts disagreed | Active-only dashboard count |
| Opportunity Inbox repeated its heading | Removed duplicate heading |

## Open test gates

* Resolve Dhan rolling-option `DH-905` responses and complete option history.
* Reproduce strategy results from immutable manifests.
* Complete external penetration evidence.
* Restore the production point-in-time backup to a temporary server and
  rehearse the release migrations.
* Obtain explicit production approval.
