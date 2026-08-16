# Test Register — Research Tracking

Every test run, what it asked, what it found, and closure status. 

**Purpose:** Stop re-deriving dead ends; know which questions are closed.

> **The strategy was finalised on 2026-08-16.** The rules to trade, in
> executable form, are in **[`STRATEGY.md`](STRATEGY.md)**. Two parameters
> changed: the trail went 0.5R → 0.7R and the entry premium went ₹50–250 →
> ₹100 with no cap. Result on 246 sessions: 51 trades, 66.7% win, **₹38,626**
> on ₹1,00,000, maximum drawdown ₹5,114 — and **₹28,208 still standing after a
> ₹2 round-trip bid-ask**, which is why that band was chosen. The production
> path reproduces the research to the rupee.

---

## Entry Signal Tests

| test | question | key finding | closed? | ref |
|---|---|---|---|---|
| Spike anatomy | What happens just before a spike? Can we enter the spike and exit? | The spike is visible in 1-min OI rising, but in premium it's indistinguishable from other high-vol bars. The information is already in the 5-min bars we use. | ✓ | opening breakout signal |
| Reversal at OI support | Buy CE when near OI support, PE when near OI resistance. | OI support/resistance exist but carry no predictive power; trades taken near them have the same win rate as random entries. Walls break more often than they hold. | ✓ | `research/oi_levels.py` |
| Constituent breadth | Do individual stocks' 1-min moves predict the index? Use weights to model the index and test breadth features. | No. A 49-stock 1-min cache and effective-weight fit both exist; breadth itself (breadth oscillator, dispersion) adds no edge. | ✓ | `research/breadth.py`, `breadth_test.py`, `breadth_filter.py`; memory: `nifty-constituent-breadth-null` |
| Swing-pivot reversal | Trade reversals at marked tops and bottoms (TradingView charts). | Fully tested with multiple entry rules; all lose money. | ✓ | `research/swing_*.py` (9 scripts); memory: `nifty-swing-pivot-dead-end` |
| Four online strategies | ITM breakout-and-retest, trend debit spread, low-IV compression breakout, underpriced straddle — from a published research report. | All four fail on our data. The spread and straddle variants are also ruled out by the sell-side capital constraint. | ✓ | `research/online_s1_*.py` … `online_s4_straddle.py`, `online_control.py` |
| **15-min ORB scalper** | Mark the 09:15–09:30 range; buy ITM/ATM on a close beyond it while above the 9 EMA and VWAP; 15–20% premium stop or exit when the index closes back inside; 20–30% target or aggressive trail. | **Dead in every cell.** 6 trigger definitions × 6 strikes × 6 stop rules × 9 exits: exactly one variant is positive (1.0R trail, +₹4,851) and it beats only 90.5% of random draws — under the 95% bar. Win rates 8–28% before exits. The rule's own strike advice is inverted here: 3 ITM was least bad, 1 OTM beat ATM. The opening range breaks and fails: on a 1-min trigger 196 of ~200 trades ended on the close-back-inside abort. | ✓ | `research/orb_scalper.py` |
| **VWAP pullback** | Pull back to VWAP in an established trend, buy the confirming candle. | Dead. −₹46,159 at 5-min, −₹41,239 at 15-min, on 49.9% and 41.2% win. | ✓ | `research/momentum_setups.py` |
| **Supertrend + RSI** | Supertrend flips red→green, RSI clears 60 within 2–3 candles. | Dead at 5-min (−₹26,343). The 15-min version is the only near-miss in the group: +₹5,354 at ATM, +₹16,358 at 1 ITM, on **43 trades** — too few and too small to pursue. Supertrend alone (no RSI) is much worse (−₹59,895), so the RSI leg is doing real work; there just isn't enough of it. | ✓ | `research/momentum_setups.py` |
| **EMA scalping** | 5- or 9-period EMA cross on a 5-min chart. | Dead. Three variants, −₹36,977 to −₹55,354, win rates 47–52% — coin flips paying brokerage. | ✓ | `research/momentum_setups.py` |
| **RSI + MACD** | RSI >70 or <30 with a MACD crossover, 5-min. | Dead, and the worst per-trade result in the batch (−4.27 pts/trade, 29.8% win, n=47). Loosening to 60/40 raises volume and loses more (−₹55,584). | ✓ | `research/momentum_setups.py` |
| **Last-hour (3 PM) momentum** | Enter after 14:30 or 15:00 in the direction of the day. | Dead. −₹48,974 from 14:30, −₹20,362 from 15:00. Not enough time left for a trailing exit to work. | ✓ | `research/momentum_setups.py` |
| **Previous-day high/low break** | Buy CE on a close above yesterday's high, PE below yesterday's low. | **The one that worked — best result in the project.** See the dedicated section below. | ✓ | `research/prev_day_break.py`, `prev_day_validate.py` |
| **Long straddle / strangle** | Buy ATM CE+PE, or OTM both sides, for a move rather than a direction. | **Dead, decisively, and the reason is a number.** At 09:45 the median ATM straddle costs **222 index points** while the median largest subsequent move is **128 points** — and only **20.1%** of days move further than the straddle cost, before theta and before the move has to arrive by 15:20. Every configuration tested loses ₹79,000–99,000 of a ₹1,00,000 account; max drawdown equals the loss, i.e. the equity curve goes straight down. Strangles are worse than straddles at every width. | ✓ | `research/straddle_strangle.py` |
| Calendar spreads (volatility expansion) | Sell near-expiry, buy next-expiry. | **Excluded, not tested.** Requires selling the near leg, which needs margin this account does not have. Ruled out by the standing option-buying-only constraint, not by evidence. | — | — |
| Expiry-day buying | Expiry sessions move more; can we capture it? | Weaker, but not banned. Expiry moves 186 median points vs 179 on normal days — no bigger — while theta guts the position. A 1.5x-before-0.6x race wins 39.5% on expiry vs 58.5% normally; a momentum entry under the shipped exit wins 34–52% on expiry vs 51–62% normally. **Correction:** the shipped config never excluded expiry days and no config field exists to do so. What thins them is the ₹100 floor — expiry ATM premium runs ₹51 → ₹19 across the day vs ₹123 → ₹114 normally, so 15 of 17 expiry trades in the old ₹50–250 band were sub-₹100. Two cleared the floor in 246 sessions; both won. Left as is. | ✓ | `research/expiry_anatomy.py`, `research/clock.py`, `research/when_to_trade.py` |
| The late session (14:30–15:09) | Premium is cheaper late and there are spikes — is it being wasted? | No, and the premise is half wrong. There *is* more movement late (5-min moves clear 0.15% on 4.8% of minutes vs 2.2% midday) but premium is **not** meaningfully cheaper (₹114 median vs ₹123 at the open). Under the shipped exit, 14:30–15:05 ATM entries win 44.9% vs ~56% earlier, and **18.3% are still open at the 15:20 bell and closed flat vs 0.0% in every earlier window** — the trail never gets to run. Momentum-filtered, the late window drops to 51.4% win / +0.16 pts on normal days against 61.8% / +1.84 at the open. The shipped signal takes only 2 trades there (−₹1,030). Window left at 15:09; not worth a parameter change on n=2. | ✓ | `research/clock.py`, `research/when_to_trade.py` |
| SENSEX cross-validation | Does the edge exist on a second index? | Partial support, smaller sample. Not used for the shipped config. | ✓ | `research/sensex_crossval.py` |

## Exit and Target Tests

| test | question | key finding | closed? | ref |
|---|---|---|---|---|
| Better exit levels | Price targets from round numbers, volume profile, opening range, IV sigma, swings. Only distance-in-R matters; provenance never did. | Across 8 target families, the best achievable is +3.3% (keep 13% of MFE). The shipping exit at 0.5R trail reaches +1.8% and our 0.7R trail reaches +2.1%. No static level beats what we have. Perfect placement +25.1%. | ✓ | `research/exit_lab.py`, `OVERNIGHT_REVIEW.md` section 4 |
| Volume profile POC/VAH/VAL | Use 49-stock turnover to build a synthetic index profile. Trade POC, VAH, VAL as targets. | Profile is sound (median VA 90 pts, POC 64 pts from open). But reaching these levels was rarer and worse than trailing. Value-area extensions (the real hope) still lost. No edge from any variant. | ✓ | `research/volume_profile.py`, memory: `nifty-premium-band-finding` |
| Fixed vs trailing exits | Fixed target 1.25R / 2R / 3R vs 0.25R–1.5R trail, with and without activation. | Trail 0.5R best for points, 0.7R best for win rate. Only minor gains tested; our 0.7R is already tuned. Trail 1.0R loses. | ✓ | `research/exit_lab.py` |

## Stop and Risk Management Tests

| test | question | key finding | closed? | ref |
|---|---|---|---|---|
| Stop hunting diagnosis | Why do we get stopped 18/64 times (28%), often after tiny moves? | 72% of stopped contracts trade back above entry; 12/18 reach +1R later. The stop fires after a median 24 index points (~10% premium decay). It's catching noise, not real reversals. But every repair loses. | ✓ | memory: `nifty-stop-hunting-finding` |
| Stop-trigger repairs: 13 variants | Wider stops, delayed stops, soft vs hard, index-move conditions, close confirmation, re-entry logic. | All 13 variants worse than shipped. Best is "no stop for 3 bars" (₹18,929) vs shipped (₹40,341). But it's dominated by simply dialling the trail to 0.5R (₹21,221 at lower DD). | ✓ | `research/stop_rules.py`, OVERNIGHT_REVIEW.md section 3 |
| Position sizing and throttles | Trade 1/2/3 per day; equity % controls; cash % caps. | Capping trades/day loses money overall despite lower drawdown (max 1 → ₹28,889; max 3 → ₹40,341). Trade 2 of day is worthless; trade 3 is a tiny positive. Equity and cash throttles are not binding at ₹1L. | ✓ | `research/trade_quality.py` |
| Reversal after stop (buy PE after stopped from CE) | Once stopped, reverse into the opposite contract. | Reversal beats re-entry (61% win, +2.96R total vs 44% / −5.18R). But drawdown worse (8,876 → 12,950) and it beats a zero-skill control only 76% of the time, not 95%. Unestablished. | ✗ | OVERNIGHT_REVIEW.md section 2 |

## Entry Filtering and Gatekeeping Tests

| test | question | key finding | closed? | ref |
|---|---|---|---|---|
| Premium band sweep | Do contracts priced ₹50–250 all behave the same? Try ₹100–200, ₹75–200, etc. | **Settled 2026-08-16: the ₹100 floor is the whole finding and the upper cap was noise.** Eight bands re-run against one contract load. ₹100+ with no cap wins on every axis that matters — ₹38,626, maxDD ₹5,114, **net÷DD 7.55**, **9.14 pts/trade**, and it keeps **73% of its profit at a ₹2 round-trip bid-ask** against the old band's 22%. The cap is noise because the bands either side of it disagree at random (₹100–200 → ₹31,651, ₹100–250 → ₹24,291, ₹100–300 → ₹32,723). **Applied to the shipped config.** | ✓ | `research/premium_band.py`, `finalise.py`, `band_check.py`; memory: `nifty-premium-band-finding` |
| Why the ₹100 floor works | Are cheap contracts losers, or just thin? | **Just thin — and that is a stronger reason to cut them.** The 20 sub-₹100 trades won **70.0%** and booked ₹5,645 at the mid. But they captured only **1.73 points a trade** against 9.14 above ₹100, so a ₹1 round-trip takes 58% of their edge and ₹2 takes all of it. The floor is a costs finding, not an alpha one — which is why it holds up rather than decaying. | ✓ | `research/band_check.py` |
| Removing the ₹250 cap | What does the old ceiling throw away? | 7 trades, 14% of the set, capturing **~26 points each** — dear contracts have more delta and pay the same rupee spread. Highest premium ever bought was ₹333, so the ₹1,000 setting is a sentinel and not a parameter. | ✓ | `research/band_check.py` |
| RSI + 20 EMA as standalone signal | Four classic setups: pullback, cross, reversal, momentum. Tested at 3/5/15-min on both EMA and SMA. | **Total failure.** All 24 variants lost money (best −₹3,095 vs shipped +₹40,341). Win rates 46–57%. EMA cross 15min beat random entries 96% of the time but still loses. The indicators have no information. | ✓ | `research/rsi_ema.py` |
| RSI + 20 EMA as gate on existing entries | Keep only trades where the index is on the correct side of its 20 EMA; also test RSI buckets. | **Reads well, fails in money.** Trend alignment lifted win rate to 74–80% vs 60–62%. RSI over 70 was the best bucket in all three timeframes — the opposite of textbook overbought, consistent with a trend-follower. | ✓ | `research/rsi_ema.py` |
| EMA gate tuned through the full pipeline | Does the gate survive when the whole backtest is re-run rather than rows struck from a finished list? | **No. Every gated variant loses money.** Quality rises, volume collapses, and volume wins. 15-min gate: 75.0% win and 7.45 pts/trade but ₹20,475 against ₹40,341. Stacked on the ₹100–200 band it reaches 83.3% win and 12.91 pts/trade on **12 trades** — ₹10,286, and n=12 over 246 sessions is not a strategy. RSI floors make it worse at every level. | ✓ | `research/tune_gate.py` |

## Market Regime and Context Tests

| test | question | key finding | closed? | ref |
|---|---|---|---|---|
| Strike selection: ATM vs ITM/OTM | Test 3 ITM through 2 OTM strikes. | ATM degrades most gracefully under bid-ask spread (only ₹3,444 kept at ₹1.50 round-trip). 2 OTM looks 55% better at mid but loses by ₹0.25 spread. Stop room is 24–26 pts at *every* strike (10% of a larger premium scales with higher delta). | ✓ | `research/moneyness_lab.py`, OVERNIGHT_REVIEW.md section 5 |
| Bid-ask spread sensitivity | Model a fixed rupee spread instead of percentage. Measure break-evens. | **Critical finding.** Strategy captures 5 R-pts per trade ≈ 5 premium points. ₹1 round-trip is 20% of the edge; ₹2 is 80%. No parameter moves the result as much as execution quality does. Shipped band keeps 22% at ₹2 round-trip; ₹100–200 band keeps 56%. | ✓ | OVERNIGHT_REVIEW.md section 5 |

---

## The Previous-Day High/Low Break — the one live candidate

Buy 1 ITM on the first 5-minute close beyond yesterday's high (call) or low
(put). One trade a day. House exit: 10% stop, 0.7R trail, flat 15:20.

| measure | prev-day break | shipped strategy |
|---|---|---|
| trades over 246 sessions | 158 | 52 |
| win rate | 64.6% | 68.8% |
| points per trade | 3.21 | 4.98 |
| net at 2% risk | **₹1,24,356** | ₹40,341 |
| max drawdown | ₹30,578 (30.6%) | ₹8,876 (8.9%) |
| **net ÷ drawdown** | **4.07** | **4.55** |
| beats random draws | 100.0% of 200 | — |

**It is not three times better. It is the same edge run at three times the
risk**, and slightly worse per unit of drawdown. At 1% risk it makes ₹27,468 for
a drawdown of ₹7,884 — very close to the shipped strategy's shape, and less
money. What it genuinely adds is *volume*: 158 trades against 52, so the same
edge is measured with three times the confidence.

What survived testing:

- **Walk-forward.** Parameters chosen on the first 123 sessions only, then run
  untouched on the last 123: ₹71,329 in sample, **₹25,868 out**. 7 of 8 grid
  variants were profitable out of sample. The edge carries at ~36% of the
  in-sample rate — decayed, not dead.
- **The first signal of the day is the entire strategy.** First break: 64.6%
  win, +3.21 pts/trade, ₹1,24,356. Second break of the same day: 46.8% win,
  **−2.16 pts/trade, −₹22,001**. Once a level is gone it stops being a level.
- **1 ITM is the strike**, and this is the rare case where moneyness matters a
  lot: ₹70,025 at 1 ITM vs ₹37,250 ATM and ₹3,123 at 1 OTM (2/day basis).

What to be careful about:

- **A parameter cliff.** Asking price to clear the level by 0.05% instead of
  merely closing across it drops ₹70,025 → ₹12,647; by 0.10% it goes negative.
  The edge lives entirely in the immediate break. Explicable (a later entry is a
  worse entry against a fixed percentage stop) but it is a narrow ledge.
- **Timeframe sensitivity.** 5-min is the sweet spot (₹70,025); 3-min gives
  ₹23,239 and 15-min ₹14,859. That much variation across neighbouring settings
  is a fitting risk.
- **A 30% drawdown at 2% risk** on a ₹1,00,000 account, and the strategy needs
  2% risk to size at all — at 1% risk only 124 of 158 trades are affordable, at
  0.5% only 25. The account is close to the minimum this strategy can be run on.
- **Second-half decay** in per-trade terms: 2.48 pts/trade in the first half,
  −0.10 in the second (2/day basis).

Not applied to anything. It combines with the shipped strategy — see the section
below — but was deliberately left out of the finalised config, and the reason is
the parameter cliff above rather than the drawdown.

---

## Does the Previous-Day Break Combine With the Shipped Strategy? — answered

Both signal sets were put through **one** ₹1,00,000 ledger in chronological
order, with cash reserved against positions still open so a second signal can
never be sized using money the first trade is still holding.

| account holds | n | win% | net | maxDD | net÷DD |
|---|---|---|---|---|---|
| shipped ₹100+ alone | 51 | 66.7 | ₹38,626 | ₹5,114 | **7.55** |
| previous-day break alone | 158 | 64.6 | ₹1,24,356 | ₹30,578 | 4.07 |
| both together | 209 | 66.0 | **₹2,27,252** | ₹51,493 | 4.41 |

**They genuinely add.** The combination beats the sum of the parts (₹2,27,252
against ₹1,62,982) because each strategy compounds the capital the other one
sizes against. Collision is nearly absent: across 209 trades there are only
**3 overlapping open positions**, so the two rarely compete for cash even though
61% of shipped days also carry a break signal.

**And it is still not what to trade first**, for three reasons that only show up
once the sample is cut in half:

| most recent 123 sessions | n | win% | net | maxDD | net÷DD |
|---|---|---|---|---|---|
| shipped ₹100+ | 42 | **69.0** | ₹29,646 | **₹4,538** | **6.53** |
| previous-day break | 73 | 57.5 | ₹25,868 | ₹16,647 | 1.55 |
| both | 117 | 61.5 | ₹65,583 | ₹23,607 | 2.78 |

1. The break is **decaying** — 69.0% → 57.5% win, and its drawdown nearly
   trebles. The shipped band is not.
2. Combining costs a **₹23,607 drawdown in six months** on a ₹1,00,000 account
   — 23.6% — to roughly double the money.
3. Decisive: **the break is fragile to exactly the delay that live execution
   adds.** Requiring price to clear yesterday's level by 0.05% rather than
   merely close across it drops it from ₹70,025 to ₹12,647. On NIFTY at 25,000
   that 0.05% is **12.5 points** — well inside what a real fill gives up on a
   fast break. The backtest enters at a 5-minute close; a live order will not.

So it is a real second strategy, not a duplicate of the first, and it is the
obvious candidate for a second sleeve **once live fills have been measured**
against the modelled ones. It is not something to switch on for day one.

---

## Still Open

| question | why it is still open | next step |
|---|---|---|
| What are we actually paying in spread? | Everything in this register assumes a modelled bid-ask. It is the single largest sensitivity, the reason the ₹100 floor was chosen, and the only number that has been modelled rather than measured. **Now the top question by a distance**, because the finalised strategy is about to place real orders. | Log quoted bid/ask against actual fills from the first live orders. Every finding here is conditional on it. |
| Does Dhan's `trailingJump` reproduce a 0.7R trail? | The backtest trails at 0.7R behind the running high **once the trade is 0.7R in profit**. Dhan's super order trails by a fixed rupee jump from the moment price moves. They are not the same rule and the difference has never been quantified. | Either model Dhan's actual trail in the backtest, or manage the trail client-side and send only the hard stop. |
| Will the break survive live fills? | It combines and it adds (section above), but a 0.05% entry delay — 12.5 NIFTY points — cuts it by 82%. That is inside normal slippage. | Paper-trade it alongside the live strategy for a month; compare fills to the 5-minute closes the backtest assumed. |
| Reversal after a *genuine* adverse move | The >25-point subset wins 75% (+3.02R) but n=8. The all-stops version beats a zero-skill control only 76% of the time. | Needs more than 8 trades before it can be traded. |
| BANKNIFTY cross-validation | Would confirm or kill both the premium-band finding and the previous-day break on independent data. | Token works; the capture is a day's work. |

## Answered and Closed This Round

| question | answer |
|---|---|
| Is the ₹200 upper bound real? | **No.** The bands either side of it disagree at random. Removed; the floor is the whole finding. |
| Does the previous-day break combine with the shipped strategy? | **Yes, and it adds beyond the sum of the parts** — but it is decaying and it is fragile to live-fill delay, so it is not in the finalised config. Full analysis in the section above. |

---

## Infrastructure

| item | status | note |
|---|---|---|
| `research/simlib.py` | ✓ built and in use | One execution model — fill, slippage, stop, trail, ₹1L compounding ledger, real charges, zero-skill control — shared by every strategy in the new batch, so two ideas differ only in their *entry rule*. Loads price cubes only (not `v`/`oi`/`iv`), keeping 246 sessions resident in ~200MB instead of ~900MB. |
| `research/indicators.py` | ✓ extended | Added `macd`, `atr`, `supertrend` (standard band-carrying rule) and `vwap`. All four pass known-answer tests on synthetic data. |
| `research/vwap.py` | ✓ built and validated | Synthetic index VWAP from 49-constituent rupee turnover, checked against real futures VWAP on the 56 overlapping sessions: **0.963 median correlation**, 6.2-point median gap once the 263-point futures basis is removed, and the binary "price above VWAP" — the only thing strategies consume — **agrees 91.7% of minutes** (worst session 63.2%). Usable across all 246 sessions. |
| `research/fast_backtest.py` | partial | Caches contracts in memory so a multi-variant script pays the load once instead of per variant. |
| Contract load time | **unsolved, highly variable** | Measured 158s cold-ish and 3,632s (60 min) on a later run — a 23× swing on identical work, driven by OS file cache. Note the `simlib` path avoids this entirely by reading the npz session cache directly, so it now affects only the older `strategy_backtest` scripts. |
| Candidate-set caching | not built | Signal generation is ~27s and is repeated per variant. Descoped once the gate study came back negative. |

---

## Closed — Not Worth Re-testing

- **Better levels or targets** — 8 families tested (round numbers, IV sigma, opening range, POC, VAH, VAL, naked POC, value-area extensions). Only *distance in R* ever mattered; *provenance* never did.
- **Stop-trigger repairs** — 13 variants, all dominated by simply dialling the trail.
- **Moneyness / ITM for stop room** — stop room is 24–26 index points at every strike from 3 ITM to 2 OTM.
- **Position-size and equity throttles** — not binding at ₹1L; a third of trades are already a single lot.
- **Constituent breadth** — no edge.
- **Swing-pivot reversal** — fully tested, negative.
- **RSI / 20 EMA in any form** — dead as a signal, dead as a gate.
- **Expiry-day buying** — theta dominates and directional win rates drop 10–20 points, but it is *not* excluded by a date rule; the ₹100 premium floor thins it, which is the better mechanism. See the correction in the table above.
- **The last hour** — weakest window under this exit; 18% of entries get closed flat at the 15:20 bell against 0% earlier. Left in the window because the signal only reaches it twice in 246 sessions.
- **15-minute ORB scalper** — every trigger definition, strike, stop and exit tested; one marginal positive that fails the control.
- **Long straddle and strangle** — the day is priced at 222 points and delivers 128; only 20% of days move further than the straddle costs.
- **VWAP pullback, EMA scalping, RSI+MACD, last-hour momentum** — all four are coin flips paying brokerage.

---

## Dhan Data Collection Status

| data | needed for | status | blocker |
|---|---|---|---|
| NIFTY futures (1-min bars) | Real index volume, VWAP validation | ⚠ **partial — 56 of 246 sessions** | Not a token problem. Dhan's instrument master lists only *live* contracts, so once a contract expires its history becomes unreachable. Three NIFTY futures are listed (Aug/Sep/Oct 2026) and the deepest reaches back about three months. This cannot be fixed retroactively — it can only be fixed forward, by capturing futures volume daily from now on. |
| Constituent turnover (49 stocks, 1-min) | The VWAP that strategies actually use | ✓ **246 sessions, full coverage** | None. Validated against the futures on the 56 overlapping sessions. |
| BANKNIFTY options (1-min) | Cross-validation on a second index | ✗ not captured | None — token works. Just not done yet. |
| Volume profile by contract | Refined entry timing | ✓ available locally | None |

---

*Last updated: 2026-08-16*
