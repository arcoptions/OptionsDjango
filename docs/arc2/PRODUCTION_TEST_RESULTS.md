---
title: ARC 2.0 Production Activation Test Results
description: Automated, staging, browser, data, safety, and performance evidence for the production activation branch
---

## Result summary

The activation branch is code-validated and staging-activated. It is not
approved or deployed to production.

| Area | Result | Evidence |
|---|---|---|
| Application commit | Passed | `09561ac2a05929f62955b89da734d6909263f275` |
| GitHub Actions | Passed | Reruns `42673171903` and `42673181295` passed on exact commit `09561ac` |
| Django tests | Passed | 589 non-browser tests in 107.696 seconds |
| Dependency audit | Passed | `pip-audit` in run `42623063766` |
| Static security scan | Passed | Bandit medium-and-higher gate in run `42623063766` |
| Django checks | Passed | No issues |
| Migration drift | Passed | No changes detected |
| Static collection | Passed | Production collection completed |
| Staging safety | Passed | Preview mode, token owner off, background workers off |
| Staging deployment | Passed for tested commit | Deployment `a4b8e0bb-443d-4fd6-bdb3-2998217f9dd3`; exact commit `09561ac` reported through deployment health |
| Strategy Lab activation | Passed in staging | Five immutable manifests reproduced independently from 860 real Dhan-equity candles |
| Live security probes | Passed bounded scope | Access, CSRF, HTTPS, TRACE, sensitive-path, and response-header checks passed; independent penetration evidence remains open |
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

Reruns `42673171903` and `42673181295` passed against exact commit `09561ac`.
Their first attempts ended before creating jobs or logs because of a transient
GitHub workflow-start failure. The successful reruns passed the production
dependency audit, Bandit medium-and-higher scan, migration check, Django
checks, static collection, and complete non-browser suite.

The current activation branch passed 589 non-browser tests after the
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

## Strategy Lab activation

Staging now contains one bounded 15-minute momentum definition for each equity
in the Dhan history pilot. Each immutable manifest used 172 stored candles,
the realistic configured cost model, and exact package commit
`09561ac2a05929f62955b89da734d6909263f275`.

| Symbol | Trades | Expectancy | Validation expectancy | Manifest SHA-256 |
|---|---:|---:|---:|---|
| HDFCBANK | 32 | 0.0361% | -0.3323% | `d7b01d6acb66bf5689a7ad3f6c9009e7fedef5f645ef66c66c70d9ef32af8d9c` |
| ICICIBANK | 31 | 0.1280% | 0.0464% | `105c89f68f4cce6457e52a410c86261cf4cc2c05ac07c1c86b6d017fb46d5262` |
| INFY | 20 | -0.3805% | -0.6469% | `c15c01fe9b658744419921597755d18c3a28a1ddf366a4b893c72e2bd5fb6f32` |
| RELIANCE | 26 | 0.1221% | -0.3013% | `dcde3751928eff01efeb84734c48e163492371d7469f00f1ef026bcf92c38df2` |
| TCS | 17 | -0.3056% | -0.6531% | `20943d54a271f179f705852d1bfbfe2a8dcb38f6adb371cdc6f836c57556d8a3` |

For every manifest, an independent query recomputed the candle SHA-256 and
backtest result. Both matched the stored evidence. Recreating the same manifest
returned the existing identity rather than inserting a duplicate. All five
70/30 chronological validations completed. The results are intentionally not
promoted: only ICICIBANK had positive validation expectancy, and no live or
paper execution was enabled.

## Live security validation

Bounded unauthenticated probes against staging verified:

* Seven protected application routes redirected to the login page
* Missing-CSRF login submission returned HTTP 403
* Public endpoints rejected TRACE with HTTP 405
* HTTP redirected to HTTPS with HTTP 301
* `.env`, `.git/config`, `DEPLOYED_SHA`, debug, and server-status paths returned
  HTTP 404
* HSTS, `nosniff`, same-origin referrer policy, and frame denial headers were
  present

The browser resource inventory found no third-party executable assets.
Enforced Content-Security-Policy now restricts active content to the
application origin while temporarily allowing the existing inline scripts and
styles. Permissions-Policy disables camera, geolocation, microphone, payment,
and USB access. Independent external penetration evidence remains a separate
release gate.

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
| Strategy manifests trusted a stale App Service commit setting | Exact packaged commit now takes precedence, with stale-setting regression coverage |
| CSP and Permissions-Policy headers were absent | Origin-restricted CSP and disabled browser capabilities, with response-header regression coverage |
| Release Dashboard implied empty workflows were complete | Live database activation evidence |
| Release gates mixed the prior production baseline with current activation | Activation-specific pending states |
| Stock counts disagreed | Active-only dashboard count |
| Opportunity Inbox repeated its heading | Removed duplicate heading |

## Open test gates

* Resolve Dhan rolling-option `DH-905` responses and complete option history.
* Complete independent external penetration evidence.
* Restore the production point-in-time backup to a temporary server and
  rehearse the release migrations.
* Obtain explicit production approval.
