# Predicting the move, and trying to buy it: the CE side

*Measured 18 August 2026. Companion to `STOCK_OPTIONS_REPORT.md`,
`CHARTINK_CE_REPORT.md` and `SPREAD_REPORT.md`.*

The brief was three steps: list the tracked stocks, pull a year of their tick and
option history, then find what happens before a 5–10% move and buy a CE into it,
hoping for 2–10x.

Steps 1 and 2 are done — 189 symbols, 3,015,526 call bars across three strike
rungs, Feb 2025 → Aug 2026. Step 3 splits cleanly into two questions that turn
out to have opposite answers, which is why it took a while to get a straight
verdict:

- **Can you predict the move?** Yes, and it survives every deconfound.
- **Can you predict the *side*?** No. AUC 0.510.
- **Can you buy the move without knowing the side?** No. That is this report.

---

## 1. What the predictor actually does

A gradient-boosted model over 34,776 stock-days, fit out-of-sample on three
date-split folds with a 5-day embargo, predicting whether a stock moves 10% in
either direction over the next 5 sessions. Top decile by score = "signal".

It works. The lift is **2.7x** over the base rate, and it survives controls for
sector, market regime, volatility level and the stock's own recent range. The
signal is essentially *distance to the edge of the 20-day range*: stocks about to
move are stocks already pressed against a boundary.

The problem appears immediately after. Split the same prediction into up-moves
and down-moves and the model is a coin: **AUC 0.510**. It knows something is
coming; it does not know which way. And implied volatility already prices the
motion at 1.02–1.06x, so the thing the model sees is not a secret.

That leaves the CE trade as a knowing bet on the up side of a move whose
direction is unpredictable — run anyway, because 61% of the 10% movers in this
sample were up moves (drift plus the sample's own bull market). If a CE cannot
beat a non-signal CE *with that tailwind*, nothing downstream is worth pricing.

## 2. The trade, and what it is measured against

Buy one call at the first bar of the session after the signal, hold 5 sessions,
5-paise tick each way plus 0.28% turnover. Compared **not against zero** but
against the identical trade on non-signal days — buying premium on this cache
loses before any signal (0.77x per two-day hold), so the gap between the columns
is the whole finding.

Run at three rungs of the strike ladder, because the cheap-strike argument is the
one thing the ATM test left open: a 10% move turns a 2.4%-of-spot ATM call into
roughly 3x, where a cheaper contract further out converts the same move into
much more.

| | ATM | ATM+1 | ATM+2 |
|---|---|---|---|
| Trades | 19,803 | 11,623 | 18,050 |
| Symbols | 187 | 185 | 115 |
| Median premium | ₹25.5 (2.52% of spot) | ₹17.2 (1.98%) | ₹15.6 (1.62%) |

## 3. The signal does not survive at any rung

Every cell is **signal minus non-signal** at that rung.

| | ATM | ATM+1 | ATM+2 |
|---|---|---|---|
| Per-session median | −8.1pp | −24.1pp | −23.5pp |
| Positive sessions | −3.7pp | −8.3pp | +0.5pp |
| ≥2x on a perfect exit | +2.7pp | +8.9pp | +3.3pp |
| ≥3x | −3.6pp | +0.6pp | −0.8pp |
| **≥5x** | **−3.4pp** | **−0.8pp** | **−5.0pp** |
| ≥10x | −1.2pp | +0.3pp | −2.8pp |
| Day-clustered t | −0.92 | −0.64 | −0.47 |

Read the bottom rows first, because they are the ones the brief was about. **The
signal makes 2x more likely and 5x less likely.** Negative at ≥5x at all three
rungs. The model finds stocks that move — but it finds moves of a size that gets
you to 2x, not to the far tail. The 10x lives in moves nobody saw coming, which
is exactly the population a predictor removes you from.

### The pooled column disagrees, and it is wrong

`sum(pnl)/sum(cost)` reads **+19.0pp / +10.8pp / +12.3pp in favour of the
signal** — the one measure that makes this look like a strategy. It is a lottery
readout. On the 115 symbols common to all three rungs, the ATM signal column
pools to +4.7% and its **top 17 trades (1%) contribute +19.9pp of that**; without
them it is roughly −15%. At ATM+1 the +50.1% pooled figure is carried by **two
trades** contributing +21.9pp across 283.

The same split shows in mean-versus-median: equal-weighted mean is *positive* at
every rung in both columns (13.9% to 52.4%) while the median trade runs −37% to
−100%. That is not an edge, it is a payoff shape. The per-session median and the
day-clustered t are the honest readouts, and both are negative everywhere.

## 4. Why going cheaper cannot escape it

The premium row is the mechanism, and it is the cleanest number in the study.

| Premium as % of spot | signal | no signal |
|---|---|---|
| ATM | 3.5% | 2.4% |
| ATM+1 | 2.8% | 1.9% |
| ATM+2 | 2.4% | 1.6% |

The signal costs **+0.8 to +1.1 points of spot at every rung** — the skew tracks
it in lockstep, and moving out does not shake it off. Read the diagonal:
**an ATM+2 call on a signal day costs 2.4% of spot, which is precisely what an
ATM call costs on a non-signal day.** Going out two strikes buys back exactly
what the prediction costs you, and not one basis point more. That is the market
quoting the same information back.

## 5. The strike mechanism is real — and it is free

Worth separating, because it is the one genuinely positive finding here and it
has nothing to do with the predictor. Same 115 symbols, **non-signal trades
only**, so the signal is taken out entirely:

| Rung | Premium | ≥2x | ≥3x | ≥5x | ≥10x | Median trade |
|---|---|---|---|---|---|---|
| ATM | 2.45% | 28.9% | 17.6% | 7.8% | 2.5% | −45.7% |
| ATM+1 | 1.93% | 25.9% | 17.5% | 10.3% | 5.2% | −43.0% |
| ATM+2 | 1.56% | 27.1% | 19.0% | **12.2%** | **5.9%** | **−100.3%** |

Going out two strikes takes ≥5x from 7.8% to 12.2% and ≥10x from 2.5% to 5.9%.
The cheap-strike arithmetic works exactly as theory says, and it survives the
symbol control (all three rungs on the same 115 names).

And the right-hand column is what it costs: the median ATM+2 call finishes far
under water. You buy a 2.4x better shot at 10x by making the typical trade a
near-total loss. That trade-off is available on any random day to anyone; the
predictor does not improve it, it degrades it.

### It also survives a date control, and one number above is wrong

The symbol control is not the strictest one available, and it turned out to
matter. ATM+1 coverage is **thinned to 43–56% of ATM between Aug 2025 and Jun
2026** — 94% of the download windows for that leg failed on HTTP 504 — so the
three columns above are not drawn from the same calendar. Re-run on the 3,065
exact **(symbol, day)** triples quoted at all three rungs:

| Date + symbol matched | Premium | ≥2x | ≥5x | ≥10x | Median trade |
|---|---|---|---|---|---|
| ATM | 2.56% | 20.7% | 6.0% | 2.0% | −38.0% |
| ATM+1 | 2.02% | 21.9% | 8.4% | 4.2% | −41.6% |
| ATM+2 | 1.56% | 23.0% | **10.1%** | **6.1%** | **−57.6%** |

The gradient holds in both tails and in premium, attenuated but intact. The
mechanism is real.

> **Status note, 19 Aug 2026.** Both tables above are drawn from the rolling
> ATM±3 feed while its coverage gap is still being backfilled — the ATM+2 call
> leg landed 55,769 further bars on 19 Aug and the ATM−1 put leg is still
> running. Restricting to the 115 symbols common to all three rungs is what makes
> the first table's ATM+1 row read 10.3% / −43.0%; the same row computed over
> *all* symbols reads 9.6% / −65.0%. Neither unmatched figure is the one to
> trust, which is why the date-matched table is the one that carries the claim.
> These numbers are **not** to be re-derived until the download finishes: doing
> arithmetic on a partial, non-randomly-ordered download is the specific error
> that cost this programme a day (see `otm-strike-selection-bias`).
>
> This section is also now superseded in scope. It measures the ladder ~2.6% out
> of the money, which is as far as this feed reaches. The real deep-OTM cache
> built on 18–19 Aug reaches +25% on pinned, absolutely-struck contracts and
> settles the question this section could only gesture at — see
> `DEEP_OTM_REPORT.md`.

**The correction: the median ATM+2 trade is −57.6%, not −100.3%.** The
−100.3% figure came from sessions where ATM+2 was quoted and the nearer rungs
were not — precisely the far-drifted contracts that get imputed at intrinsic and
therefore score a total loss. Matching the calendar removes them. The claim that
"the median ATM+2 call finishes with no intrinsic at all" was an artifact of the
unmatched sample and is withdrawn; the median is bad, not total.

One limitation on the matched set, stated rather than buried: because the ATM+1
thinning is a time block, 136 of its 154 sessions fall in the first half of the
sample. It is a clean control on symbol and date, and a weak one on regime.

### Free is not the same as profitable

One number in the tables above looks like it says otherwise and needs killing
explicitly. Equal-weighting rupees across trades — the natural way to buy a
lottery deliberately — gives a **positive mean at every rung**: +26.6% at ATM,
+73.3% at ATM+1, +48.4% at ATM+2. Taken at face value that would contradict the
0.77x buying null outright.

It does not survive contact:

| Non-signal, 115 symbols | ATM | ATM+1 | ATM+2 |
|---|---|---|---|
| Equal-weighted mean | +26.6% | +73.3% | +48.4% |
| **Day-clustered t** | **−2.33** | **−0.78** | **−1.43** |
| Trimmed mean (drop top/bottom 5%) | −15.1% | −10.8% | −31.7% |
| Mean excluding the best 1% | +4.1% | +24.0% | +3.4% |
| Share of trades positive | 32.2% | 30.3% | 26.3% |
| Best 1% share of all gross gains | 35% | 32% | 39% |

The t-statistic is the one that matters, and it is negative at every rung. The
raw mean treats 16,123 trades as independent when they sit in 184 sessions —
and option winners cluster hard, because one market-wide rally pays hundreds of
calls at once. Cluster by day and the positive mean evaporates. Trim 5% off each
tail and every rung is negative. Roughly a third of the gross gains at each rung
come from the best 1% of trades.

So the strike mechanism converts moves into multiples exactly as advertised, and
still loses money doing it. This **confirms** the 0.77x buying null rather than
contradicting it, and extends it past its previous ATM-only scope.

## 6. Verdict on the CE side

**Null.** The predictor is real, the cheap-strike mechanism is real, and they do
not compose. The move is predictable, the side is not, the skew prices the
prediction at every rung, and the far tail the brief was aimed at prefers the
days nobody predicted.

Concretely, on the brief's own terms: there is no rung at which a signal-day CE
reaches 2–10x more often than a random-day CE. ≥5x is *negative* at all three.

## 7. What is not yet answered

**The PE side is not judgeable yet.** The put cache is ATM and ATM+1 only
(592,658 and 519,773 bars); ATM−1 holds 250 bars on one symbol and ATM−2 has not
started. Downloading. Given AUC 0.510 on direction, the prior is that a PE mirror
lands in the same place, but it has not been measured and will not be asserted.

**The far OTM tail is out of reach of this feed.** Even ATM+2 is only ~2.6% out
of the money on a 2.5-point strike ladder, at 1.62% of spot. The ₹0.70-premium
contracts where 8–10x actually lives need the ladder feed with real contract
security ids, not the rolling ATM±3 feed. That is the only remaining way to test
the brief as originally posed.

---

## Method notes

- **Corporate actions are cut on the spot series' continuity, not on strike
  distance.** The spread study cuts rows where the strike sits >30% from spot,
  which is safe there. It is **unsafe here**: this study deliberately selects
  stocks predicted to move 10%+, so a real 30% run in five sessions is exactly
  the trade being measured, and cutting on distance would delete the winners.
  Instead a corporate action is detected as a **bar-to-bar jump in spot above
  30%** — unreachable through a 20% daily circuit limit, and blind to the strike,
  the direction and the outcome, so it cannot delete a winner.
- **Missing exits are imputed, never dropped.** The feed is ATM-relative, so a
  pinned strike stops being quoted once spot walks away — and for a long call,
  spot walking up is the win. Dropping unpriceable exits deletes winners. Every
  missing bar is valued at `max(spot − strike, 0)`.
- **The rung is chosen by the feed's label at entry; the contract is then followed
  by absolute strike** across every offset, so a strike bought at ATM+2 stays
  visible as it drifts back through ATM+1 and ATM.
- Signal refit inside the pricing script, same folds and embargo, so the traded
  signal is provably the out-of-sample one.

Files: `research/premove_features.parquet` (features),
`research/premove_legs.py` (ATM CE/PE legs),
`research/premove_otm.py` (the strike ladder).
Outputs: `premove_otm.csv`, `premove_otm_summary.csv`.
