# The Finalised Strategy

*Decided 2026-08-16. Measured on 246 sessions, 2025-08-18 to 2026-08-14.*

**NIFTY weekly ATM option buying, opening-range breakout entry, trailing exit.**
Buying only — nothing here needs margin to sell.

Implemented in `options_tracker/nifty_trail_strategy.py::nifty_trail_config()`.
The production path was run end to end after the change and reproduces the
research figures to the rupee.

---

## 1. What it did

| | over 246 sessions | over the most recent 123 |
|---|---|---|
| trades | 51 | 42 |
| win rate | 66.7% | **69.0%** |
| net on ₹1,00,000 | **₹38,626** (+38.6%) | ₹29,646 |
| maximum drawdown | ₹5,114 (5.1%) | ₹4,538 |
| premium points per trade | 9.14 | — |
| net ÷ drawdown | **7.55** | 6.53 |

Best trade +₹5,468, worst −₹2,828, average +₹757. Longest losing run **4
trades**. Most capital ever deployed at once ₹27,407; most ever at risk behind a
stop ₹2,740.

**It got better, not worse.** Of eight premium bands tested, this is the only one
whose second half beat its first (₹13,605 → ₹16,742). Every other band roughly
halved; the band this replaces was the worst of them (₹24,232 → ₹8,473).

**It survives a bid-ask, which is the point.** At a ₹2 round-trip spread it still
returns ₹28,208 — **73% of the mid-price result**, against 22% for the old
₹50–250 band. Bid-ask is the largest sensitivity in the entire study.

**Expect about five trades a month, and expect that to be lumpy.** March 2026
gave 17; August through October 2025 gave none at all. A quiet month is the
strategy behaving as measured, not a fault.

---

## 2. When to trade

- **NIFTY weekly options only.** Nearest expiry.
- **Entry window 09:30 – 15:09.** One continuous window; no lunchtime blackout.
- **Everything is flat by 15:20**, without exception.
- **Expiry days are not banned.** An earlier draft of this file said "skip expiry
  day entirely" and that was wrong — no such rule exists in the code and none
  should. What removes almost all expiry-day trades is the ₹100 premium floor:
  median ATM premium on an expiry session runs ₹51 in the morning down to ₹19 by
  14:30, against ₹123 → ₹114 on a normal session, so most expiry signals are
  sub-₹100 and get filtered on cost. Two cleared the floor in 246 sessions and
  both won. Expiry-day buying *is* genuinely weaker — a momentum entry wins
  34–52% there against 51–62% on normal days — but the floor already handles
  that, and it does so for a reason that survives a bid-ask instead of by
  blacklisting a date. Nothing to do; just don't be surprised by an expiry-day
  signal.
- **The late session is the weakest cohort.** Nothing forbids it and only 2 of 51
  trades landed there, but see §8.

---

## 3. The entry rule

### Step 1 — mark the opening range (once, at 09:30)

From NIFTY **spot** between 09:15 and 09:30, take the high and the low.

```
opening_high, opening_low        # 15 minutes of spot
call_trigger = opening_high * 1.0003     # a 0.03% buffer
put_trigger  = opening_low  * 0.9997
```

### Step 2 — wait for a breakout bar (1-minute spot bars)

A side fires when, on a **completed 1-minute spot bar** inside the entry window:

- **CALL:** spot close > `call_trigger`, and the **5-minute** context bar in
  progress is green (close > open).
- **PUT:** spot close < `put_trigger`, and the 5-minute context bar is red.

Each side starts **armed** and disarms the moment it fires. It re-arms only when
spot comes back to the boundary — back to `opening_high` or below for calls, back
to `opening_low` or above for puts. This is what stops one breakout being bought
five times.

### Step 3 — the contract has to agree

On the same 1-minute timestamp, on the **ATM strike** for that side:

| filter | rule |
|---|---|
| moneyness | **ATM exactly** — zero strikes away |
| entry premium | **≥ ₹100**, no upper cap |
| volume | that bar's volume ÷ median volume of the prior 5 bars **≥ 1.5** |
| spot direction | spot now vs spot 5 minutes ago: **up** for a call, **down** for a put |
| spot move | that 5-minute move is **≥ 0.15%** in absolute terms |
| option bar | the option's own 1-minute bar must close **above its open** |

All six must hold. If they do, the signal is live.

### Step 4 — enter on the next minute

```
entry = max(next_bar_open, signal_bar_close) * 1.005
```

The 0.5% uplift is modelled slippage. **If the next bar's high never reaches that
price, the trade does not happen** — do not chase it.

---

## 4. The exit rule

```
risk       = entry * 0.10             # the stop is 10% of premium
stop       = entry * 0.90             # hard, from the moment of entry
trail_gap  = 0.7 * risk = entry * 0.07
```

- **There is no profit target.** The fixed 1.25R target is switched off when the
  trail is on. Targets were tested across eight families of price levels and none
  beat trailing.
- **The trail arms at +7%.** Once the running high is at least `entry * 1.07`,
  move the stop to `high_water - entry * 0.07`.
- **The stop only ever moves up.** Never widen it, never reset it.
- **Hard square-off at 15:20** at market, whatever the position is doing.

Exit reasons, in the order they are checked each minute: stop hit → trail hit →
15:20.

---

## 5. Position sizing

Lot size **65**. Account **₹1,00,000**, compounding.

```
unit_risk  = entry - stop            # = entry * 0.10
risk_lots  = floor(equity * 0.02 / (unit_risk * 65))
cash_lots  = floor(equity * 0.40 / (entry * 65))
lots       = max(0, min(risk_lots, cash_lots))
```

Take the **smaller** of the two, so neither 2% risk nor 40% deployed capital is
ever breached. **If `lots` is zero, skip the trade** — do not round up to one.

---

## 6. Daily limits

| limit | value |
|---|---|
| maximum trades per day | **3** |
| cooldown after an exit | **10 minutes** before the next entry |
| daily loss limit | stop trading for the day at **−2R** |
| ties on the same minute | take the one with the highest `volume_ratio × breakout_percent` |

---

## 7. Placing it on Dhan

`options_tracker/services.py::place_super_order` already posts to
`https://api.dhan.co/v2/super/orders` with an entry, a stop and a target in one
call. Two things need care before it is pointed at this strategy.

**The target field must not be used.** This strategy has no target. Sending a
`targetPrice` would cap the winners that pay for the losers.

**Dhan's `trailingJump` is not this trail, and the difference is not cosmetic.**
`trailingJump` moves the stop up from the moment price moves. This strategy
leaves the stop *fixed at −10%* until the trade is **+7%** in profit, and only
then follows 7% behind the running high. Setting a jump would tighten the stop
during exactly the early wobble the 10% stop exists to absorb — and stop-hunting
was already diagnosed as this strategy's main leak: 72% of stopped contracts
later trade back above entry.

So, concretely:

1. Place the super order with `stopLossPrice = round(entry * 0.90, 2)`,
   `trailingJump = 0`, and **no** target.
2. Track the running high client-side, once a minute.
3. Once `high_water >= entry * 1.07`, modify the order's stop to
   `round(high_water - entry * 0.07, 2)`. Only ever upward.
4. Square off at 15:20 regardless.

Until the trail is managed client-side, running with the fixed 10% stop alone is
the safe subset: it gives up profit but never takes on unmeasured risk.

---

## 8. What this rests on, honestly

- **The spread is modelled, not measured.** Every figure here assumes a bid-ask
  that has never been observed on a real fill. The band was chosen for how it
  degrades under one — 73% retained at ₹2 round-trip — precisely because that
  assumption is the weak link. **Log quoted bid/ask against actual fills from
  day one.** It is the single most valuable data this account can produce.
- **51 trades is not a large sample.** The win rate is the durable property; the
  rupee total is indicative.
- **The ₹100 floor is a costs finding, not an alpha one.** Sub-₹100 contracts are
  not losers — 20 of them won 70% and booked ₹5,645 — but they capture only 1.73
  points a trade, so a ₹2 round-trip takes more than their entire edge. Above
  ₹100 the same signal captures 9.14 points. This is *why* it should keep
  working, and why it is a floor rather than a band.
- **Nothing above is new machinery.** Every parameter was already a config field;
  two of them changed value.
- **The last 50 minutes are the weakest part of the window, structurally.** Buy
  the ATM at a sampled minute and run this exact exit on it: 14:30–15:05 entries
  win 44.9% against ~56% in every earlier window, and **18.3% of them are still
  open at the 15:20 bell and get closed flat, against 0.0% earlier**. This
  strategy's edge is the trail; a trade the clock closes before the trail can run
  is a different trade. The signal barely goes there anyway — 2 of 51, worth
  −₹1,030 — which is why the 15:09 cutoff was left alone rather than tightened on
  a two-trade basis. Measured in `research/clock.py`.
- **The early afternoon may be underused.** On the same clock study, 13:00–14:30
  momentum entries capture 3.51 points on 55.3% wins — the best points-per-trade
  of any window — while the shipped signal fires there only twice, because it is
  anchored to a 09:15–09:30 range that is stale by then. Not acted on, not
  tested as a strategy. Noted as the most promising open lead.

---

## 9. Deliberately not included

**The previous-day high/low break.** It makes more money historically
(₹1,24,356), it genuinely adds to this strategy rather than duplicating it
(₹2,27,252 combined, only 3 overlapping positions in 209 trades), and it is the
best second sleeve available. It is out of day one because it is **fragile to
exactly the delay a live fill introduces**: requiring price to clear yesterday's
level by 0.05% instead of merely closing across it cuts it from ₹70,025 to
₹12,647. On NIFTY at 25,000 that 0.05% is **12.5 points** — well inside normal
slippage on a fast break. Its win rate is also decaying (69.0% → 57.5% across the
two halves) while this strategy's is not.

Paper-trade it alongside for a month, compare fills against the 5-minute closes
the backtest assumed, and revisit. Details in `TEST_REGISTER.md`.

**Also excluded, each for a measured reason:** OI walls, expiry-day buying, wider
stops, RSI in any form, the 20 EMA as gate or signal, 15-minute ORB, VWAP
pullbacks, EMA scalping, RSI+MACD, 3 PM momentum, straddles, strangles, and
calendar spreads. See `TEST_REGISTER.md`.
