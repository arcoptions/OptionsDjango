# The two scans, backtested — and why NIFTY works where this does not

*Measured 19 August 2026. Reproducible from `research/breakout_scans.py` and
`research/scan_option_leg.py`.*

You asked three things. Taking them in the order that makes the answer legible,
not the order you asked them.

---

## 1. Why 66% on NIFTY and nothing here

This is the most useful question in the whole programme, and the answer is not
"stocks are harder". It is that **the NIFTY strategy wins 66% because it asks
for very little, and this scheme asks for a great deal.** They are not the same
kind of bet wearing different underlyings.

| | shipped NIFTY rule | this stock scheme |
|---|---|---|
| entry premium | ₹123 median (₹100 floor) | **₹37.40** median |
| round-trip toll | ₹2 on ₹123 = **1.7%** | **7.3%** |
| what it asks for | +9.14 points = **+7.6%** | **+50% to +100%** |
| stop | 10% of premium | 50% of premium |
| holding period | **minutes; flat at 15:20, always** | **9 sessions** |
| overnight theta | none, ever | 9 nights + 2 weekends |
| win rate | **66.7% / 69.0%** | **29.4%** |

Read the last two columns of the toll row together with the "asks for" row,
because that ratio is the entire story:

- **NIFTY: 7.6% gross against a 1.7% toll — the toll eats 22% of the edge.**
- **Stock near-ATM: the toll is 7.3%, which is very nearly the whole of what the
  NIFTY rule captures per trade.** At NIFTY's bite size a stock option would hand
  back 96% of the gross before anything else went wrong.

So the 66% win rate is not a signal-quality number you can port. It is a
consequence of a small target and a short hold. A rule that exits within the
session, takes 7.6%, and pays 1.7% to do it *can* win two-thirds of the time. A
rule that needs a double over nine sessions cannot, on any underlying — and the
measured win rate here, 29.4%, is roughly what that bet is worth.

**And "so many options" is the part that is most misleading.** 189 symbols ×
57 sessions looks like 10,773 chances. It is not:

| | count |
|---|---|
| 1-hour signals in the option window | 1,623 |
| distinct symbol-days | 1,118 |
| ...with a near-ATM call that actually traded | **651** |
| ...where one lot fits in ₹25,000 | **303** |
| ...where one lot fits in the ₹13,000 first tranche | **40 (6.1%)** |

Breadth in names is not breadth in placeable trades. NIFTY has one instrument
and you can always transact in it; here, **more than half the signals cannot be
acted on at ₹25,000 per trade at all**, because the median near-ATM lot costs
₹26,250.

---

## 2. The two scans, at the stock level

Backtested on **1,737,572 fifteen-minute bars, 189 symbols, 369 sessions,
2025-02-17 → 2026-08-17** — eighteen months, not the six weeks the option cache
allows. Entry is the **open of the bar after** the signal. Every number is
against **other symbols' bars at the same timestamp**, with the t-statistic
clustered by session, so a scan cannot be credited for firing on strong days.

### The 15-minute pullback — dead

*EMA20 > EMA63, EMA63 rising and rising on ≥30 of the last 63 bars, this bar's
low pierces EMA63 and the previous bar's did not.* 17,259 signals.

| sessions held | edge vs same-bar control | t (clustered) |
|---|---|---|
| 1 | −0.03% | −0.71 |
| 2 | −0.05% | −1.13 |
| 3 | −0.11% | **−2.28** |
| 5 | −0.11% | −1.94 |
| 10 | −0.09% | −1.35 |

Reach lift 0.96x / 0.92x / 0.90x at +3/+5/+10% — **below 1.0, meaning the stock
runs *less* after this signal than a random bar of the same minute.** Both halves
agree (0.92x, 0.93x). Only 45.3% of days are positive. This one is not
marginal; it is negative, consistently, and the option leg was never run on it.

### The 1-hour breakout — real, but it is volatility, not direction

*close > EMA20, close > UpperBB(20,2), volume > 2× SMA(vol,20), RSI(14) > 60.*
9,724 signals; 5,413 with the band-change filter.

The pooled forward return is +0.145% at one session, which looks like an edge and
is not one: **the daily-difference mean is +0.010%, the median +0.003%, and only
50.4% of 363 days are positive.** That is a coin, and the pooled figure is a
handful of days.

What *is* real is the reach:

| move within 5 sessions | UP reach lift | DOWN reach lift | ratio |
|---|---|---|---|
| ±3% | 1.13x | 1.07x | **1.060** |
| ±5% | 1.22x | 1.09x | **1.122** |
| ±10% | 1.28x | 1.11x | **1.160** |

Split-half stable (1.21x, 1.24x). So the scan genuinely finds stocks about to
*move* — but it lifts the downside almost as much as the upside. **The
directional tilt a call needs is only ~1.12x**, and that is the number the option
leg has to pay a 7.3% toll out of.

This is worth keeping: as a *volatility* filter it works. It is the wrong
instrument choice that fails, not the scan.

---

## 3. Your money-management scheme, priced on real contracts

₹25,000 per trade, ₹13,000 first, add ₹12,000 on a 25% fall, stop at 50% of the
blended entry on a closing basis, first target 50/75/100% with a partial exit and
a 30% trail on the rest. Whole lots only — no fractional lots, because pretending
you can buy half a lot invents exactly the flexibility the scheme depends on.

| first target | trades | win% | P&L total | per trade | median | stopped |
|---|---|---|---|---|---|---|
| +50%, then trail | 296 | 29.4% | **−₹843,013** | −₹2,848 | 0.56x | 47.0% |
| +75%, then trail | 296 | 30.1% | −₹822,698 | −₹2,779 | 0.48x | 49.7% |
| +100%, then trail | 296 | 29.7% | −₹706,026 | −₹2,385 | 0.46x | 52.4% |

**The breakout signal adds nothing.** Same scheme entered on *every* session
rather than on signals:

| | per trade | win% |
|---|---|---|
| signal, +50% | −₹2,848 | 29.4% |
| **control, +50%** | **−₹2,416** | 30.1% |
| signal, +100% | −₹2,385 | 29.7% |
| control, +100% | −₹2,470 | 28.9% |

Indistinguishable, and *worse* at the 50% target. The 1.12x directional tilt does
not survive contact with the toll.

### The scale-in is measurably harmful

The tempting read is that trades which never needed the add returned 1.17x with a
67.7% win rate, against 0.43x and 10.2% for those that did. **That comparison is
worthless** — "never added" just means "the option never fell 25%", which is the
outcome wearing a disguise. You cannot select on it at entry.

The honest test runs both variants over the *same paths*:

| on the 197 trades where the add fired | P&L/trade | win% |
|---|---|---|
| with the ₹12,000 add | −₹8,521 | 10.2% |
| without it | −₹7,771 | 9.6% |
| **the add is worth** | **−₹750** | t **−3.31**, better on **3.0%** |

Averaging down works when the thing you are buying is cheap. An option that has
fallen 25% has usually fallen because time passed, and time does not come back.
You are adding to a position whose decay is accelerating, three days closer to
expiry than when you sized it.

### And the 50% floor does not hold

You said: at most lose 50% of the cash allocated. It does not:

| construction | worse than −50% of deployed | worse than −50% of ₹25,000 | mean |
|---|---|---|---|
| stop vs blended entry *(as specified)* | **48.3%** | 24.3% | −14.3% |
| stop vs the first fill | 42.6% | 19.6% | −13.3% |
| no scale-in at all | 50.7% | **17.6%** | −12.8% |

Two leaks, and they are separate. First, a **closing-basis** stop on a daily bar
in a 10x-levered instrument gaps straight through — the option closes at 40% of
entry and the stop never had a chance to be 50%. Second, averaging down **drags
the blended entry below the first fill**, so a stop at "50% of the blend" sits
well below half of what you originally paid. Measuring the stop against the first
fill instead recovers about five points of that, and dropping the add recovers
the rest of the allocation-level damage.

---

## What I would do with this

1. **Keep the 1-hour scan. Stop buying calls with it.** It is a genuine
   volatility detector — 1.22x reach at 5%, stable across halves — and that is a
   real, reusable finding. It is not a direction detector, and a long call needs
   direction.
2. **Drop the 15-minute pullback.** It is negative on eighteen months with a
   clustered t of −2.28 and reach *below* the control.
3. **Drop the averaging-down rule specifically.** It is the one component here
   with a properly-controlled, statistically significant negative effect
   (−₹750/trade, t −3.31, helps 3% of the time). This holds regardless of what
   else you decide.
4. **The ₹13,000/₹12,000 split cannot be run as written** — it buys a lot 6.1%
   of the time. Any version of this needs ₹50,000+ per position, which at ₹1 lakh
   means two positions and no diversification at all.

The NIFTY rule remains the thing that works, and now there is a reason rather
than a coincidence: **it takes a small bite, quickly, on the one instrument in
this market where the toll is small enough to leave the bite intact.**

---

*Caveats that bound everything above. The stock leg is eighteen months and is
solid. The option leg is six weeks (2026-05-27 → 2026-08-17), one regime — the
pinned deep-OTM cache is the only feed that can price a multi-day hold, because
the 18-month 15-minute option feed is ATM-relative and drops contracts as spot
walks away (42–46% survive five sessions), carries no expiry column, and mixes
maturities under one key. Expired contracts return DH-907 and their security ids
are published nowhere, so a longer history is not obtainable. 16.0% of bars on
the near-ATM contracts traded here are stale repeats, which defers exits rather
than inventing them.*
