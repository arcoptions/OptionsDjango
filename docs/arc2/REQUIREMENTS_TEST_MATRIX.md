---
title: ARC 2.0 Requirements-to-Tests Matrix
description: Named automated, browser, staging, and live-evidence coverage for ARC 2.0 release requirements
---

## Evidence policy

A rendered page is not sufficient evidence that a workflow is functional.
Every implemented requirement maps to named automated coverage. Workflows that
depend on provider data, market-session behavior, or browser interaction also
identify the required non-unit evidence.

The current automated baseline is 593 non-browser tests. The matrix records
coverage for implemented behavior, not completion of unavailable provider
feeds.

## Safety and platform requirements

| Requirement | Named automated evidence | Non-unit evidence | State |
|---|---|---|---|
| Authenticated application routes | `LegacyMutationAccessTests.test_read_only_application_pages_require_an_authenticated_session` | Authenticated staging smoke | Passed |
| Staff-only mutations | `LegacyMutationAccessTests.test_anonymous_and_non_staff_users_cannot_mutate_legacy_routes` | Staff staging session | Passed |
| CSRF and POST mutation controls | `LegacyMutationAccessTests.test_staff_mutations_reject_missing_csrf_token`, `test_scanner_sync_requires_post_and_staff`, `test_archive_run_requires_post_and_staff` | Live missing-CSRF probe returned HTTP 403; external penetration evidence remains open | Passed tested scope |
| Manual-first execution | `test_manual_execution.py` and execution-mode tests in `tests.py` | Production manual mode verified | Passed |
| Preview blocks broker mutation | `test_manual_execution.py` and `test_activation_smoke.py` | Staging reports preview mode | Passed |
| Legacy automatic mode requires opt-in | `ActivationSmokeTests.test_legacy_auto_requires_explicit_smoke_override` | No automatic activation performed | Passed |
| Staging cannot own the token or workers | `test_staging_settings.py` | Staging settings and environment verified | Passed |
| Versioned polling API contracts | `PollingApiContractTests` | Browser console showed no contract errors on tested routes | Passed |
| Rate-limited integration intake | `test_rate_limit.py` | Shared-cache distributed limiting remains open | Passed implemented scope |
| Deployment health reports exact commit | Deployment-health tests in `tests.py` | Staging reported exact commit `1c3abee` after deployment handoff | Passed |
| Live HTTP security controls | Access, method, CSRF, and `BrowserSecurityHeaderTests` | Protected routes, HTTPS redirect, sensitive paths, TRACE, CSRF, nonce-based CSP, Permissions-Policy, COEP, and CORP probed on staging; independent penetration evidence remains open | Passed bounded scope |
| Passive staging DAST | Immutable official ZAP action and high-or-medium enforcement in `dast-activation.yml` | Exact-commit run `42691024543` reported zero high and zero medium findings | Passed |

## Research and decision requirements

| Requirement | Named automated evidence | Non-unit evidence | State |
|---|---|---|---|
| Command Centre ranks current evidence | `CommandCentreTests` | Command Centre browser journey remains to be recorded for this commit | Passed automated scope |
| Five-state market session context | `CommandCentreTests.test_market_session_context_classifies_five_windows`, `test_market_session_context_closes_weekends_and_verified_holidays` | Approved exchange-holiday feed remains open; weekdays without attributed holiday evidence are labeled schedule-only | Passed implemented scope |
| Fresh-universe breadth detail | `CommandCentreTests.test_breadth_reports_coverage_gaps_distribution_and_industries` | EMA, RSI, average volume, 52-week history, and named-universe membership remain explicitly unavailable pending complete evidence | Passed implemented scope |
| Market context fails unavailable | `test_market_context.py` | Approved market-context provider remains open | Passed implemented scope |
| Opportunity identity and revisions | `test_opportunities.py` | 152 tip opportunities plus five canonical scanner opportunities; scanner rerun retained one revision each | Passed |
| Canonical instrument reconciliation | `test_instrument_identity.py` | 74,917 staging mappings and zero unmapped radar instruments | Passed |
| Unparsed Telegram messages require opt-in | `test_opportunities.py` | 2,147 staging messages remain excluded | Passed |
| Telegram tip parsing | `TelegramTipParserTests` in `tests.py` | Live source monitoring remains required | Passed implemented scope |
| Stock Intelligence evidence and freshness | `test_stock_intelligence.py` | Stock Intelligence browser smoke passed; provider datasets remain open | Partial activation |
| Five-stock historical pilot bounds | `DownloadStockDataCommandTests.test_exact_day_lookback_bounds_equity_pilot` | Approved live capture remains open | Passed automated scope |
| Credential-confined history transfer | `HistoryFixtureImportTests` | Five-stock VM capture and 860-row staging import passed with matching hash | Passed equity scope |
| Scanner conditions, cooldown, and matches | `test_scanner_engine.py`, including persisted-bar coverage | Two five-symbol staging runs passed with deduplication and historical alerts suppressed | Passed |
| F&O recommendation evidence | `test_fno_desk.py` | Approved fundamentals, event, and news providers remain open | Partial activation |
| Investment thesis lifecycle | `test_investment_desk.py` | Investment Desk browser journey remains to be recorded for this commit | Passed automated scope |
| AI briefing provenance and dry run | `AIBriefingTests` | Approved Azure OpenAI provider remains open | Passed implemented scope |
| AI research access and traceability | `AIResearchTests` | Approved Azure OpenAI provider remains open | Passed implemented scope |

## Trading and performance requirements

| Requirement | Named automated evidence | Non-unit evidence | State |
|---|---|---|---|
| Trading Workspace manual controls | `test_manual_execution.py`, `test_radar_workspace.py` | Trading Workspace repeat loads passed at 1.379 to 2.968 seconds | Passed |
| Radar readiness and quote freshness | `test_radar_readiness.py`, `test_radar.py` | Production market-session monitoring remains required | Passed implemented scope |
| Immutable radar history | `test_radar_history.py` | Production retention monitoring remains required | Passed |
| NIFTY live collector safety | `test_cas_nowcast.py` | VM worker lease and market-session freshness remain required | Passed implemented scope |
| Trade journal lifecycle | `test_trade_journal.py` | Trade Journal browser journey remains to be recorded for this commit | Passed automated scope |
| Broker P&L analytics | `test_performance_analytics.py` | Approved broker statement import remains open | Partial activation |
| Strategy backtest chronology and costs | `test_strategy_backtest.py`, `test_dynamic_strategy.py` | Five immutable staging manifests used 172 real bars each; independent hashes and results matched, and chronological validation completed | Passed staging scope |
| Strategy Lab storage and access | `test_strategy_lab.py`, including exact packaged-commit precedence | Five definitions and manifests persisted on exact commit `09561ac`; no strategy was promoted | Passed staging scope |
| Performance budgets | `test_performance_budgets.py` | Quiet staging timings passed for Release Dashboard, Opportunity Inbox, and Trading Workspace | Passed tested routes |

## Operations and release requirements

| Requirement | Named automated evidence | Non-unit evidence | State |
|---|---|---|---|
| Operations Health evidence | `test_operations_health.py` | Authenticated staging route rendered | Passed |
| Release Dashboard authorization | `ReleaseDashboardTests.test_release_dashboard_requires_staff` | Authenticated staging route rendered | Passed |
| Release and gate counts | `ReleaseDashboardTests.test_snapshot_counts_release_and_gate_states` | Dashboard now separates prior production baseline from activation gates | Passed |
| Persisted activation counts | `ReleaseDashboardTests.test_snapshot_reports_persisted_activation_evidence` | Staging database counts verified | Passed |
| Activation smoke fails closed | `ActivationSmokeTests.test_unhealthy_activation_report_fails_closed` | Final production smoke remains open | Passed implemented scope |
| Navigation exposes Options and Equities | `test_options_navigation.py` | Authenticated browser navigation previously verified | Passed |

## Browser coverage status

Authenticated staging evidence covers all 20 left-navigation routes on commit
`cd11d4a`. Every route returned HTTP 200. No console errors, page errors, CSP
violations, or horizontal overflow were observed. Action List rendered exactly
one level-one heading. The complete 605-test local run on nonce-policy commit
`1c3abee` included all 15 browser-tagged tests and passed.

## Gate interpretation

The requirements-to-tests mapping gate is complete for implemented behavior.
This does not promote partial provider workflows to complete. Provider,
market-session, browser, restore, penetration, and production-deployment
evidence remain separate release gates.
