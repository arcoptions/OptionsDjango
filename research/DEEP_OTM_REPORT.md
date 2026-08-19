# The 2–10x question, answered on real deep-OTM contracts

*Measured 18–19 August 2026. Supersedes the strike-ladder sections of
`PREMOVE_REPORT.md`, which could only reach ~2.6% out of the money.*

You said: *"Are you saying we can't build a strategy to find 2-5X options? In
just HAL alone there were many entries which could have easily gotten us these
trades. There was a 10X trade too. You are doing something wrong. We don't have
to wait till expiry. You just have to find the right entry and right exit."*

Every factual part of that is correct. This report agrees with it and then shows
why it still does not produce money.

---

## First: you were right about HAL, and I had understated it

The HAL 25-Aug 5,000 call is in the cache now as a real pinned contract, security
id 97484, 49 trading sessions from 2026-07-07.

**It went ₹22.20 → ₹302.30. That is 13.62x, not the 8.96x I quoted earlier.**

There was no error of measurement in the earlier figure — it was the move to the
date I had then. The contract kept going. So the trade you pointed at is real,
it is bigger than I said, and nothing in this report claims otherwise.

The disagreement was never about whether these trades exist.

---

## The short version

**A 2x is present in 44.7% of contracts and captured by a placeable rule on 7.0%
of entries.** That ratio is the whole answer.

| calls, premium ≥ ₹2.50 | 2x | 3x | 5x | 10x |
|---|---|---|---|---|
| share of **contracts** whose low is followed by that multiple | 44.7% | 24.7% | 13.0% | **4.3%** |
| share of **entries** whose *best of twelve exits, chosen with hindsight* reached it | 19.9% | 8.4% | 2.8% | **0.1%** |
| share of **entries** under one rule you could actually place | 7.0% | 2.7% | 0.7% | **0.1%** |

*5,286 traded contracts, 54,742 entries, 181 symbols, 2026-05-27 to 2026-08-17.*

Read the 10x column. A 10x is sitting inside **4.3%** of contracts and is
delivered to **0.1%** of entries — a **43-fold** gap. And the middle row is the
important one, because it is not limited by the exit at all: even letting a
perfect oracle pick the best of twelve exit rules *after* seeing the whole path,
only 0.1% of entries reach 10x.

**So the exit is not what is missing.** Hindsight on the exit buys you 19.9%
against 7.0% at 2x, and buys you nothing at all at 10x. The binding constraint is
which contract you are holding on which day.

### Why the gap is so large

The median contract in this cache trades 11 sessions, and **its low arrives 0
sessions before its high** — that is, the typical deep-OTM call decays to its
lifetime low and then expires there. There is no "buy the dip and ride it" for
the median contract, because for the median contract the dip is the end.

The 13.62x on HAL is what the 4.3% tail looks like. It is genuinely there. It is
one contract out of 5,286, and you have to be holding it, in size, on the right
day, having not been stopped out of the eleven that looked identical at entry.

---

## Second: I built the predictor you asked for, and it works

This is not a case of the signal being too weak to test. Out of sample, on a
model that never sees the window it is scored on (5-day embargo, trained to
2026-05-20), predicting whether a stock moves 10% either way within five
sessions:

| selection | lift over base rate |
|---|---|
| top 10% | **2.36x** |
| top 5% | **2.39x** |
| top 2% | **3.12x** |

That is a real, deconfounded, out-of-sample edge on the *stock*. Priced onto
calls with a 30% trailing exit it moves everything in the right direction: the
pooled return goes 0.92x → 1.20x / 1.30x / **1.50x**, and the win rate goes
27.7% → **40.8%**.

Against a **same-session** control — comparing signal names only to other names
traded the same day, so it cannot be credited for simply picking good days — the
signal adds **+0.089x**, with a clustered t of +1.12.

**And the base is 0.904x. So 0.904 + 0.089 = 0.992x.**

The edge is real, it is measurable, it survives every control I could build, and
it lands **eight-tenths of one percent short of break-even**.

---

## Third: what ₹1 lakh actually does

Top-2% signal calls, five positions at 25% each, whole lots, cash locked from
entry to that position's own exit:

| period | final | return | trades |
|---|---|---|---|
| all 56 sessions | ₹210,507 | **+110.5%** | 22 |
| first half, from 2026-05-27 | ₹82,334 | −17.7% | 10 |
| second half, from 2026-07-08 | ₹519,651 | +419.7% | 18 |
| **excluding 24, 27, 29 July** | **₹30,841** | **−69.2%** | 21 |
| only 24, 27, 29 July | ₹164,511 | +64.5% | 5 |

**The strategy is three days.** Remove them and ₹1 lakh becomes ₹30,841.

I swept 20 parameter settings (1–8 positions × 15–100% sizing). All 20 are
profitable on the full window; **2 of 20** are profitable with those three
sessions removed. Do not read the first number as robustness — all twenty
settings re-slice the same three days. Robustness across parameters is not
robustness across time, and here the two disagree completely.

> **Corrected 19 August, and the correction runs against the strategy.** The
> bottom three rows previously read −49.1% / +345.2% / 3 of 20. Those came from
> *subsetting* the calendar, and `exit_i` is an offset into it: cut three sessions
> out of the middle and every position open across the cut releases its cash early
> while still booking the full multi-day return it was credited with. The
> degenerate case shows the size of it — subset to the three hot sessions alone and
> the calendar is three rows long, so ten-day holds all exit by row 3 and ₹1 lakh
> recycles into *eight* trades on three days. The rows above suppress entries
> instead, leaving the calendar and every holding period intact. Both constructions
> now print from `otm_portfolio.py` so the earlier figures stay traceable. The
> session count was also one high: 56, not 57.

Concentration says the same thing three more ways: drop the best 10% of trades
and 1.505x becomes 0.999x; the session mean beats 1.0 on 18 of 39; and those
three sessions rank 4th, 6th and 8th of 57 by share of names going on to move
more than 10%.

---

## Fourth: the finding that closes it

The obvious objection to everything above is that I was predicting the wrong
thing. "The stock moves 10%" is not the trade — it ignores what the option cost,
how far out the strike was, and how fast it decays while you wait. So I trained
directly on **the option's own net outcome**: walk-forward, expanding window,
10-session embargo (longer than the hold, or the label leaks), one contract per
symbol-session.

With a positive control run through the identical harness:

| label | pooled AUC | within-session AUC |
|---|---|---|
| **control: the stock moves +10% in 5d** | **0.703** | 0.645 |
| the trade made money | **0.519** | 0.525 |
| the trade doubled | **0.522** | 0.570 |

**The stock's motion is predictable. The option's profit is not.** Same features,
same folds, same embargo, same dedup — the only thing that changed is the label.

Selection is flat-to-negative in signal strength: the model's top 5% returns
0.839x per session against 0.852x for everything it scored. Its best picks are
its average picks. And the motion-trained model correlates with option P&L
*better* than the model trained on option P&L directly (Spearman +0.102 vs
+0.052), because the option outcome is mostly noise and training on it fits the
noise.

The scored window contains all three of the paying sessions (541 of 2,887
trades). It is not a model that never saw a good day. It saw them and could not
rank them.

---

## Why: the toll is premium-governed, and it is large

Measured off the live chain, not assumed — median round-trip spread by premium:

| premium | ₹0.05–0.5 | ₹0.5–1 | ₹1–2.5 | ₹2.5–5 | ₹5–10 | ₹10–25 | ₹25+ |
|---|---|---|---|---|---|---|---|
| round trip | **40.0%** | 29.5% | 16.0% | 9.9% | 8.7% | 6.8% | 7.3% |

**This is why HAL was payable and why it does not generalise.** Its 5,000 call was
7.8% out of the money — deep OTM — but cost ₹22.20 because the stock is ₹4,637,
putting it in the ₹10–25 bucket at a ~7% round trip. The *same* 7.8% strike on a
₹200 stock costs ₹0.80 and pays **29.5%**. Moneyness is a proxy for premium, and
a poor one.

Which kills the intuitive version of the idea outright: **cheaper is worse,
monotonically.** Net per session by premium runs 0.30x / 0.38x / 0.49x / 0.51x /
0.59x / 0.70x / 0.68x from ₹0.05 up to ₹25+. Buying cheap far-OTM lottery tickets
is the *worst* version of this strategy, not the best.

And 63.5% of listed strikes — 17,868 of the 28,118 asked for — never traded at
all. You cannot buy what does not print.

---

## What was tried and killed on the way

Recorded so none of it gets re-derived.

- **Far-OTM × signal.** The gross gradient is real and large (1.263 → 2.230 across
  moneyness, clustered t 2.18). But the per-session median stays flat and below
  1.0 throughout (0.786 → 0.879), so it is entirely pooled-mean tail. The put
  mirror fails hard, t −8.8 to −11.8 in every band.
- **21–35 days to expiry.** Looked like the first slice ever to clear 1.0 on all
  twelve exit rules. Killed by pairing: on the 10 sessions carrying both
  maturities the near leg is *worse* (−0.019x, t −0.70). It was the same late-July
  fortnight in disguise.
- **The put side, generally.** Calls beat puts structurally, not because the
  window was an up-market — the basket drifted only +2.21% with 50.8% of names
  up. The asymmetry is the move distribution (10% up-moves 5.1% vs down 3.1%) plus
  put skew. The signal makes puts actively *worse*, 0.76x → 0.62x, t −11.84.
- **Exit-side friction accounting.** Charging the sell side at the exit premium
  rather than the entry premium changes nothing (≤0.007x on any rule). The 7.3%
  round trip stands.

---

## What I would say if you asked me what to do

**Do not run this as a buying strategy at ₹1 lakh.** The honest expectation is
−69%, and the +110% version requires having been in the market for one specific
week in July.

Three things are worth knowing before deciding anything else:

1. **The exit is not the missing piece.** A hindsight-perfect exit lifts the 2x
   rate from 7.0% to 19.9% and lifts the 10x rate not at all. This was worth
   testing and it is now tested.
2. **The entry is not the missing piece either.** A predictor was built, it works
   (AUC 0.703 on motion, 2.36–3.12x lift), and re-targeting it directly at profit
   yields AUC 0.519. The predictable part of this problem is not the part that
   decides the money.
3. **The toll is the whole story**, and it is a property of the instrument rather
   than of anything I can model away. Two independent measurements — this cache
   and the intraday study — now put the gross edge and the friction within a
   percent of each other.

The one caveat that genuinely bounds all of this: **six weeks, one regime.**
Expired contracts return DH-907 and their security ids are published nowhere, so
a longer deep-OTM history on real pinned contracts is not obtainable at any
effort. This is not a limitation I can work around by trying harder.

Everything in this file is reproducible from `research/fetch_deep_otm.py`,
`deep_otm_base.py`, `otm_exits.py`, `otm_signal.py`, `otm_portfolio.py`,
`otm_relabel.py` and `otm_visible.py`.
