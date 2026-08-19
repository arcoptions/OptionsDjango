# A stock-option buying strategy: what the data supports, and what it refuses

Built 2026-08-19. Files: `research/breakeven_screen.py`, `research/vol_spike_calls.py`,
`research/stock_orb_strategy.py`, `research/friction_by_symbol.py`, `research/cheap_toll_bite.py`.

This is the answer to "come up with a proper options buying strategy based on your
own thinking and validate it with the data you collected." It is built from
measurements rather than from a hypothesis, and it ends somewhere I did not
intend when I started.

---

## 1. The decision rule, which is the actual deliverable

Everything below reduces to one inequality. A bought call pays if and only if

> **mean tradeable move in the underlying  ≥  (toll + decay × bars) ÷ elasticity**

All four quantities are now measured, per underlying, rather than assumed:

| quantity | how it was measured | value |
|---|---|---|
| toll | live chain capture, near-ATM, per symbol | **2.01%** (cheap head) / 7.3% pooled |
| decay | ATM call, intraday, strike-change guarded | **−0.142% per 15-min bar** |
| elasticity | 829,299 paired stock/option bars | **21.5x** stocks / **86x** NIFTY |
| the bar it produces | | **+0.106%** per 30-min hold |

This is worth keeping regardless of the verdict below, because it turns "is this
strategy any good" into arithmetic that can be checked in an afternoon.

**Elasticity is a property of stock options as a class, not of the names tested.**
Across all 174 underlyings with a measured toll — 1,783,527 paired bars — it is
flat at 23–25x in every toll band, on a premium of ~2% of spot everywhere:

| toll band | names | elasticity | break-even | vs the +0.092% best signal |
|---|---|---|---|---|
| ≤2% | 16 | 25.2x | 0.074% | *clears on paper* |
| 2–3% | 16 | 25.3x | 0.113% | short |
| 3–5% | 50 | 24.3x | 0.177% | short |
| 5–8% | 39 | 23.2x | 0.261% | short |
| >8% | 52 | 24.1x | 0.577% | short |

So the verdict below is not an artefact of the 32 names it was developed on. Only
the toll varies, and the toll is what the bar is made of.

---

## 2. Direction is worth exactly nothing

Fourteen intraday signals, screened against a **same-instant cross-section**
(every other symbol trading in that bar), with day-clustered t-statistics and --
critically -- **a null row: every eligible bar, no signal at all.**

| 60-min hold | vs market | t(day) |
|---|---|---|
| **(null) every eligible bar** | **+0.019%** | +21.25 |
| volume spike 3x, bar up | +0.023% | +1.32 |
| ORB up | +0.020% | +1.44 |
| gap down, still below open | +0.018% | +10.29 |

Every signal lands within a whisker of the null. **Nothing beats doing nothing.**

An earlier version of this screen used a same-symbol-same-day control and produced
gap-down +0.328% against gap-up −0.357% at t≈60. Those are mirror images, which
was the tell: the control set for one signal was mostly the other signal, and a
symbol-day only entered the comparison if the stock crossed back over its opening
price — so bars below the open were selected on having recovered. **That is
conditioning on the outcome, the same trap as "never added" meaning "never fell
25%".** The null row now anchors every table so this cannot recur silently.

---

## 3. Motion is worth a great deal, and it is one-sided

The same screen, asking the question a call actually cares about — the right tail,
not the mean, because a call's loss is capped at the premium and its gain is not.

| 30-min hold | P(+1%) | lift | P(+2%) | lift | t(day) |
|---|---|---|---|---|---|
| **(null) every eligible bar** | 1.62% | 1.00x | 0.15% | 1.00x | — |
| volume spike 3x, bar up | **3.57%** | **2.71x** | **0.83%** | **5.51x** | +9.19 |
| new session high | 3.06% | 1.83x | 0.44% | 2.69x | +14.95 |

And it is genuinely directional, not just volatility: on the cheap-toll names the
right tail lifts **3.27x against a left tail of 2.71x — a ratio of 1.21.**

That 2.71x is the same number the pre-move study found for 10% moves. **Motion is
predictable; direction is not.** This is the most robust positive finding in the
whole programme and it survives every deconfound thrown at it.

---

## 4. And it still does not pay. Every construction loses.

| construction | trades | mean | its control |
|---|---|---|---|
| spike up, stop 30% / trail 20% | 660 | −7.21% | −6.01% |
| spike up, **no stop**, 30-min hold | 660 | −2.49% | −1.57% |
| spike up, no stop, DTE 0–5 | 101 | −5.11% | −5.24% |
| spike 3x AND range 2x wide | 167 | −4.36% | **+0.35%** |

**In every single case the signal is indistinguishable from, or worse than, a
random bar on the same symbol-days.** Split-halves disagree. The one thing that
helped was removing the stop, worth **4.7 percentage points** — because a stop
caps the loss at 30% instead of 100% while forfeiting the right tail, destroying
the convexity that was the only reason to buy. Every prior test in this programme
carried a stop.

**The last cell standing was tested and it also fails.** The 16 names at toll ≤2%
(median 1.74%: ADANIPOWER, BEL, BHARTIARTL, BHEL, BOSCHLTD, BSE, DIXON, HDFCBANK,
INDIGO, INFY, LT, NAUKRI, PAYTM, RELIANCE, TIINDIA, VOLTAS) are the only cell whose
arithmetic clears. On those names the signal delivers **+0.017% to +0.071% against
the 0.074% needed** — short even there — and priced on the option it is **−1.59%
against a −0.55% control**, with both halves agreeing.

One row of that test is instructive: `spike 3x AND range 2x wide, hold 4 bars`
returns **+4.97% mean on a −4.43% median with t 0.60** — one or two outliers on 95
trades. Quoted as a mean alone it would read as a discovery. **Nothing in this
report rests on a mean without its median and its clustered t.**

---

## 5. Why: NIFTY's edge is leverage, not signal

| | premium/spot | elasticity | toll | break-even |
|---|---|---|---|---|
| **NIFTY near-ATM** | **0.581%** | **~86x** | 1.63% | **~0.02%** |
| stock near-ATM | 1.951% | 21.5x | 2.01% | 0.106% |

**NIFTY options are four times more levered and cost less to trade, so their bar
is roughly five times lower** — and NIFTY's opening-range breakout clears it
comfortably, while individual stocks *mean-revert* after breaking their opening
range (−0.014%, t −4.56, no options involved). The architecture does not fail to
port because the signal is worse. It fails because the instrument is worse.

Days-to-expiry moves stock elasticity a long way (49.2x at DTE 0–2 against 16.4x
at DTE 18–24, a genuine finding — **every earlier test pooled these, and the
untradeable regime has 5x more bars**), but the expiry window still loses on the
option and still ties its control. Leverage bought there is paid for in decay.

---

## 6. What to actually do

**Trade the NIFTY rule. It is already shipped, it wins 66.7%, and section 5 is
the explanation of why it works** — a 7.6% bite against a 1.63% toll on an 86x
instrument.

**Do not buy individual stock calls on any signal in this dataset.** Not because
stocks are unpredictable — motion is predictable at 2.71x, and that is real — but
because the bar is +0.106% and the best signal delivers +0.092%, and the gap does
not close under any exit, strike, or expiry tested.

**What would change the answer**, stated so it can be checked rather than argued:
a signal delivering **> +0.11% mean tradeable move over 30 minutes**, or an
underlying quoting **< 1% near-ATM toll** with stock-like tails. Both are
measurable in an afternoon with the rule in section 1.

## What remains optimistic, stated rather than buried

15-minute bars, not the 1-minute the NIFTY engine uses. Fills at the bar open with
the measured round trip charged, no impact and no rejections. One live chain
capture sets the per-symbol toll, so it ranks symbols but cannot speak to how that
ranking moves across regimes. Whole-lot capital is not modelled in sections 2–4;
where it was modelled, a near-ATM lot cost a median ₹26,250 and ₹13,000 bought one
only 6.1% of the time.
