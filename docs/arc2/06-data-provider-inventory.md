---
title: ARC 2.0 Data Provider Inventory
description: Inventory of external and internal data providers, credentials, provenance, reliability, and fallback policy
---

## Provider policy

Every provider integration should expose:

* Provider and endpoint.
* Data product and instrument identity.
* Provider timestamp and application receipt timestamp.
* Freshness threshold.
* Retry and rate-limit behavior.
* Whether fallback data was used.
* Last success and last error.

Fallbacks must be labeled. ARC 2.0 must not return a success-shaped live response when only stale, repaired, backfilled, or modeled data is available.

## External provider inventory

| Provider | Uses | Authentication | Current reliability behavior | ARC 2.0 action |
|---|---|---|---|---|
| Dhan REST | Quotes, option chain, candles, instrument master, orders, token renewal | Client ID and access token | Timeouts, local/shared token, selected caching and retries | Retain; standardize errors, age, and rate limits |
| Dhan WebSocket | Live radar, NIFTY, and CAS feeds | Dhan credentials | Long-running VM and App Service workers | Retain; expose subscription and reconnect health |
| Dhan instrument master | Equity and option reference | Public/download path used by services | Periodic refresh and reconciliation | Retain; make canonical identity source explicit |
| NSE archive CSV | NIFTY constituents and sector/reference enrichment | Public HTTPS | Static fallback exists for some index symbols | Retain; label fallback and cache version |
| Telegram Bot API | Private alerts and token reminders | Bot token and chat ID | Delivery errors persisted in alert records | Retain; per-user routing and retry policy |
| Telethon | Private Telegram channel ingestion | API ID, API hash, session | Optional startup based on credentials | Retain; scoped ingestion and health |
| Public Telegram web pages | BeatTheStreet and public message observation | None | Advisory failures | Keep advisory; never gate core radar |
| Chartink exports | Scanner triggers and research source data | Legacy/export workflow | Imported from legacy data or files | Retain provenance; remove web-triggered legacy sync |
| Yahoo Finance | Research equity bars | Public external access | Used only by research scripts | Keep outside production runtime |
| Broker statement files | Dhan, Zerodha, and Sahi P&L | User-provided files | Parsed into normalized rows and summaries | Retain; add import manifest and duplicate checks |

## Internal data sources

| Source | Consumers | Provenance issue | ARC 2.0 action |
|---|---|---|---|
| PostgreSQL | Web app, VM workers, shared settings and status | Domain and control state are mixed | Retain; type configuration access |
| Development SQLite | Local development | Can become a misleading empty fallback on a misconfigured VM | Refuse production-like commands without expected database |
| Legacy SQLite | Telegram and Chartink synchronization | Separate historical identity and manual trigger | Isolate behind operations workflow |
| Published research CSVs | Production research page | Manual publication drift | Add manifest, hash, and generation metadata |
| `research/` workspace | Exploratory scripts and reports | Path, environment, and artifact variability | Keep out of deployment package |
| Process cache | Ticker and web aggregation | Not shared across workers | Use only for short-lived optimization |
| Token file | VM resilience | Local cache can be mistaken for ownership | Keep as cache; database remains shared publication |

## Dhan integration details

### Credential ownership

* The static-IP VM is the sole token-renewal owner.
* App Service must keep `DHAN_TOKEN_OWNER=0` or unset.
* A renewal invalidates the previous account token globally.
* The VM publishes the renewed token to shared PostgreSQL.
* A local token file on the VM is a resilience cache, not an ownership signal.

### Data products

* Instrument master.
* NIFTY last traded price.
* Full market quote.
* Option-chain expiry list and snapshots.
* Rolling option candles.
* Intraday candles.
* WebSocket ticker and market depth.
* Super Order creation, fetch, modification, cancellation, and event history.

### Known limitations

* History API reliability varies during market hours.
* Expired futures and options may no longer be reachable through the current instrument master.
* Rolling ATM data is not a survivorship-free fixed-contract history.
* Broker order placement requires a whitelisted static IP.
* Token renewal and order placement have account-wide operational consequences.

## Telegram integration details

### Ingestion paths

* Telethon reads configured channels and persists `ChatMessage` rows.
* The CSRF-exempt ingest API accepts Telegram webhook or custom bulk payload shapes.
* Public web-page parsing supports advisory market context.

### Risks

* The ingest API accepts requests when no expected token is configured.
* Source categories and parser decisions are partly conventional strings.
* Public web pages can reset TLS connections or change markup.

### Required policy

* Fail closed when the ingest token is absent.
* Identify the source channel and message ID.
* Preserve raw payload and normalized interpretation.
* Keep public-page failures advisory.
* Never create a broker order from an ingested message.

## Research data inventory

### Major source families

* Dhan rolling ATM and fixed-strike option data.
* Dhan index and stock candles.
* Yahoo Finance daily equity bars.
* Chartink scanner exports.
* Telegram/public-channel messages.
* Generated CSV, Parquet, NumPy, Pickle, log, and Markdown artifacts.

### Material validity risks

* Rolling ATM recentering creates survivorship and moneyness bias.
* Missing or unquoted option exits can reverse strategy conclusions.
* Modeled spread and slippage may not represent executable fills.
* Some validation windows are small or regime-specific.
* Missing expired-contract history limits longitudinal tests.
* Manual publication can drift from the latest local experiment.

### Production publication boundary

The production research page reads only these approved families:

* Option strategy and large-run tables.
* Chartink ARC15MIN and NARC1HR tables.
* Chartink option-stage, money, hurdle, and control tables.
* Spread summary, DTE, exit, cycle, slippage, month, and moneyness tables.

The publication command compares the source and packaged tables. ARC 2.0 should retain this boundary and add an immutable manifest.

## Environment and secret inventory

### Django

* `DATABASE_URL`
* `DJANGO_SECRET_KEY`
* `DJANGO_DEBUG`
* `DJANGO_ALLOWED_HOSTS`
* `DJANGO_CSRF_TRUSTED_ORIGINS`
* `DJANGO_SECURE_HSTS_SECONDS`

### Dhan

* `DHAN_CLIENT_ID`
* `DHAN_ACCESS_TOKEN`
* `DHAN_TOKEN_FILE`
* `DHAN_TOKEN_OWNER`
* `DHAN_SECURITY_ID_FALLBACK`

### Telegram

* `TELEGRAM_API_ID`
* `TELEGRAM_API_HASH`
* `TELEGRAM_SESSION`
* `TELEGRAM_SESSION_STRING`
* `TELEGRAM_RETRY_SECONDS`
* `TELEGRAM_CATCHUP_DAYS`
* `TELEGRAM_INGEST_TOKEN`
* `TELEGRAM_DIAGNOSTICS_TOKEN`
* `TELEGRAM_BOT_TOKEN`
* `TELEGRAM_ALERT_CHAT_ID`

### NIFTY and radar

* `ARC_EXECUTION_MODE`
* `NIFTY_LIVE_ENABLED`
* `NIFTY_LIVE_DRY_RUN`
* `NIFTY_LIVE_CAPITAL`
* `NIFTY_LIVE_FIXED_LOTS`
* Other settings declared by `live_config.py`
* `NIFTY_CAS_SYMBOLS`
* `NIFTY_CAS_OUTPUT_DIR`
* `RADAR_HEALTH_TELEGRAM`

### Runtime

* `PORT`
* VM systemd `EnvironmentFile=/etc/optionsdjango.env`

Secrets should remain in Azure configuration or the root-only VM environment file. ARC 2.0 documentation should record names and ownership, never values.

## Provider acceptance checklist

A provider is ready for an ARC 2.0 production workflow only when:

* Authentication failure is distinguishable from no data.
* Timeouts are bounded.
* Retry policy is bounded and observable.
* Rate limiting is handled explicitly.
* Source timestamps are preserved.
* Stale thresholds are defined.
* Raw evidence is retained where legally and operationally appropriate.
* Tests use recorded or synthetic fixtures, not a live provider.
* A degraded or unavailable state is visible in the UI.
