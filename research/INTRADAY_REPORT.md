# Why stock options don't ship, and what does

*Measured 18 August 2026. The closing report on the stock-option programme.
Companions: `STOCK_OPTIONS_REPORT.md`, `CHARTINK_CE_REPORT.md`,
`SPREAD_REPORT.md`, `PREMOVE_REPORT.md`. What actually ships: `STRATEGY.md`.*

Every entry signal tested on stock options is a null — 68 of 70 combinations,
both Chartink scans, the pre-move predictor at three strike rungs. This report
is the last untested lever: the **exit**, ported from the NIFTY strategy that
ships live.

It produces the single most useful number in the programme, and it is not a
strategy. It is an explanation for why there isn't one.

---

## 1. The one number

Buy an ATM stock call or put at a random bar, run the shipped NIFTY exit —
10% hard stop, trail arms at +7% and follows 7% behind the high, no target,
flat before the bell — and close intraday. 96,304 trades, 189 symbols, 366
sessions.

| | |
|---|---|
| Gross return, before any cost | **+1.024%**, day-clustered t **+3.50** |
| Round-trip friction | **−1.639%** |
| **Net** | **−0.614%**, day-clustered t **−3.98** |

**Gross of costs, buying an intraday stock option is a winning trade, and it is
statistically real.** Net of costs it loses, and that is also statistically
real. The entire loss is friction, and there is 62 basis points of it left over
after the gross edge is spent.

That single line explains every null in this programme at once. No entry signal
works because the gross game is already fair-to-favourable without one. The
exit matters enormously — because holding overnight bleeds theta *beyond* the
fair game. And nothing ships because the round trip costs 1.64% and the market
does not hand you 1.64%.

## 2. Where the friction actually is

| Component | Cost | Fixed? |
|---|---|---|
| Bid-ask (5-paise tick, both ways) | 1.069% | No — scales with 1/premium |
| STT, brokerage, stamp, exchange | 0.570% | Yes |

The tick is **two-thirds of the bill**, and it is the part that moves. At the
₹21 median premium of an ATM stock option, a 5-paise round trip is 48 basis
points before tax. On NIFTY, where the shipped strategy requires a ₹100+
premium, the same 5 paise is 5 basis points. **That is a tenfold difference in
the only cost that is controllable**, and it is the whole reason one instrument
ships and the other does not.

## 3. Why you cannot buy your way out of it by going cheaper

The obvious escape is to raise the premium and shrink the tick's share. It
fails, because the gross edge lives in exactly the contracts where the tick is
worst:

| Premium | n | Gross | Friction | Net | t(gross) |
|---|---|---|---|---|---|
| < ₹5 | 15,451 | **+6.11%** | −4.47% | +1.64% | +9.23 |
| ₹5–10 | 15,031 | +2.01% | −1.98% | +0.03% | +2.95 |
| ₹10–20 | 16,423 | +0.35% | −1.28% | −0.93% | +0.45 |
| ₹20–40 | 18,037 | +0.03% | −0.92% | −0.88% | −0.23 |
| ₹40–80 | 15,206 | −1.00% | −0.74% | −1.74% | −5.35 |
| ₹80+ | 16,156 | −1.06% | −0.63% | −1.69% | −4.61 |

Read the two middle columns against each other. Gross falls monotonically as
premium rises; friction falls monotonically too, and **they fall at the same
rate**. Cheap options have more gamma, so the trail captures a larger percentage
move — and the tick is a larger percentage of the position by exactly the same
arithmetic. Leverage and cost are the same quantity measured twice.

This is the identical diagonal the strike-ladder study found: *an ATM+2 call on
a signal day costs precisely what an ATM call costs on a non-signal day.* The
market is not leaving a cheap-option premium on the table.

A direct premium-floor sweep confirms it from the other side — the NIFTY ₹100
floor, which is a genuine costs finding on the index, **inverts on stocks**:

| Floor | n | Net | t(day) | Tick as % of premium |
|---|---|---|---|---|
| none | 110,172 | −0.61% | −3.98 | 0.23% |
| ₹10 | 75,810 | −1.29% | −12.73 | 0.13% |
| ₹50 | 29,804 | −1.72% | −12.90 | 0.05% |
| ₹100 | 14,514 | −1.78% | −5.89 | 0.03% |

Monotonically worse. The floor buys a cheaper tick and pays more than that back
in lost gamma.

### The sub-₹5 bucket is a lottery, not a loophole

It is the one positive net cell in the table and it needs killing explicitly,
because +1.64% net on 15,451 trades reads like a strategy.

- **Day-clustered t is −3.83**, against a +1.69% mean. Median trade −7.10%,
  win rate 32.8%. Positive mean, negative median, significantly negative once
  you stop treating 15,224 trades in 366 sessions as independent.
- **It dies on half a tick.** +1.69% at a 5-paise assumption → **−2.05% at 10
  paise** → −5.63% at 15 → −27.03% at 50. A ₹3 stock option is not quoted 5
  paise wide, and 5 paise is an *index* assumption imported wholesale.
- The 11 of 19 profitable months are the early ones (+4.9%, +4.3%, +2.8% in
  Feb–Apr 2025) decaying to negative through 2026.

It is *not* the stale-quote trap, which was the first suspicion: 79% of these
trades moved 3+ ticks and only 1.9% of gross gains come from trades that moved
≤2 ticks. The data is fine. The payoff shape is a lottery and the spread
assumption is fantasy.

## 4. The entry signal makes it actively worse

The opening-range breakout — the NIFTY entry, ported with its volume and
direction filters — run against the any-bar control on the identical exit:

| | Gross | Net | t(net) |
|---|---|---|---|
| Any-bar control | **+1.024%** | −0.614% | −3.98 |
| ORB + volume + green bar | **−0.415%** | −2.006% | −4.03 |

The breakout does not merely fail to add. It **destroys the gross edge**,
+1.02% → −0.42%, on identical friction. Selecting on a breakout selects moments
where the subsequent intraday drift is worse than random — you are buying after
the move, into elevated premium.

*A note on how close this came to being reported as a success.* The first run of
this test returned **+6.57%, t +5.09, 56.5% win** — which would have been the
first working stock-option strategy in the programme. It was look-ahead: the
option-side filters read the completed volume and close of the bar we buy at
the *open* of. Reading bar `i` (the signal bar, complete at decision time)
instead of bar `j` turns +6.57% into −2.01%. The look-ahead was the entire
result. This is the same bug already documented for the Chartink scans, which
stamp a trigger with the candle's start time rather than its close.

## 5. No slice rescues it

| Slice | Result |
|---|---|
| By side, common sessions | CALL −0.71% (t −2.13), PUT +0.04% (t +0.30) |
| By month | **3 of 19 positive**, and all three are the first three |
| By entry bar | No 15-minute window clears 1.0 with significance |
| First half vs second | −0.27% (t −0.08) then −0.96% (t −5.00) |

The side split is worth a sentence. In a sample that rose, **calls are the
losing side and puts are a clean null** — so the loss is not a direction bet
gone wrong, it is the cost of carrying premium, and calls cost more of it. The
put column sits on the cache's Sep 2025 – May 2026 hole, so it is measured on
198 common sessions rather than 366; it is a null, not a positive.

The monthly column is the one that settles it. Three positive months, all at
the very start, decaying monotonically to −1.0% to −1.9% for the last twelve.

## 6. Verdict

**Intraday stock option buying is a fair game that loses its friction.** The
exit architecture is worth 22 paise on the rupee — it takes the base rate from
0.773x on a two-session hold to 0.9939x intraday, the largest single lever
measured anywhere in this programme, larger than the gap between the best and
worst entry signal ever tested. It lands about 1.6 percentage points short, the
shortfall is the bid-ask, and the bid-ask cannot be reduced without giving back
more gamma than it saves.

The stock-option programme is complete and it is a null. Not for want of a
signal — because the instrument's round trip costs more than the instrument's
intraday drift is worth.

## 7. What ships instead

The same architecture on the index, where the friction is a tenth of the size:

| NIFTY intraday, `STRATEGY.md` | |
|---|---|
| Return, ₹1L over 246 sessions | **+38.6%** |
| Win rate | 66.7% |
| Max drawdown | 5.1% |
| Net ÷ drawdown | 7.55 |
| Survives a ₹2 spread | yes, at 73% of the return |

Identical exit — 10% stop, trail arming at +7% and following 7% behind the
high, no target, flat at 15:20. The difference is not the idea. It is that a
₹100 NIFTY premium pays 5 basis points of tick where a ₹21 stock premium pays
48, and that a NIFTY option is quoted tightly enough that the 5-paise
assumption is true rather than aspirational.

That strategy is live on Dhan today, and it is the answer to "a proper options
buying strategy."

### One caveat, measured the same day

The friction rule above predicts that a higher-premium index should work at
least as well. **SENSEX was tested and it fails** — 46.7% win and −₹6,604 over
the identical window where NIFTY makes 69.0% and +₹29,646, on the shipped rules
with no refit. On the 23 sessions where both indices fired the same signal,
NIFTY is +0.63R and SENSEX is −0.13R.

So the friction rule is necessary but not sufficient. A high premium is why
stock options cannot work at ₹21; it is not enough on its own, because SENSEX
has four times the cushion and still loses. Cheap friction lets an edge survive,
it does not create one — and the NIFTY edge is, on current evidence,
NIFTY-specific. See `SENSEX_REPORT.md`.

---

## Method notes

- **Intrabar path is unknown, so the stop is assumed to win.** If a 15-minute
  bar's low breaches the stop *and* its high would have advanced the trail, this
  books the stop. Pessimistic by construction; the alternative manufactures
  profit.
- **Nothing is imputed.** Every exit comes from a real quoted bar. The spread
  study's survivorship bug — dropping exits that could not be priced, which
  happens precisely when the market runs away from the strike — cannot occur
  here.
- **Intraday defuses two of the five feed traps.** A strike pinned at 09:30 and
  released by 15:30 barely drifts, and a corporate action happens between
  sessions, so it cannot land mid-trade.
- **Untradeable entries are dropped, not scored.** An entry on the session's
  last bar has no bar left to exit into; `simulate` returns `None` and those
  rows are excluded from every statistic. 13,868 of 110,172 control rows.
- **Slippage is computed exactly, not re-simulated.** The stop and trail trigger
  off the raw entry price, not the filled price, so the exit path is independent
  of the slippage assumption and the curve can be evaluated in closed form.
- **All significance is day-clustered.** Option winners cluster hard — one
  market-wide rally pays hundreds of calls at once — so an unclustered t over
  96,304 trades sitting in 366 sessions overstates by a wide margin. Every
  positive-looking result in this programme has died on this test.

Files: `research/stock_intraday.py`, `research/stock_intraday2.log`,
`research/stock_intraday.csv`.
