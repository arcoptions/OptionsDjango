---
title: ARC 2.0 Provider Integration Matrix
description: Production provider capabilities, credentials, freshness, verification evidence, and activation blockers
---

## Provider acceptance rule

A provider is production-ready only when ARC can distinguish authentication
failure, rate limiting, transport failure, stale data, and valid empty data.
Every observation must retain provider identity, instrument identity, source
timestamp, receipt timestamp, and freshness.

No fallback is labeled live. No unavailable provider produces fabricated data.

## Current provider matrix

| Provider | Capability | Code state | Production state | Credentials | Freshness and failure policy | Required action |
|---|---|---|---|---|---|---|
| Dhan REST | Authentication and profile validation | Integrated | Read-only token check passed | VM-managed client ID and token | Authentication errors are distinct from empty data | Retain VM token ownership |
| Dhan REST | LTP, OHLC, quote, and market depth | Integrated | Used by legacy and index workflows | Configured | Bounded requests; stale values are not accepted as live | Consolidate quote contracts across legacy and radar paths |
| Dhan WebSocket | Tick, quote, depth, OI, subscriptions, reconnects | Integrated | Live radar collector verified | Configured | Packet liveness is separate from quote freshness; reconnect mode is preserved | Add browser server-push channel only after measuring polling load |
| Dhan instrument list | NSE, BSE, derivatives, identifiers, contract metadata | Integrated | Radar registry populated with 74,917 instruments | Public CSV; no secret | Cached for 12 hours; identity conflicts fail closed | Activate canonical reconciliation and full universe status |
| Dhan historical data | Daily and intraday candles | Integrated | Radar bars populated; stock-history tables empty | Configured | Resumable windows and explicit failed jobs | Seed tracked stocks and run bounded backfill |
| Dhan rolling option history | Expired index and stock option series | Integrated | Index and research history populated; stock option table empty | Configured | Fifteen-day request windows avoid observed gateway timeouts | Activate selected stock-option backfill |
| Dhan option chain | Expiries, OI, Greeks, IV, volume, bid, ask, and strikes | Integrated | 9.7 million strike snapshots and 692,868 option candles | Configured | Requests must obey the provider's unique-request interval | Cross-check representative expiries and persist readiness evidence |
| Dhan broker records | Orders and trades | Integrated | 6,147 order events; two trade records | Configured; static-IP restrictions apply to mutations | Reconciliation is read-only; execution remains manual | Import statements, trades, charges, and broker totals |
| Telegram Bot API | Private alerts and operational messages | Integrated | Alert delivery path active | Configured where enabled | Errors are persisted with bounded retry | Add provider performance reporting |
| Telethon | Channel ingestion | Integrated | 276 parsed tips and 2,150 unlinked tip-category messages | Optional session credentials | Missing private session falls back only to public advisory collection | Review source configuration and parsing backlog |
| Telegram public pages | Public advisory news | Integrated | Advisory worker active | None | Failure is advisory and never gates core radar | Retain as non-authoritative context |
| NSE reference files | Constituents and sector metadata | Integrated | Sector refresh command exists | None | Existing mappings are retained when source is unavailable | Schedule refresh and expose unmatched count |
| Broker statements | Dhan, Zerodha, and Sahi P&L files | Integrated | Production broker P&L tables empty | User-supplied files | Duplicate detection and normalized rows | Obtain and import current statements |
| Global markets | Global index evidence | Provider-neutral backend | Unavailable | Provider not selected | Fails unavailable | Select a licensed provider |
| Macro data | Rates, currency, commodities, and calendar | Provider-neutral backend | Unavailable | Provider not selected | Fails unavailable | Select a licensed provider |
| FII and DII flow | Institutional flow evidence | Provider-neutral backend | Unavailable | Provider not selected | Fails unavailable | Select an authoritative source |
| Fundamentals | Financial statements and valuation evidence | Provider-neutral backend | Unavailable | Provider not selected | Missing evidence reduces score and confidence | Select a licensed provider |
| Corporate events | Results, dividends, splits, and announcements | Provider-neutral backend | Unavailable | Provider not selected | Fails unavailable | Select an exchange or licensed provider |
| Stock news | Attributed company news | Normalized backend | Partial advisory sources only | Comprehensive provider not selected | Unverified stories cannot become trade evidence | Select a licensed provider |
| Azure OpenAI | Evidence-grounded briefing and research | Integrated | Unavailable; no production snapshots | Endpoint, key, and deployment missing | Retrieval evidence must exist before generation | Configure Azure OpenAI |

## Official Dhan capability check

The implementation was compared with the current official DhanHQ v2
documentation:

* [Live Market Feed](https://dhanhq.co/docs/v2/live-market-feed/) permits up to
  five WebSocket connections per user with up to 5,000 instruments on each
  connection. The server pings every 10 seconds and closes an unresponsive
  client after 40 seconds.
* [Market Quote](https://dhanhq.co/docs/v2/market-quote/) supports LTP, OHLC, and
  market depth for up to 1,000 instruments in one request at one request per
  second.
* [Instrument List](https://dhanhq.co/docs/v2/instruments/) provides compact,
  detailed, and segment-specific masters with security ID, exchange, segment,
  instrument, underlying, expiry, strike, option type, lot size, and tick size.
* [Historical Data](https://dhanhq.co/docs/v2/historical-data/) supports daily
  history to instrument inception and 1, 5, 15, 25, and 60-minute intraday
  history for active instruments. Intraday requests are limited to 90 days per
  call.
* [Option Chain](https://dhanhq.co/docs/v2/option-chain/) provides expiry lists,
  OI, Greeks, volume, LTP, bid, ask, and IV across NSE, BSE, and MCX options. A
  unique request is permitted every three seconds.
* [Expired Options Data](https://dhanhq.co/docs/v2/expired-options-data/)
  provides rolling strike-relative option data for up to five years. Official
  intervals are 1, 5, 15, 25, and 60 minutes and one call covers up to 30 days.

ARC currently uses smaller rolling-option windows because production
observation found dense 30-day responses less reliable than 15-day windows.
That is an operational choice within the provider's documented maximum.

## Credential ownership

| Secret family | Owner | Storage | Must not happen |
|---|---|---|---|
| Dhan access token | Static-IP VM token service | Root-owned VM environment, token cache, and shared database publication | App Service or another host renews the token |
| Dhan client ID | Static-IP VM and read-only consumers | Azure settings or root-owned VM environment | Printed in diagnostics |
| Telegram bot token | Alert and ingestion runtime | Azure settings or root-owned VM environment | Rendered in UI or logs |
| Telethon session | Ingestion runtime | Root-owned environment | Stored in repository |
| Azure OpenAI key | App Service | Azure application settings or Key Vault-backed configuration | Stored in database snapshots or repository |

## Activation order

1. Reconcile radar instruments into canonical identity in bounded batches.
2. Load the shipped 197-stock reference universe into staging.
3. Refresh current Dhan master metadata.
4. Backfill a small reviewed equity set, then expand with job monitoring.
5. Synchronize parsed tips and active radar opportunities.
6. Activate reviewed scanner definitions.
7. Import broker statements in read-only reconciliation mode.
8. Configure external research providers only after provider selection.
9. Configure Azure OpenAI only after retrieval evidence is populated.
