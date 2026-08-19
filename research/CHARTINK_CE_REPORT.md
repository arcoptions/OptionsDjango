# Chartink → stock → CE: the two-stage test

Measured 18 Aug 2026. Scripts: `research/chartink_options.py` (the two stages),
`research/chartink_control.py` (the control that decides what the loss means).
Numbers are read back out of `research/chartink_ce_*.csv`, so re-running either
script moves this report and the `/research/` page together.

The workflow being priced is the one you described: a Chartink scan names a
stock, we buy its call, we exit with a gain. That splits in two, and the second
half is only worth asking if the first half survives.

---

## The short version

**Stage 1 (stock): null.** 875 triggers, 23 trading days, 198 symbols. Excess
return over the market is ~0 at every horizon. Best t-statistic on the whole
grid is +0.94.

**Stage 2 (CE): loses at every horizon.** 791 trades. 0.961× at 30 minutes,
0.784× by the close, 0.723× held two sessions. At real lot sizes that is
**−₹27.3 lakh on ₹1.54 crore deployed.**

**And the scan is not what loses the money.** A control that prices every
*other* bar of the same 608 symbol-days finds the trigger bar and a random bar
are indistinguishable — under 1.2 paise apart on the rupee, t between −1.79 and
+0.37. You would have lost roughly the same money pressing the button at random.

So the honest answer to "how much will we make": **you don't.** Not because the
scan is bad, but because the CE leg costs more than the scan is worth.

---

## Data, and one bug I had to fix first

| | |
|---|---|
| Triggers | 875 (ARC15MIN 296 + NARC1HR 579), 16 Jul → 17 Aug 2026 |
| Sources | `Backtest arc15min.csv`, `Backtest arc15min (1).csv`, `Backtest narc1hr.csv` |
| Symbols with option data | 152 of 198 |
| Option bars on trigger days | ~134,000 and rising |

I used both `arc15min` exports — the second one (242 triggers) had not been in
the earlier study, which nearly doubles that scan's sample.

**Two corrections carry over and neither is optional.** Chartink stamps a
trigger with the candle's **start**, not its close: a 09:15 15-minute trigger
describes 09:15–09:30 and is not knowable until 09:30. Entry is the open of the
bar *after* the signal candle closes. And because a breakout scan fires when the
whole market is moving, every trigger is compared against the average cached
stock over the identical clock window.

**The bug.** `StockOptionCandle` stores `expiry_code=1` — front month — but
never the expiry *date*. My first run followed a fixed strike forward and
silently walked out of the dying July contract into the freshly-rolled August
one, at a fraction of the price. It printed a **228× on KALYANKJIL**: a 610 CE
bought at ₹0.05 on 28 July, "sold" at ₹11 two days later as a different
contract. NARC1HR looked like 2.84× mean because of this.

The tell was mean 2.84× against median 0.83×. `find_expiries()` now reads the
boundary off the data — the median ATM premium decays to 0.29% of spot on 28
July 2026, then opens at 3.33% the next session — and holds are capped there.
Every number below is post-fix.

*The ATM±3 download is still running, so option-side counts grow slightly on
each re-run. Three re-runs so far have moved the conclusions by less than a
paise.*

---

## Stage 1 — does the trigger move the stock?

Excess = the trigger's return minus what the average stock did over the same
clock window. t is day-clustered, because 40 triggers on one morning are one
bet wearing 40 hats.

| horizon | ARC15MIN excess | t | NARC1HR excess | t |
|---|---|---|---|---|
| 30m | −0.03% | −1.65 | +0.02% | **+0.94** |
| 1h | −0.02% | −0.38 | +0.02% | +0.38 |
| 2h | −0.04% | −0.41 | +0.00% | −0.00 |
| EOD | −0.13% | −0.78 | +0.14% | +0.56 |
| 2d | −0.20% | −1.30 | +0.21% | +0.76 |

Nothing clears noise in either direction. NARC1HR leans very slightly positive,
ARC15MIN very slightly negative, and neither is distinguishable from zero. The
median trigger is **−0.13% by the close**.

This is "not demonstrated" rather than "disproved" — 23 days is a short sample.
But there is nothing here to build on.

---

## Stage 2 — buying the CE anyway

ATM call at the same entry bar, strike pinned at entry and followed as one
contract, 5-paise tick crossed each way plus 0.28% turnover tax. Both scans
pooled.

| exit | mean × | median × | win rate | t (day-clustered) | n |
|---|---|---|---|---|---|
| 30m | 0.961 | 0.967 | 35.2% | −4.18 | 781 |
| 1h | 0.941 | 0.949 | 30.9% | −5.52 | 754 |
| 2h | 0.905 | 0.932 | 27.6% | −7.57 | 699 |
| EOD | 0.784 | 0.829 | 11.8% | −6.33 | 482 |
| 2 days | 0.723 | 0.766 | 6.8% | −7.21 | 309 |
| 30% trail | 0.822 | 0.800 | 14.4% | −13.34 | 791 |

Excluding entries under ₹1 (stale five-paise quotes on thin contracts) changes
essentially nothing. Split by scan, ARC15MIN and NARC1HR land within a paise or
two of each other at every horizon.

Note the shape: **the loss grows monotonically with holding time.** That is the
signature of pure carrying cost. If the entry carried any directional edge,
holding longer would recover some of it. It doesn't.

Worth flagging: the 30% trail was the *best* exit in the base-rate study
(0.882× vs 0.773×). Here it is worse than simply getting out after 30 minutes.
A trail only helps when there are winners to ride.

### In rupees, at real lot sizes

| exit | trades | deployed | P&L | per trade | worst |
|---|---|---|---|---|---|
| same day | 482 | ₹99.2 lakh | **−₹18.8 lakh** (−19.0%) | −₹3,905 | −₹22,400 |
| hold 2 sessions | 309 | ₹66.9 lakh | **−₹17.4 lakh** (−26.0%) | −₹5,617 | −₹25,635 |
| 30% trail | 791 | ₹1.54 crore | **−₹27.3 lakh** (−17.7%) | −₹3,446 | −₹24,611 |

The median trade loses ₹2,920 on the trail.

---

## The control — is it the scan, or is it calls?

The stage-2 table alone does not convict the scan, because buying ATM stock
calls loses money from a *random* bar too. So: for every symbol-day that
produced a trigger, price the same trade from **every other bar of that same
session**. Same stock, same day, same contract, same exits. The only difference
is when you pressed the button.

13,800 priced entries across 608 symbol-days — 774 on a trigger bar, 13,041 on
every other bar.

| exit | trigger bar | random bar, same day | difference | t |
|---|---|---|---|---|
| 30m | 0.965 | 0.972 | −0.009 | −1.26 |
| 1h | 0.942 | 0.949 | −0.010 | −1.57 |
| 2h | 0.908 | 0.909 | −0.001 | +0.37 |
| EOD | 0.788 | 0.808 | −0.011 | −1.09 |
| 2d | 0.729 | 0.757 | −0.011 | −1.60 |
| 30% trail | 0.823 | 0.828 | −0.012 | −1.79 |

**The trigger adds nothing.** Every difference is inside noise, and the sign is
mildly *against* the scan.

This control also independently reproduces the base rate from the main study:
a random ATM call held two sessions returns **0.757×** here, against 0.77×
measured on a different date window and a different symbol set. Two independent
roads to the same number.

---

## Why it can't work: the hurdle

Median entry is a **₹30.12 premium on a ₹1,306 stock** — 2.3% of spot.

Costs are not the problem; tick plus tax needs only about +0.03% of stock
movement to clear. Theta is the problem. Working back from the measured decay
at a delta of roughly 0.5, break-even needs:

| horizon | stock must move | median trigger delivers | % of triggers that clear it |
|---|---|---|---|
| 30m | +0.18% | −0.02% | 31.3% |
| 1h | +0.27% | −0.06% | 27.9% |
| 2h | +0.44% | −0.07% | 25.3% |
| EOD | **+0.99%** | −0.13% | 26.7% |
| 2d | **+1.28%** | −0.03% | 29.5% |

Roughly **7 in 10 triggers fail to clear the bar**, and the median one moves the
wrong way. A directional option buyer has to be right about *magnitude*, not
just direction, and these scans are not even demonstrably right about direction.

---

## Strategies

**1. Don't run this workflow.** Chartink → CE is a −18% to −26% machine. This is
the direct answer to the question asked.

**2. Don't trade the trigger in the stock either.** Stage 1 is null. The scan
survives as a *watchlist* — a way to narrow 200 names to 30 — but not as a
signal with measurable expectancy.

**3. If you want option exposure on a view, the horizon has to be minutes.** The
one consistent structure in the data is that the loss scales with time held:
0.961× at 30 minutes against 0.723× at two days. Nothing here makes 30 minutes
*profitable* — it just loses least. That is a reason to shorten holds on
positions you take for other reasons, not a strategy in itself.

**4. The other side of this table is the interesting one, and I have not tested
it.** A random ATM call returns 0.757× over two sessions. That is the buyer's
result; the seller's side of the same trade is where the number points. I am
explicitly **not** recommending naked short calls — the risk is unbounded and
the P&L already on record makes that unwise. The honest version is a
defined-risk spread (bear call / bull put), which caps the loss and gives up
most of the premium. **It is untested here**, because the cache is ATM-only and
a spread needs a second strike. `research/fetch_otm.py` is downloading ATM±3
now; that is what would make it testable.

**5. The one live buy-side lead is unchanged and it is not this.** Expiry-day
ATM calls on a 30% trail cleared 1.041× over 23 expiry sessions. It has a
0.847× median and a 26% win rate — a positive-expectancy lottery, not something
to size up. It remains the only rule in this whole body of work to clear
break-even.

---

## What would change my mind

- **More trigger history.** 23 days is short. Stage 1 is "not demonstrated",
  not "disproved". A year of exports would settle it.
- **The OTM download finishing.** Everything above is ATM. Cheap OTM calls have
  different arithmetic — a much lower hurdle in rupees, a much lower hit rate.
- **A scan that selects for magnitude, not direction.** The hurdle table is the
  design spec: a useful scan for option buying needs to find +1% by the close,
  not just "up". Nothing tested so far does that.
