# Defined-risk spreads: the last lead, and how it died

*Measured 18 August 2026. Companion to `STOCK_OPTIONS_REPORT.md` and
`CHARTINK_CE_REPORT.md`.*

Buying stock options is a null — 0.77× per two-day hold, measured twice on
independent samples. That number is an argument for being on the **other** side
of it, and a naked short call is not on the table, so the only responsible way
to take that side is a **vertical call spread**: sell the ATM call, buy the
ATM+1, collect a credit, cap the loss at the strike gap minus the credit.

The cache already had what that needs — 594,196 ATM/ATM+1 call pairs quoted at
the same instant, 189 symbols, 368 sessions, Feb 2025 → Aug 2026 (524,288 after
the quality filter). No waiting on a download.

**It does not work.** It loses 5–15% of the capital at risk, at every horizon,
in 15 of 18 months. What is worth your time is not that verdict but *how the
first version of this study said the exact opposite* — because the mechanism
that fooled it will fool anything else built on this data.

---

## The short version

For most of a day this study had a strategy. Short ATM/ATM+1 call spread,
entered within a week of expiry, squared off at 14:00 on expiry day: **+18.2% of
risk, 8 of 8 expiry cycles profitable, t = +3.03, +₹15.2L on ₹68.8L of margin.**
It had survived a non-overlap test, a cost decomposition, a
one-trade-per-symbol-expiry deduplication, and a check that it was not a
disguised direction bet.

It was survivorship bias, and the bias was 21 percentage points wide.

The table below isolates that one effect: both columns are the *fixed* code —
same trades, same dedup, same after-entry exit rule — differing only in whether
an exit that could not be priced is dropped or valued at intrinsic.

| Short ATM/ATM+1 call spread, 0–7 DTE | Quoted only | Exits repaired |
|---|---|---|
| Square off expiry day 14:00 | **+16.35%** | **−4.52%** |
| Ride into the bell | +23.83% | −6.95% |
| Day-clustered t | +8.04 | −1.33 |
| Worst 5% of trades | −30.7% | −104.8% |
| Expiry cycles profitable | 7 / 8 | 3 / 8 |

Be precise about what the right-hand column now says. At t = −1.33 — and −0.46
once deduplicated to one trade per symbol-expiry — **this slice is a null, not a
demonstrated loss.** The strategy is dead either way, because a credit trade that
cannot be distinguished from zero before slippage is not worth the assignment
risk in section 4, and the slippage table kills what is left: −7.96% at a 5-paise
half-spread becomes −16.74% at ₹1.00, with only 77% of entries still openable.
But the honest reading of the expiry-week trade is *no measurable edge*, where
the pooled study below is *a measurable loss* at t −4 to −8. The corporate-action
guard removed 3.00% of imputed exits here (22,074 of 736,243), a higher rate than
the 1.17% it removed from the pooled five-day sample.

The pooled study reverses just as hard, and takes its headline finding with it.

| Held 5 sessions, by DTE at entry | Quoted only | Exits repaired |
|---|---|---|
| 0–7 days | +34.39% | −8.40% |
| 8–14 days | +10.83% | −11.79% |
| 15–21 days | −0.62% | −21.82% |
| 22+ days | −4.41% | −8.75% |

The left column is a clean monotonic theta gradient — exactly what option theory
predicts, which is why I chased it. The right column is noise around a loss.

---

## 1. The mechanism

Dhan's rolling feed is **ATM-relative**. `relative_strike='ATM'` does not name a
contract; it returns whatever strike was at the money on that bar. This was
already known and already guarded — both legs are pinned as fixed contracts at
entry and followed as real orders would be.

What was *not* guarded is the consequence for **exits**. A strike pinned at entry
only stays in the cache while spot stays near it. Walk away from the strike and
the contract keeps trading on the exchange but stops appearing in our data.

So an exit that fails to price is not missing at random. It is missing precisely
because **the market moved away from the strike** — and for a short call spread,
spot running up is the losing direction. Dropping unpriceable exits therefore
deletes the losers, and only the losers.

How much of the sample that quietly removes:

| Exit horizon | Entries with both legs still quoted |
|---|---|
| 2 hours | 56.2% |
| End of day | 34.9% |
| 2 days | 25.1% |
| 5 days | **15.5%** |
| 5 days, 0–7 DTE bucket | **14.3%** |

The `+34.39%` headline was computed on 2,429 trades. There were 16,978.

The dropped trades finish further in the money than the kept ones, which is the
fingerprint: at the closing bell the median kept trade lands **+0.04** strike
gaps above the short strike, the median dropped trade **+0.57**. Above +1.0 the
spread is at its maximum loss.

## 2. The repair

At expiry an option has no time value left, so it **is** its intrinsic value —
`max(spot − strike, 0)`. A vanished leg can therefore be valued from spot alone,
with no quote at all, and every trade stays in the sample. Coverage goes from 22%
to 99.9%.

That premise is load-bearing enough to be worth checking rather than asserting.
`bell_intrinsic_check.py` compares quote against intrinsic on the 3,482
strike-quotes that *were* still present at an expiry-day bell:

- median quote − intrinsic = **+₹0.05**, exactly one tick
- 63% within 50 paise, 77% within ₹2
- at the money, median error +₹0.05; in the money, −₹0.04

The rule holds where it can be tested, and the two legs' errors largely cancel in
the debit. Intraday it is a floor rather than an equality — but the floor binds
hardest exactly where the trade is lost, spot far above both strikes with the
spread pinned at max loss, which is the region the missing quotes live in.

## 2.5 The repair's own bug

Imputing at intrinsic introduces a fault of its own, and it took a second pass to
find. A **split or bonus re-bases `spot` but not the recorded `strike`**. After
the action the feed reports the new-scale spot while the strike written on the
old rows is still on the old scale, so `max(spot − strike, 0)` subtracts two
different price scales and returns nonsense. It lands almost entirely on imputed
rows, because the corporate action is exactly what stops the old strike being
quoted.

It is rare and it dominates: **1.17% of the 5-day exits carried 70% of the
loss** — −22.3% of risk with them in, −8.1% with them out.

The cut has to be taken on **the strike's distance from spot, never on how far
spot moved.** Filtering on the move would delete adverse outcomes and manufacture
a positive — the same mistake section 1 is about. Distance from a strike that was
at the money at entry is a scale check, not a market outcome.

`MAX_DRIFT = 0.30` in `option_spreads.py::drop_rebased_strikes`, applied per
horizon rather than per row (a 2-hour exit can be clean on a day whose 5-day exit
lands the far side of the split). The threshold is not tuned — 5-day ROI runs
−28.03% (no cut) → −16.93% (0.50) → **−15.41% (0.30)** → −15.24% (0.15), and a
plateau is what a clean filter looks like: once the actions are gone there is
nothing left to remove. Strikes the exchange was still quoting reach only 0.025
drift at the 99.9th percentile, so 0.30 is loose by an order of magnitude.

Dhan's *equity* series is split-adjusted, so this is an option-cache problem
only: the same guard over 69,741 stock-days blanks 18 rows.

**It is a problem for imputed rows, and essentially only for those.** Raw bars
are barely touched — 0.063% of call bars and 0.827% of put bars exceed 30% drift
— because a re-based spot makes a quote contradict its own intrinsic, and any
ordinary sanity check catches that. The two earlier buying studies were re-run
against the guard on that logic and both hold: the ATM buying null keeps
**279 contaminated rows in 1,747,268** (0.016%, its existing
`close ≥ intrinsic − 0.10` filter having already removed 96.5% of them), and the
Chartink CE overlay has **0 of 791 trades** whose followed strike ever drifts
that far. Imputation is what made this study vulnerable: a synthetic intrinsic
has no quote to contradict.

## 3. What the repaired strategy actually does

Short ATM/ATM+1 call spread, all 189 symbols, 5-paise tick each leg each way plus
0.28% turnover:

| Exit | ROI on risk | Win % | Worst 10% | t (day-clustered) | n |
|---|---|---|---|---|---|
| 2 hours | −5.42% | 37.2% | −102.7% | −8.03 | 516,958 |
| End of day | −6.94% | 43.5% | −103.7% | −4.19 | 505,419 |
| 2 days | −9.73% | 44.5% | −104.5% | −5.17 | 475,408 |
| 5 days | −15.24% | 44.5% | −106.4% | −6.29 | 386,357 |

- **3 of 18 months profitable.**
- One entry per symbol per week, so no two holds share a path: **−14.7% to
  −17.8%** at 5 days, at every one of four entry clock times, t −2.04 to −3.18.
- At real lot sizes, the 5-day version is **−₹44.7 crore on ₹464 crore of
  risk**. (Not a tradeable size — it is every entry the data allows, and it is
  there to show the sign is not a small-sample artefact.)

An earlier draft of this table read −18.77% to −28.49%. That was the second
repair in section 2.5 not yet applied: the loss was roughly twice as deep
because 1.17% of the exits were pricing a post-split spot against a pre-split
strike. The verdict does not move — every horizon still loses, every t is still
between −4 and −9 — but the magnitude does, and the smaller number is the
correct one.

**And it is a direction bet after all.** Correlation between monthly ROI and the
monthly spot move is **−0.73**. The earlier +0.01 — the check that had convinced
me it was structural rather than directional — was itself survivorship: with the
adverse moves deleted, there was no direction left to correlate against.

## 4. Delivery, which would have killed it anyway

Indian stock options are **physically settled**. A short call left in the money
at expiry is an obligation to deliver shares, not a cash debit, and STT on
exercise is charged on intrinsic value — which a flat 0.28% turnover model does
not capture at all.

Measured on every entry whose expiry-day bell exists:

- spot finishes above the **short** strike **53.3%** of the time
- above the **long** strike too, so both exercise and the deliveries net: **30.6%**
- the dangerous band — short in the money, long expiring worthless, naked
  delivery: **22.7%**

Better than the 54% the broken version implied, but still roughly one trade in
five facing assignment. Squaring off before the bell avoids all of it, which is
why every exit tested here does so.

## 5. What is left

**The mirror is not a lead.** Negating the short spread makes the long (debit)
call spread look positive — +₹4.69 to +₹7.12 per spread, 52–56% win. That is the
same direction bet read backwards, over a sample in which Indian stocks rose.
With correlation −0.73 to the spot move, it is a bullish position, not an edge,
and it has not been tested as one.

**Bull put spreads were testable after all, and they lose too.** An earlier draft
of this report said they were untestable on "about 2,070 PUT bars." That was my
own query, not the cache. The cache holds **1,080,003 ATM/ATM+1 put bars across
189 symbols**, which pair into 439,158 raw spreads and **352,066** after the same
quality filter (175 symbols, 163 sessions). Sell the ATM+1 put, buy the ATM put —
the mirror trade, long delta where the call spread is short:

| Exit | ROI on risk | Win % | Worst 10% | t (day-clustered) | n |
|---|---|---|---|---|---|
| 2 hours | −22.90% | 34.8% | −103.5% | −7.65 | 346,997 |
| End of day | −21.93% | 39.6% | −104.8% | −8.03 | 338,089 |
| 2 days | −19.57% | 43.2% | −105.7% | −6.42 | 315,516 |
| 5 days | −15.37% | 48.8% | −106.9% | −3.48 | 245,065 |

This is the result that finishes off the direction story. The call spread lost
with **short** delta and correlated **−0.73** to the monthly spot move. The put
spread loses with **long** delta over the same rising sample, correlating
**+0.42**. Both sides of the same trade cannot both be a badly-timed direction
bet; what they have in common is the friction.

**The box measures that friction directly, with no model.** Short call spread
plus short put spread on the same symbol at the same instant with the same strike
gap is a **box**: short a forward at the lower strike, long one at the higher,
worth exactly the gap at every instant by put-call parity, with no delta, theta
or vega left in it. 334,637 such paired entries exist (172 symbols). The two
credits sum to **₹18.25 against a ₹20.00 median gap** — a median shortfall of
**₹0.25, 2.45% of the gap, before a single day passes.** Held, the box loses
11.5%–14.1% of risk, and its correlation to the spot move decays +0.60 → +0.30
across the four horizons as the legs' deltas finish cancelling. The box column is
a friction measurement, not evidence about premium; the call and put columns
either side of it are the verdicts.

**Everything in the stock-options programme is now a null**: buying ATM options
(0.77×), the Chartink CE overlay (−17% to −26%), defined-risk call spreads
(−5% to −15%), and their put mirror (−15% to −23%). The one rule still standing
anywhere is the NIFTY expiry-day gamma lottery at 1.041×, and its median trade is
0.847×.

---

## Method notes

Two other bugs were found and fixed in the same pass. Neither changes the
verdict, but both would have distorted it:

- **`groupby.first()` is column-wise, not first-row.** It takes the first
  non-null value of each column independently, so every
  "one trade per symbol-expiry" row was a composite — entry legs from one bar,
  exits from another. Now `head(1)`.
- **Expiry-day clock exits were not required to fall after the entry**, so a
  15:00 entry could be handed an exit priced at 10:00 that same morning.

Files: `research/option_spreads.py` (pooled),
`research/spread_near_expiry.py` (0–7 DTE stress test),
`research/bell_intrinsic_check.py` (validates the repair),
`research/spread_tables.py` (page-ready aggregates, derived from the above).
Outputs: `spread_summary.csv`, `spread_money.csv`, `spread_months.csv`,
`spread_cycles.csv`, `spread_slippage.csv`, `spread_dte.csv`,
`spread_exits.csv`, `spread_trades.csv`.
