# The shipped strategy does not transfer to SENSEX

*Measured 18 August 2026. `research/sensex.py`, `research/sensex_why.py`.
Companion to `INTRADAY_REPORT.md` and `STRATEGY.md`.*

`INTRADAY_REPORT.md` closed the stock-option programme with a decomposition
rather than another null: buying an intraday option is **+1.02% gross** and
loses because the round trip costs **1.64%**, two thirds of it bid-ask. That is
a design rule as well as a post-mortem — it says a shippable option trade needs
a high premium, tightly quoted — and it made a prediction. SENSEX ATM premium
averages ₹455 against NIFTY's ₹123 median. Four times the cushion. The
architecture should survive there.

It does not. This report is the test and the diagnosis.

---

## 1. The result

The shipped config, copied without a single refit. Only two things changed, and
both are facts about the instrument rather than choices: lot size (20 on SENSEX,
65 on NIFTY) and the underlying passed to the loader. NIFTY is re-run over the
SENSEX cache's own window, because comparing against the headline +38.6% would
confound the instrument with the regime.

| Same window, Feb–Aug 2026 | n | Win | Net on ₹1L | Max DD | Net÷DD | Pts/trade |
|---|---|---|---|---|---|---|
| **SENSEX** | 45 | **46.7%** | **−6,604** | 14,577 | **−0.45** | −6.18 |
| **NIFTY** | 42 | **69.0%** | **+29,646** | 4,538 | **+6.53** | +9.86 |
| *NIFTY full sample (context)* | 51 | 66.7% | +38,626 | 5,114 | 7.55 | 9.14 |

SENSEX loses money, wins less than a coin, and carries three times the drawdown.

## 2. It is not the entry

The obvious explanation is that the breakout signal misfires on SENSEX. It does
not. The two indices correlate above 0.95 and the signal fired on **23 common
sessions**. On those shared days:

| Days both indices fired | n | Mean R | Win |
|---|---|---|---|
| SENSEX | 29 | **−0.13R** | 44.8% |
| NIFTY | 34 | **+0.63R** | 73.5% |

Same signal, same day, same market, opposite outcomes. That rules out the entry,
the direction and the regime — all three are shared. What differs is the
**option's own price path**, which is what the exit reads.

The tell is the stop. SENSEX takes the full 10% stop **45.1%** of the time
against NIFTY's **25.6%**.

## 3. The obvious fix was wrong, and the measurement says why

Hypothesis: a 10% stop is not a statement about risk, it is a statement about how
far an option wanders in a session. It was fitted on NIFTY. If SENSEX options are
more percentage-volatile, 10% is simply tighter there and the architecture is
mis-scaled rather than broken.

Measured directly on the contracts, independent of the strategy:

| Median full-session ATM range | as % of the open | 10% stop, in days-of-range |
|---|---|---|
| SENSEX | **28.0%** | 0.36 |
| NIFTY | **36.6%** | 0.27 |

**The opposite of the hypothesis.** SENSEX options wander *less*, so a 10% stop
is the *wider* of the two in volatility-adjusted terms — and it still gets hit
nearly twice as often. The stop is not mis-scaled. SENSEX options simply fall
after this signal.

## 4. The stop sweep is a refit, and it was rejected on a rule set in advance

The decision rule was stated before the sweep ran: *a change that rescues SENSEX
by degrading NIFTY is a refit and is rejected; only a change that helps both is a
better parameterisation of the same idea.*

| Stop | SENSEX net | NIFTY net |
|---|---|---|
| **10%** (shipped) | −6,604 | **+29,646** |
| 15% | +11,904 | +1,215 |
| 20% | +8,875 | +3,010 |
| 25% | +11,103 | −2,661 |

Every stop that makes SENSEX profitable costs NIFTY 96% or more of its return.
**The two indices have opposite stop gradients** — NIFTY's edge is maximised
tight, SENSEX's wide. There is no shared parameter, so this is not one
architecture with a mis-set dial. It is the same rule meaning different things on
two instruments.

The SENSEX cells at 15%+ are **not claimed as an edge.** They were never run
against a random-entry control, they rest on 21–38 trades in 121 sessions, and
71.4% win at +0.16R mean is the signature of winning small and losing big. They
are reported so the rejection is visible, not as a candidate.

## 5. What this does and does not say about the live NIFTY strategy

It is a genuine caution and should be recorded as one: **the architecture is not
instrument-general.** "It works on NIFTY" is a weaker claim than "this is a
robust way to trade index options," and until today the second was the implicit
assumption.

It is not evidence that NIFTY is overfit, and one check was run to make sure.
NIFTY's sharp optimum at exactly 10% looks like a fitted cliff. It is not:

- The **trade count** collapse is position sizing, exactly as `STRATEGY.md`
  claims. Raw signals barely move as the stop widens (43 → 42 → 41 → 39) while
  the trades the account can actually fund collapse (42 → 27 → 14 → 5). At a
  fixed 2% risk budget a wider stop buys fewer lots, and below one lot the trade
  is skipped. That is arithmetic, not fitting.
- The **R-multiple** decay (+0.50R → +0.09R → +0.03R) is the trail, not the
  stop. `trail_gap_r = 0.7` defines the trail as 0.7 × risk, so widening the stop
  widens the trail by construction and the trail is where the edge lives. The two
  parameters are coupled through R and cannot be read as independently fitted.

So the 10% is load-bearing for a reason that was already documented. But it is
load-bearing *on NIFTY*, and SENSEX now shows it does not carry elsewhere.

## 6. Verdict

**SENSEX is rejected.** Not tradeable on the shipped rules, and not rescuable
without a refit that destroys the strategy it was copied from.

The friction rule from `INTRADAY_REPORT.md` survives but is narrowed. A high
premium is **necessary** — it is why stock options cannot work at ₹21 — and it is
**not sufficient**, because SENSEX has four times the premium cushion and still
loses. Cheap friction lets an edge survive; it does not create one.

One thread remains genuinely open and is not asserted either way: SENSEX has 121
sessions against NIFTY's 246, on a single six-month window that was mostly one
regime. A longer SENSEX history could change this. It would need to change it by
a lot.

---

## Method notes

- **Nothing was refit.** Entry rules, exit, stop, trail, windows and the ₹100
  premium floor are the shipped values.
- **The ₹100 floor is inactive on SENSEX, not transferred.** It is a costs
  finding calibrated to a ₹123 NIFTY premium; at ₹455 it passes essentially
  everything. A floor sweep (₹100/200/300/400) was run and every cell loses
  money (−6,604 / −3,731 / −1,710 / −7,415), so the inactivity does not hide a
  result.
- **Option volatility is measured on the contracts, not inferred from trades**,
  so it cannot be contaminated by the strategy that is under test.
- **The bid-ask sensitivity was run and is not the story**: SENSEX is already
  negative at a zero spread (−6,604), so no spread assumption rescues or
  condemns it.

Files: `research/sensex.py`, `research/sensex_why.py`.
