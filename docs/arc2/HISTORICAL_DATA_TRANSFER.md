---
title: ARC 2.0 Historical Data Transfer
description: Credential-confined Dhan history capture and validated staging import procedure
---

## Safety boundary

The Dhan credential remains on its designated production VM. Staging receives
only normalized market candles in a credential-free JSON fixture.

The transfer commands enforce these controls:

* Capture does not write database rows
* Capture fails without creating a partial output file
* Existing output files require explicit `--force`
* The fixture excludes security IDs, request headers, and credentials
* Import rejects token, client ID, authorization, password, API key, and
  header fields in any JSON object
* Import defaults to a database read-only dry run
* `--apply` imports all validated rows in one transaction
* Duplicate candle identities are ignored during repeat imports
* Download evidence includes the fixture SHA-256 prefix

## Capture on the token-owning VM

Run the bounded pilot against five active tracked stocks:

```text
python manage.py capture_stock_history_fixture ^
  C:\arc-transfer\dhan-history-5x10.json ^
  --days=10 ^
  --limit=5 ^
  --interval=15
```

If the token-owning runtime does not have the staging stock universe, pass the
reviewed symbols and Dhan security IDs explicitly:

```text
python manage.py capture_stock_history_fixture ^
  C:\arc-transfer\dhan-history-5x10.json ^
  --days=10 ^
  --stock=RELIANCE:2885 ^
  --stock=HDFCBANK:1333 ^
  --stock=ICICIBANK:4963 ^
  --stock=INFY:1594 ^
  --stock=TCS:11536 ^
  --interval=15
```

Explicit stocks avoid production database writes. The command validates each
symbol and security ID before calling Dhan.

If Dhan explicitly rejects the rolling-option endpoint, capture the same
reviewed stocks with `--equity-only`. This mode preserves real equity data for
scanner activation and records an empty option array. It does not satisfy the
option-history acceptance gate, which remains blocked until Dhan restores or
clarifies the provider contract.

The command captures:

* Fifteen-minute equity candles
* Fifteen-minute monthly rolling ATM call candles
* Fifteen-minute monthly rolling ATM put candles

Every selected stock must return all three streams. Any provider error or empty
stream stops the command before the destination file is created.

## Transfer controls

Before moving the file:

1. Record its SHA-256 hash.
2. Verify that it contains no credential or request-header fields.
3. Transfer it through the approved operator channel.
4. Compare the destination hash with the recorded source hash.
5. Delete temporary transfer copies after staging validation.

Do not place the fixture in Git, application static storage, or a public
artifact service.

## Validate in staging

The first import command is always a dry run:

```text
python manage.py import_stock_history_fixture ^
  C:\arc-transfer\dhan-history-5x10.json
```

The validator requires:

* Schema version 1 and provider `DHAN`
* A timezone-aware capture timestamp
* ISO 8601 window dates
* Active tracked-stock symbols
* Supported Dhan intervals
* Timezone-aware candle timestamps inside the declared window
* Non-negative finite numeric values
* Consistent OHLC bounds
* Unique fixture candle identities
* Valid option side and ATM-relative strike labels

## Apply in staging

Apply only after the dry-run hash and row counts match the operator record:

```text
python manage.py import_stock_history_fixture ^
  C:\arc-transfer\dhan-history-5x10.json ^
  --apply
```

Repeat the same command to prove idempotency. Equity and option candle counts
must remain unchanged on the second application.

## Acceptance evidence

The pilot passes when:

* Five requested stocks are present
* Equity, ATM call, and ATM put candles exist for every stock
* Candle timestamps and the declared window agree
* No duplicate identities are present
* Download jobs contain the fixture hash prefix
* A repeated import inserts zero additional candles
* One scanner run reads the persisted equity bars
* The Release Dashboard reports the resulting candle and scanner counts
