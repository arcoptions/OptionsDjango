# Stock options: every strategy tried, and what the data said

*Measured 16–18 August 2026. Live version at `http://127.0.0.1:8000/research/`.*

You asked for a report of the different strategies tried. This is it, worst news
first, because the worst news is the most useful thing in the file.

---

## The short version

**Sixty-eight of the 70 buy-side strategy combinations tested lose money after
the spread.** Fourteen entry signals, five exit rules, run against real
fixed-strike option paths with the tick charged both ways. Momentum, breakouts,
OI surges, IV rank, moneyness bands — on every signal with a real sample the
answer is 0.75–0.89× per rupee committed.

**One thing cleared break-even, and it was not on the list.** Following the data
rather than my signal ideas led to expiry-day at-the-money calls on a trailing
stop: **1.041×** across 21,693 entries. It survives the checks designed to kill
it, but its median trade is 0.847× and 74% of trades lose. Section 7 is the
whole story, and it is the part worth reading twice.

Your observation was correct. UNIONBANK 190 CE really did go 0.70 → 5.00, and
**348 contract-cycles in this dataset ran 3× or more off their own low**. The
moves are there. What is not there is a way to know in advance *which* option is
about to be one of them — see section 5, which is the finding that generalises
past anything I happened to test.

> **Read this before you read anything else.** Every option bar in this study is
> **at the money** — 87% sit within 1% of the strike, and 100% of the 348 big
> runs fall inside ±2%. That is not a property of the market, it is the shape of
> the data I pulled: the cache was built with Dhan's `relative_strike='ATM'`,
> front month only. So this report is a complete verdict on **buying ATM stock
> options** and says almost nothing about buying **cheap OTM options** — which is
> the specific trade in your UNIONBANK example, where a ₹0.70 call on a ₹190
> strike means spot was nowhere near 190. Section 0 states exactly what that
> invalidates. The wider data is downloading now.

Three things came out of it worth more than a working strategy would have been:

1. **The base rate is 0.77×.** A stock option bought at a random bar and held
   two sessions comes back worth 77 paise on the rupee. It touches 2× at some
   point 0.62% of the time. That single number explains the last eighteen months.
2. **The exit is worth more than any entry.** Same entries, different exit:
   holding two sessions returns 0.773×, a 30% trailing stop returns 0.882×.
   Eleven paise recovered by changing nothing but when you sell — a bigger gap
   than between the best and worst entry signal in the entire study.
3. **The winners are invisible at entry.** Doublers and non-doublers are
   statistically identical on OI, volume, IV, and stock momentum. If you cannot
   tell them apart beforehand, no entry filter can exist.


---

## 0. What this data can and cannot answer

I found this after writing the rest of the report, and it changes how three
sections should be read, so it goes first.

The option cache was built entirely from Dhan's rolling feed at
`relative_strike='ATM'`, `expiry_code=1`. Every one of the 831,962 option bars
carries that tag. In practice:

| Moneyness band | Share of bars |
|---|---|
| Within ±1% | **86.7%** |
| ±1% to ±2% | 3.2% |
| Beyond ±2% | 1.1% |
| Spot missing / zero | 9.1% |

The feed re-centres on the money continuously, so a strike only appears while
the stock is standing on it. UNIONBANK is the clean illustration: the stock
ranged **₹106.87 → ₹205.14** over the 18 months, and the 190 CE exists in the
cache for exactly **121 bars**, every one of them with spot between 188.76 and
191.24. The moment spot left that band, the contract left the data.

**What still stands, untouched:**

- The **base rate** (section 1) — correct, and correctly labelled, for ATM options.
- The **exit finding** (section 3) — the 30% trail beating hold by 11 paise is
  measured on the same paths either way.
- The **expiry-day result** (section 7) — it *required* |moneyness| ≤ 1.5%, which
  is precisely where this data lives. This is the one conclusion the limitation
  cannot touch.
- The **Chartink work** (section 4) — equity-only, unaffected.
- The **sell-side arithmetic** (section 8) — the mirror of section 1, same scope.

**What has to be withdrawn or narrowed:**

1. **"OTM 2–6% + momentum" was never really tested.** Only 5,651 call bars in
   the entire cache — **0.68%** of them — sit 2–6% out of the money, and they are
   there by accidental drift between re-centrings, not by design. The 305 trades
   behind that row are a scrap sample. It should not be reported as the best
   entry for two of the five exit rules, and it is now marked accordingly.
2. **"Median moneyness at the low: zero" was circular.** Of course it was zero —
   nothing else was in the dataset. It is the boundary of the cache, not a
   discovered property of big movers. Section 6 has been rewritten. The
   *days-to-expiry* half of that finding survives, because DTE genuinely varied
   (0 to 34 days).
3. **"No entry filter can exist" (section 5) is now a claim about ATM options.**
   Within ATM, the doublers really are invisible at entry, and that is solid. It
   is no longer a claim about OTM options, because I have not seen them.

**The honest position on your UNIONBANK thesis:** buying a 190 CE at ₹0.70 with
spot near ₹175 is a deep-OTM lottery ticket, and this dataset contains no such
trade. I cannot tell you it works and I cannot tell you it fails.

`ATM±1, ±2, ±3` for the full universe and window is downloading now
(`research/fetch_otm.py`, live progress in `research/fetch_otm.log`). Two caveats
on it, both mine to own:

- **It is slow.** 29,484 windows at ~13/minute is roughly **36 hours**, not
  overnight — the ATM job from the first pass is still running and competing for
  the same rate limit, and about 13% of requests are coming back 504 and being
  retried. The offsets are fetched nearest-first, so this degrades gracefully:
  ATM±1 across the whole universe lands in the first few hours and on its own
  roughly doubles the moneyness coverage, which is enough for a first real answer
  on the 2–6% band.
- **Even finished, it will not reach your example.** On a 2.5-point strike ladder
  ATM±3 is only about ±4% on a ₹190 name. A ₹0.70 premium on that strike means
  spot was ~8% away, which is outside what the rolling feed will serve at any
  offset. Getting there needs the **ladder feed** (real contract security ids,
  live expiries only) — that is a separate pull and it is the honest next step
  for your specific thesis.


---

## 1. The base rate

Before any strategy, the ground truth. 279,400 tradeable entry bars, 38+ symbols,
19 expiry cycles, fixed strike within a single cycle.

| Hold | n | ≥1.5× | ≥2.0× | ≥3.0× | median MFE | mean end | ≥2× at end |
|---|---|---|---|---|---|---|---|
| 1 day  | 152,027 | 5.35% | 1.09% | 0.34% | 1.14 | 0.86 | 0.32% |
| 2 days | 80,692  | 5.15% | 0.88% | 0.27% | 1.14 | 0.78 | 0.21% |
| 5 days | 11,703  | 6.52% | 1.26% | 0.19% | 1.17 | 0.66 | 0.12% |

MFE is the best price the option printed while held — the ceiling a perfectly
timed exit could have caught. "end" is what you get if you simply hold. The gap
between them is the exit problem, and it is enormous: the median option touches
1.14× at some point and comes back worth 0.78×.

Sliced by entry premium, every band loses:

| Premium at entry | Trades | Net avg | Win % | Reached 2× | Tick as % of premium |
|---|---|---|---|---|---|
| ₹0.05–1 | 9,580 | 0.841 | 18.4% | 6.21% | **8.3%** |
| ₹1–2 | 11,343 | 0.762 | 17.9% | 2.66% | 3.2% |
| ₹2–5 | 49,968 | 0.761 | 17.1% | 1.31% | 1.4% |
| ₹5–10 | 55,837 | 0.789 | 18.0% | 0.96% | 0.7% |
| ₹10–25 | 87,652 | 0.767 | 18.0% | 0.62% | 0.3% |
| ₹25+ | 248,445 | 0.771 | 16.2% | 0.23% | 0.1% |

The cheap options double seven times more often *and* the 5-paise tick is 8.3%
of the position before anything happens. Those two facts cancel almost exactly.

---

## 2. The fourteen entry signals

All run against the same paths, all with entry at the **next bar's open** after
the signal bar closes, never at the signal bar itself.

| # | Signal | What it was testing |
|---|---|---|
| 1 | Every liquid bar | The baseline everything else must beat |
| 2 | Stock 25-bar breakout | Classic momentum |
| 3 | Breakout + volume surge >1.5× | Momentum with confirmation |
| 4 | Stock +1% in an hour | Short-horizon thrust |
| 5 | Stock +3% in a day | Longer thrust |
| 6 | OI +20% over a day | Positioning — "smart money is building" |
| 7 | OI up + premium up | Positioning with price confirmation |
| 8 | Low IV rank (<30) + breakout | Buy volatility cheap, before it expands |
| 9 | OTM 2–6% + momentum | The leverage sweet spot — **but see section 0: the data has almost no OTM bars, so this row is a scrap sample, not a test** |
| 10 | Under ₹3 + momentum | The lottery-ticket version of your thesis |
| 11 | 14+ DTE + breakout | Buy time, avoid the decay cliff |
| 12 | EMA20 pullback in an uptrend | Chartink-style: buy the dip in a trend |
| 13 | Stock 25-bar breakdown (puts) | The short side |
| 14 | Stock −1% in an hour (puts) | Short-side thrust |

And five exits: hold two sessions; target 1.5× / stop 0.6×; target 2× / stop
0.6×; target 3× / stop 0.7×; trailing stop 30% off the peak.

**Result: 2 of 70 combinations returned above 1.0, and both had fewer than 100
trades, all inside one stretch of the sample, with nothing in the other half to
check them against.** Zero survived the half-sample split.

The full 70-row table is on the localhost page. The shape of it:

| Exit rule | Best net achieved | With which entry |
|---|---|---|
| Trail 30% off peak | 0.974 | put, stock breakdown *(77 trades)* |
| Hold 2 sessions | 1.087 | put, stock −1% in an hour *(99 trades)* |
| Target 1.5× / stop 0.6× | 0.935 | call, OTM 2–6% + momentum *(305 trades — scrap sample, see section 0)* |
| Target 2× / stop 0.6× | 0.897 | put, stock −1% in an hour *(99 trades)* |
| Target 3× / stop 0.7× | 0.864 | call, OTM 2–6% + momentum *(305 trades — scrap sample, see section 0)* |

On every signal with a real sample (thousands of trades), the answer is
0.75–0.89. Momentum, breakouts, OI surges, IV rank, moneyness bands — none of
them moves the number more than a couple of paise, and none moves it above 1.

---

## 3. The exit was the real lever

Read the table by *exit* instead of by *signal* and something appears that no
entry rule produced.

| Exit | Baseline net (all liquid bars, 423,954 trades) |
|---|---|
| Hold 2 sessions | 0.773 |
| Target 2× / stop 0.6× | 0.794 |
| Target 1.5× / stop 0.6× | 0.815 |
| Target 3× / stop 0.7× | 0.814 |
| **Trail 30% off peak** | **0.882** |

Eleven paise on the rupee, from the exit alone. This is the same lesson the
NIFTY work landed on — the trail gap was the lever there too. It still does not
reach 1.0: a better exit shrinks the loss, it does not create an edge. But if
there is one habit to carry into live trading from this whole study, it is that
**giving back 30% from the peak is a far better rule than any target you can
name, and far better than holding.**

Fixed targets are actively harmful here. A 2× target with a 0.6× stop hits the
target 1.8% of the time and the stop 43.1%. You are paying a 43% stop rate to
chase a 1.8% event.

---

## 4. The Chartink scans

Both measured from their own backtest exports, so there is no risk my reading of
the rule differs from Chartink's. Two corrections change the answer completely.

**Correction 1 — Chartink stamps a trigger with the candle's START, not its
close.** Both exports contain 09:15 triggers, and no candle has finished at the
opening bell. A 15-min trigger stamped 09:15 describes the 09:15–09:30 candle
and is only knowable at 09:30. Entering at the stamped bar means buying with the
breakout candle's own move already in hand. Before this correction, NARC1HR
showed +1.24% excess at 1 hour with a t-stat of 22.97 and 23 of 23 days
positive — an impossible result, and that impossibility was the tell.

**Correction 2 — market drift.** A breakout scan fires when stocks are breaking
out, which is when the whole market is moving. Every trigger is therefore
compared against what the average stock in the same 172-name universe did over
the identical clock window.

| Scan | Triggers | Days | 1h raw | 1h excess | t-stat |
|---|---|---|---|---|---|
| ARC15MIN (15-min) | 270 | 7 | — | **+0.064%** | 1.09 |
| NARC1HR (1-hour) | ~1,100 | 23 | — | **−0.001%** | −0.02 |

ARC15MIN's entire positive mean comes from 5 of its 270 triggers.

Both samples are short. This is **"not demonstrated"**, not "disproved" — but it
is certainly not demonstrated, and it is not a foundation to build option entries
on yet. If you want a verdict on these, the thing to do is collect six months of
triggers rather than seven days.

---

## 5. The finding that generalises: the winners are invisible

This is the most important test in the study, because it does not depend on my
choice of signals.

Take the 3,203 trades that doubled. Take the 459,622 that did not. Compare what
was *observable at the moment of entry*.

| Observable at entry | Doublers | Everything else | Separation |
|---|---|---|---|
| Open interest | 318,750 | 317,200 | 0.00 sd |
| Volume surge | 0.71× | 0.70× | 0.01 sd |
| Implied volatility | 27.2 | 29.0 | −0.13 sd |
| Stock return, prior hour | −0.11% | −0.01% | −0.13 sd |
| IV rank | 33.5 | 44.4 | −0.28 sd |
| Distance above EMA20 | −0.22% | +0.01% | −0.28 sd |
| Stock return, prior day | −0.59% | +0.05% | −0.32 sd |
| **Premium at entry** | **₹5.25** | **₹28.75** | −0.25 sd |
| **Days to expiry** | **3** | **15** | **−1.03 sd** |

Breakout in progress at entry: 2.3% of doublers, 4.4% of everything else. The
breakout pointed the *wrong way*.

Only two things separate them, and both are arithmetic rather than edge.
**Premium** — a ₹5 option needs ₹5 to double and a ₹29 option needs ₹29.
**Days to expiry** — which is the whole story, and it leads to section 6.

If the winners cannot be told apart from the losers at entry, no entry filter can
exist. Not the fourteen tried here, and not the fifteenth.

**Scope:** this is a statement about **at-the-money** options (section 0). Within
that population it is solid — 3,203 doublers against 459,622 non-doublers is a
large, clean comparison. It is not a statement about OTM options, whose entry
characteristics I have not observed.

---

## 6. What the 2–3× moves actually are

348 contract-cycles ran 3× or more off their own low — 2.7% of all
contract-cycles. The top of the list:

| Symbol | Type | Strike | From | To | Multiple | DTE at low | Moneyness |
|---|---|---|---|---|---|---|---|
| SRF | CALL | 3100 | ₹0.80 | ₹25.05 | 31.3× | **0** | −0.6% |
| KEI | CALL | 3800 | ₹1.35 | ₹34.15 | 25.3× | **0** | −0.6% |
| HINDZINC | CALL | 430 | ₹0.10 | ₹1.80 | 18.0× | **0** | −0.2% |
| NATIONALUM | PUT | 340 | ₹1.00 | ₹15.00 | 15.0× | **2** | +0.1% |
| KALYANKJIL | CALL | 570 | ₹0.45 | ₹6.00 | 13.3× | **0** | −0.9% |
| VEDL | CALL | 450 | ₹0.15 | ₹1.75 | 11.7× | **0** | −0.3% |
| TECHM | CALL | 1400 | ₹1.90 | ₹18.00 | 9.5× | **2** | 0.0% |
| ABB | CALL | 5200 | ₹5.50 | ₹52.00 | 9.5× | **0** | −0.5% |

Median days-to-expiry at the low: **2**. The distribution is bimodal — 205 of
the 348 (59%) were within three days of expiry, and 124 were freshly-rolled
front-month contracts at 31–34 days.

The moneyness column needs the caveat from section 0: every one of these 348 sits
within ±2% of the money, but that is where *all* the data sits, so it is the
boundary of the cache rather than a discovered property of big movers. What the
table does establish, because DTE ranged freely from 0 to 34, is the
**time-to-expiry** half: the single largest cluster of 3×+ runs starts on or
within days of expiry day.

For those near-expiry ones — the 59% — the mechanism is not in doubt. They are
**at-the-money options with hours left**, where all remaining value is gamma and
a 1% move in the stock is a 10× move in the option. That is the machine that
produced the UNIONBANK chart you remembered. Whether the *same* multiples are
available further out of the money is exactly what this data cannot say, and
exactly what the download running now is for.

The catch is in the same table, and it does not depend on any of the above. Every
multiple is measured from the contract's own low, and a low is only visible as a
low afterwards. At the moment of that low, these options were indistinguishable
from the hundreds of others expiring the same afternoon that went to zero
instead: same moneyness, same time to expiry, same prior-hour stock move.

---

## 7. The one thing that cleared break-even

Section 6 pointed somewhere specific, so it got its own test on its own terms:
**expiry-day (DTE 0–1) at-the-money calls, exited on a 30% trail off the peak.**
21,693 entries across 23 expiry sessions and 87 symbols.

**Net expectancy 1.041×.** The only rule in the study to clear 1.0 on a serious
sample.

I expected it to be a settlement artefact — the 15:15 slot alone printed 1.42×,
and F&O runs to 15:39 on quotes nobody can fill. So it got the hostile treatment:

| Check | Result | |
|---|---|---|
| Drop entries from 14:00 onward | **1.033×** | survives — not a closing-bell artefact |
| First half of the sample | **1.029×** | survives |
| Second half of the sample | **1.054×** | survives |
| Drop the best 5 of 23 expiry sessions | 0.966× | **fails** |
| Drop the best 1% of trades | 0.939× | **fails** |

It is not an artefact and not a one-half fluke. It is also not a reliable edge,
and the reason is in the rest of the numbers:

- **Median trade: 0.847×.** The typical trade loses 15%.
- **Win rate: 26.1%.** Three trades in four lose money.
- **9 of 23 expiry sessions were profitable.** 61% of expiry days lost.
- **Day-clustered t-stat: 1.11.** Across 23 sessions that is indistinguishable
  from zero.

So: a positive-expectancy **lottery**, where the entire mean lives in the top 1%
of trades. That is a real thing and it can be traded — but it is the opposite of
what it feels like to trade. You lose on three trades out of four and on three
expiry days out of five, and the money arrives in rare, violent bursts. Sizing it
like a normal trade guarantees you are stopped out of the account before the tail
shows up.

And 23 expiry sessions is far too short a sample to tell a real fat tail from a
lucky one. The correct next step is a **small forward test** — fixed tiny size,
every expiry, no discretion — not a reallocation.

This is also the honest answer to your UNIONBANK question. The 2–3× moves are
real, they are catchable, and the structure that produces them is expiry-day
gamma. What they are not is *easy*: the same structure that produces the 31×
on SRF produces a 0.85× median and a 74% loss rate, and nothing observable at
entry tells you which one you are in.

---

## 8. Where the edge sits structurally

Every buyer's 0.77× is somebody's 1.23×. Mirroring all 462,825 trades to the
sell side: the seller collects **+19.4% per rupee of premium sold** and wins
**79.8%** of the time.

That is not a discovery — it is the same measurement read from the other end.
But it is where the money in this dataset went, and it is worth stating plainly
because it is the structural fact underneath the account statement.

**It is not a recommendation to sell naked.** The worst 1% of those short trades
average **−474% of premium collected**. One gap wipes out a hundred good weeks,
and margin on a naked stock-option short is roughly 15% of contract value, so
return on *capital* is nothing like return on premium.

The honest version is **defined-risk spreads** — keep the decay, cap the tail.
That has not been tested yet and is the single most promising thing left on the
list.

---

## Method, so you can attack it

- **Fixed strike, one expiry cycle.** Dhan's rolling feed is ATM-*relative*: ask
  for "ATM" and each bar returns whatever strike was at the money at that moment,
  changing every ~5 bars. Buying "ATM" Monday and selling "ATM" Friday is not a
  trade — it swaps contracts underneath you and manufactures returns. Every
  series here is rebuilt as a fixed strike inside a single monthly cycle.
- **...which also means the study only ever saw at-the-money options.** The same
  re-centring that has to be undone to build a holdable series is what keeps 87%
  of bars within 1% of the strike. This is the section 0 limitation, and it is a
  property of *what was downloaded*, not of the rebuild.
- **Stale quotes removed by self-contradiction.** A freshly-rolled contract that
  has not traded is quoted at a nominal 5 paise: TRENT 4700 CALL showed ₹0.05
  with spot 4673 and 30 days left, when it was worth about ₹150. Those quotes
  invent thousand-baggers two bars later. IV and volume filters do not catch them.
  What does: price the option from the feed's *own* IV with Black-Scholes and drop
  quotes disagreeing by more than 3× either way. Keeps 91.4% of bars; cut the
  measured 2× base rate from 7.64% to 0.88%.
- **Entry is the next bar's open**, never the signal bar's close.
- **Stops win ties.** When a 15-minute bar's high and low would both trigger, the
  stop is taken. The bar cannot say which came first, and assuming the good one
  is how backtests lie.
- **Costs at the tick.** One full 5-paise tick each way plus 0.28% turnover tax.
- **Significance clustered by day.** Overlapping entries on one name in one hour
  are one bet wearing many hats. (The baseline's day-clustered t reads +0.94
  despite a 0.773 mean — that is five freak days out of 359. The *median* day is
  0.762. Where a skewed distribution makes the mean and the t disagree, the
  median day is the honest summary.)

## Files

| Script | What it does |
|---|---|
| `research/option_moves.py` | Base rates, the stale-quote filter, cost reality |
| `research/option_strategy.py` | The 70-combination table (vectorised path walker) |
| `research/option_diagnostics.py` | Sections 5 and 7, plus the big-runs list |
| `research/option_expiry_day.py` | Expiry-day gamma, by entry time of day |
| `research/option_expiry_verify.py` | Hostile check on the one candidate that cleared 1.0 |
| `research/chartink_study.py` | Section 4 |
| `research/fetch_otm.py` | Widens the cache to ATM±1/±2/±3 so section 0's gap can be closed |

Outputs: `option_strategy.csv`, `option_big_runs.csv`, `option_moves.parquet`,
`chartink_ARC15MIN.csv`, `chartink_NARC1HR.csv`.
