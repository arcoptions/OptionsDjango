# Overnight review — 15 August 2026

Your brief was: avoid the stop-loss hunting, reduce the drawdown, test reversing
after a stop, think outside the box, come up with a proper strategy. Plus the one
lead you picked out — volume profile POC/VAH/VAL as targets.

All four are done. Three came back negative and I have closed them out so we stop
paying for them. The fourth — which was not in the brief, and which I only found
by asking a different question — is worth acting on.

---

## The short version

**There is one change worth making, and it is a parameter we already have.**
Narrowing the entry premium band to ₹100–200 is the first lever found in this
entire project that improves win rate, points captured *and* drawdown at the same
time. Every other lever so far has traded one against another. Once a realistic
bid-ask is charged it earns more money too — the contracts it excludes are cheap
ones whose profit only exists at the mid.

**Stop hunting is real and I measured it precisely — and it cannot be repaired.**
Thirteen different stop rules, every one worse than what we ship.

**Your reversal idea is the only positive follow-up**, but it beats a
zero-skill control only 76% of the time, so I cannot call it established.

**Volume profile came back null**, and it closes the entire "better levels"
research family for good.

**The real threat to this strategy is not any rule. It is the bid-ask spread.**
It is also the one number in here I have had to model rather than measure.

---

## 1. The change I recommend: narrow the entry premium band

We currently accept any contract priced ₹50–₹250 (`premium_min`, `premium_max`).
That range is doing a lot of unexamined work. Full pipeline, ₹1,00,000, 0.7R
trail, every band re-run end to end so the cooldown and daily-loss rules respond
to the trades the filter removes:

| premium band | n | win% | net ₹ | max DD | pts/trade |
|---|---|---|---|---|---|
| 50–250 *(shipped)* | 64 | 68.8 | 40,341 | 8,876 | 4.98 |
| 75–250 | 55 | 67.3 | 35,813 | 8,876 | 5.54 |
| 100–250 | 44 | 68.2 | 24,291 | 5,027 | 6.46 |
| **100–200** | **32** | **75.0** | **31,651** | **4,719** | **8.69** |
| 75–200 | 43 | 72.1 | 37,944 | 5,819 | 6.94 |
| 50–200 | 52 | 73.1 | 40,855 | 5,819 | 6.01 |
| 100–175 | 24 | 70.8 | 25,289 | 4,041 | 8.68 |
| 125–250 | 33 | 66.7 | 12,270 | 5,900 | 5.75 |

**Why this is a bigger deal than it looks.** Until tonight the only honest
drawdown dial was the trail gap, and it always charged us for the privilege:

| trail gap | net ₹ | max DD |
|---|---|---|
| 0.5R | 21,221 | 5,213 |
| 0.6R | 28,691 | 5,858 |
| 0.7R | 40,341 | 8,876 |

Rs 100–200 returns ₹31,651 at a drawdown of ₹4,719. That is **more money and
less drawdown than the 0.6R trail** — it dominates the dial rather than sitting
somewhere on it. Rs 50–200 returns ₹40,855 at ₹5,819: slightly more money than
we make today, with a third less drawdown. Nothing else tested in this project
has done that.

Note also that `125–250` is poor (₹12,270). So this is not "expensive contracts
are better". Both tails of the premium distribution hurt, and each tail on its
own accounts for roughly a third of the drawdown.

### The two bounds do not have equal evidence, and this matters

Sixty-four trades split into buckets is exactly where noise impersonates
structure, so I cut the sample chronologically in half and scored each half
separately. Points per trade, against a 4.98 baseline:

| bucket | first half (Aug 25 – Mar 26) | second half (Mar – Aug 26) |
|---|---|---|
| under ₹100 | 2.50 *(n=12)* | 0.57 *(n=8)* |
| ₹100–200 | 14.67 *(n=13)* | 5.96 *(n=18)* |
| ₹200+ | −6.32 *(n=7)* | **+5.08** *(n=6)* |

**The lower bound is solid.** Cheap contracts badly underperform in both halves,
and there is a mechanism rather than just a number: they capture 1.7 points per
trade, and a ₹1 round-trip spread is more than half of that. They were never
going to clear their own transaction costs.

**The upper bound is not established.** The ₹200+ bucket flips sign between the
two halves. It is also confounded with market regime — those trades carry a
median spot of 23,466 and ATM IV of 23.4, against 24,709 and IV 11.8 for the
₹100–150 bucket. Excluding them is partly just excluding an earlier,
higher-volatility market, which is not a property that will repeat on demand.

### The trade-off disappears once you charge for the spread

Everything above assumes we get filled at the mid. We do not. Charging a half
bid-ask on each leg, net at ₹1,00,000:

| round-trip spread | 50–250 *(shipped)* | 100–200 | 100–250 |
|---|---|---|---|
| ₹0.00 | **40,341** | 31,651 | 24,291 |
| ₹0.50 | **33,692** | 30,155 | 21,890 |
| ₹1.00 | 19,970 | **20,782** | 19,490 |
| ₹2.00 | 8,712 | **17,708** | 16,852 |

The shipped band's advantage exists **only at zero spread**. It is gone by a ₹1
round trip and by ₹2 the narrow band earns more than double. The shipped band
keeps 22% of its profit at a ₹2 round trip; 100–200 keeps 56%.

That is the real argument for this change, and it is a much stronger one than the
raw table. The wide band's extra profit is made of cheap contracts that capture
1.7 points each — profit that exists on paper and gets handed to the market maker
in practice. **Under any realistic execution assumption, 100–200 is better on
money, drawdown and win rate simultaneously.**

### What I would do

Set **`premium_min=100, premium_max=200`**. On the numbers above this is not a
trade-off at all unless we are filling at the mid, which we are not: it moves all
three things you have asked for repeatedly — win rate 68.8% → 75.0%, points per
trade 4.98 → 8.69, drawdown down 47% — and at any spread of ₹1 or more it also
makes more money.

The honest caveat stays: the *upper* bound is the one that failed the split-half
test. If you want only the well-evidenced half, `100–250` is the conservative
choice — it is spread-robust for the same reason (₹16,852 at a ₹2 round trip) and
cuts drawdown 43%, but it does not lift the win rate.

This is a one-line change at `options_tracker/nifty_trail_strategy.py`. I have
not applied it — which variant you want depends on your drawdown tolerance, and
that is your call.

---

## 2. Your reversal idea: it works, and I still cannot call it established

You asked: once you hit the SL, what if you reverse — stopped out of a CE, buy a
PE and sell it. Tested on all 18 stop events, same 10% stop and 0.7R trail,
entered on the bar after the stop.

| follow-up | n | win% | mean R | total R | effect on the ledger |
|---|---|---|---|---|---|
| **reverse** (opposite ATM) | 18 | 61.1 | +0.16 | +2.96 | ₹40,341 → ₹47,289 |
| re-enter the same contract | 18 | 44.4 | −0.29 | −5.18 | −₹19,562 |
| re-enter on reclaim | 6 | 33.3 | −0.53 | −3.15 | −₹9,227 |

Your instinct beat mine here. From the diagnostic — 72% of stopped contracts
later trade back above our entry — I expected re-entry to work and reversal to
fail. The data says the opposite, clearly.

**Two reasons I am not recommending it yet.** First, the drawdown goes the wrong
way: ₹8,876 → ₹12,950, which is most of the +₹6,947 handed straight back as
risk. Second, and more important, I ran a zero-skill control — the same option
type on the same day, entered at a *random* minute, 400 times over. The real
reversal scores +2.96R; the random books have a median of −0.59R with a 5–95
range of −7.78 to +9.07. The real timing beats **76%** of them. That is inside
the noise band. A real edge should be up near 95%.

There is a hint of where the edge might be concentrated:

| what the index did before the stop | n | win% | total R |
|---|---|---|---|
| moved >25 pts against us | 8 | 75.0 | +3.02 |
| moved 10–25 pts | 7 | 42.9 | −1.11 |
| barely moved (<10 pts) | 3 | 66.7 | +1.05 |

Reversing only after a *genuine* adverse move looks better than reversing after
every stop. But that is 8 trades. It is a hypothesis for the next 6 months of
data, not a rule to trade now.

---

## 3. Stop hunting: real, precisely measured, and not repairable

**It is real, and worse than I expected.** Of 64 trades, 18 hit the initial stop.
Of those 18:

- **13 (72%)** later traded back above our entry price
- **12 (67%)** went on to reach +1R
- the index kept moving against us into the close in only **5 (28%)**
- the stop triggered after a median of just **24 index points**
- **7 of 18** fired with the index *better* than −20 points — no adverse move at
  all, just decay and a widening spread

A 10% stop on the premium is a stop on the wrong variable. On a ~0.4 delta
contract it fires after about 25 index points, which is inside ordinary
minute-scale noise. That is a genuine design flaw, correctly identified.

**And every repair loses.** Thirteen variants, all sized on their own hard stop
so none of them wins by quietly taking more risk. Points per trade is
sizing-free and is the column to read:

| stop rule | n | win% | pts/trade | net ₹1L | DD ₹1L |
|---|---|---|---|---|---|
| **shipped trigger** | 64 | 68.8 | **4.98** | 40,334 | 8,886 |
| close-confirmed stop | 64 | 68.8 | 4.10 | 22,099 | 11,717 |
| close-confirmed + hard 15% | 58 | 69.0 | 4.14 | 14,738 | 6,413 |
| spot must move 15 pts | 58 | 72.4 | 4.70 | 17,537 | 6,165 |
| spot must move 25 pts | 58 | 72.4 | 4.52 | 16,464 | 6,165 |
| spot must move 35 pts | 42 | 69.0 | 4.37 | 5,867 | 4,918 |
| spot 25 pts + close confirm | 58 | 70.7 | 3.98 | 14,986 | 6,852 |
| no stop for 3 bars | 58 | 72.4 | 4.84 | 18,929 | 5,563 |
| no stop for 5 bars | 58 | 72.4 | 4.59 | 18,080 | 6,187 |
| plain wider stop 15% | 53 | 60.4 | −0.34 | −1,572 | 11,376 |
| plain wider stop 20% | 40 | 62.5 | −0.43 | −1,091 | 6,996 |
| no soft stop, hard 12% | 64 | 68.8 | 4.15 | 20,464 | 9,869 |
| no soft stop, hard 15% | 57 | 71.9 | 3.07 | 14,629 | 8,039 |
| no soft stop, hard 20% | 40 | 72.5 | 3.53 | 7,325 | 4,772 |

Three things worth pulling out of that table:

**It is not a wick problem.** Close-confirmation leaves the stop count at exactly
18 and only worsens the fills. The premium genuinely *closes* 10% down and
recovers later. So "wait for the bar to close" — the standard advice — does
nothing here.

**Confirmation rules trade points for win rate.** Requiring the index to actually
move before arming the stop lifts win rate 68.8% → 72.4% and cuts drawdown 31%.
But points per trade *falls*. The rule converts small losses into small wins
while making the losses that do survive about 1.5× bigger. Win rate goes up;
the money does not.

**Wider stops fail for a reason worth knowing.** Rows 10 and 11 are catastrophic
because the stop and the trail are not independent in our design: risk R is
`entry − stop`, and the trail is 0.7 *of that R*. Widening the stop silently
widens the exit too. So I tested the decoupled version — remove the soft stop
entirely, keep the trail pinned to the original 10% unit, let a hard stop be the
only way to lose (last three rows). It still loses. **The 10% stop is doing real
work.** It is not merely harvesting noise, even though 72% of the time it looks
like it is.

**The clincher.** The best repair is "no stop for 3 bars": ₹18,929 at DD 5,563.
Compare that to simply setting the trail to 0.5R: ₹21,221 at DD 5,213 — more
money at less drawdown. At 0.6R it is ₹28,691 at DD 5,858, which is 52% more
money for 5% more drawdown. **Every stop repair is dominated by the trail dial we
already have.** This line of inquiry is closed.

---

## 4. Volume profile: the lead you picked came back null

You singled this out — "the one lead where the evidence says the payoff could be
large rather than marginal". You were right that it was the best remaining lead.
It did not work.

I built a synthetic index volume profile from the 49 constituents' rupee
turnover, 5-point bins, 70% value area. The profile itself is sound: median value
area 90 points wide, close lands inside the value area on 64% of sessions, and
the POC sits a median 64 points from the open while tracking VWAP within 29 — so
it is a genuinely distinct level, not the open wearing a disguise.

Scored as targets on the same 64 trades:

| level | median R away | reached | vs trail-only | beats shuffles |
|---|---|---|---|---|
| prior POC | 4.95 | 17% | −10,248 | 34% |
| prior VAH | 5.73 | 8% | +105 | 70% |
| prior VAL | 5.58 | 6% | −3,609 | 44% |
| naked POC | 5.38 | 11% | −12,036 | 42% |
| developing VAH | 0.26 | 67% | −30,614 | — |
| developing VAL | 0.58 | 67% | +18,236 | — |
| nearest profile level | 3.07 | 25% | −30,482 | 46% |
| VA width from entry | 1.93 | 25% | −27,453 | 25% |
| VA extension 0.5× | 0.94 | 54% | −41,570 | 77% |
| VA extension 1.0× | 1.06 | 53% | −54,140 | 62% |
| VA extension 1.5× | 1.90 | 31% | −57,009 | 10% |

The value-area extensions were the real hope. Their distance scales with the
day's own value-area width rather than with our stop — the one property every
price-only level we had tried lacked. They land in exactly the right 1–2R band,
and they still lose.

**This closes the whole family.** Across price levels, round numbers, IV sigma,
opening range, POC, VAH, VAL, naked POC and value-area extensions, only *distance
in R* ever mattered and *provenance* never did. No static line predicts where a
given trade's high will be. I would stop drawing lines.

The +220% ceiling from perfect target placement is still real — it just is not
reachable by better lines. If it is reachable at all it is through an exit that
reads the trade as it develops.

---

## 5. The thing that actually threatens this strategy

This is the finding I would want you to take away even if you ignore everything
else.

I modelled a half bid-ask **in rupees** rather than as a percentage, because a
quoted spread on NIFTY weeklies is roughly a fixed number of paise whatever the
premium. Net at ₹1,00,000 on the shipped band:

| round-trip spread | net ₹ | kept |
|---|---|---|
| ₹0.00 | 40,341 | 100% |
| ₹0.50 | 33,692 | 84% |
| ₹1.00 | 19,970 | 50% |
| ₹2.00 | 8,712 | 22% |

**The strategy captures about 5 premium points per trade. A ₹1 round trip is a
fifth of the entire edge; ₹2 is nearly four fifths of it.** No parameter I have
tuned across this whole project moves the result as much as execution quality
does. Limit orders, avoiding the first and last minutes, and not chasing fills
are worth more than any rule change on this list.

This is also what makes section 1's recommendation more than a preference: the
narrow premium band is not just less volatile, it is *less exposed to this*. It
keeps 56% of its profit at a ₹2 round trip where the shipped band keeps 22%.

This also settles the strike question. I tested substituting the strike at the
same entry minute, 3 ITM through 2 OTM. Note these columns are the *half* bid-ask
paid on each leg, so halve them to compare against the round-trip table above:

| strike | ₹0.00 | ₹0.25 | ₹0.50 | ₹1.00 | ₹1.50 |
|---|---|---|---|---|---|
| 3 ITM | 3,557 | 4,764 | 1,893 | 2,686 | −184 |
| 2 ITM | 17,350 | 14,062 | 8,896 | 5,829 | −908 |
| 1 ITM | 34,141 | 27,358 | 19,018 | 9,752 | 2,234 |
| **ATM** | 32,125 | 27,659 | 16,408 | 16,046 | **3,444** |
| 1 OTM | 39,696 | 28,464 | 25,831 | −4,849 | −4,495 |
| 2 OTM | 49,705 | 25,997 | 12,049 | −5,084 | −26,437 |

2 OTM looks 55% better than ATM at zero spread and is *worse* by ₹0.25. **ATM
degrades most gracefully**, which validates the `moneyness="ATM"` setting we
already ship, and kills the cheap-OTM idea before it costs us anything.

One hypothesis of mine died here too, and it is worth recording so nobody retries
it: I expected ITM contracts to buy more stop room. They do not. Stop room is
24–26 index points at *every* strike from 3 ITM to 2 OTM, because 10% of a larger
premium scales almost exactly with the higher delta.

---

## 6. Blocked: the Dhan token has expired

The token at `Downloads/Dhan Temp Token.txt` was issued 2026-08-12 09:52:50 and
expired 2026-08-13 09:52:50. The file's timestamp looks current, which is
misleading — the contents are stale.

Blocked until it is replaced: futures capture (`research/capture_futures.py`) and
any BANKNIFTY work. Everything in this document ran off the existing local cache
and is unaffected.

---

## 7. What I would now stop testing

Each of these is closed by evidence in this document or earlier work, and I would
rather we spend the time elsewhere than re-derive them:

- **Better levels or targets** — every family tested; only distance matters.
- **Stop-trigger repairs** — 13 variants, all dominated by the trail dial.
- **Moneyness / ITM for stop room** — the room is identical at every strike.
- **Position-size and equity throttles** — closed earlier; ₹1L is the binding
  constraint and a third of trades are already a single lot.
- **Constituent breadth** — no edge, established earlier.
- **Swing-pivot reversal trading** — fully tested and negative, established
  earlier.

## 8. What I would test next

1. **Re-check the premium band on fresh data.** The lower bound is well
   supported; the upper bound needs the next few months to confirm or kill it.
   This is the highest-value single question outstanding.
2. **Reversal, conditioned on a real adverse move.** The >25-point subset is the
   only version with a plausible story. It needs more than 8 trades.
3. **Execution.** Measure what we are *actually* paying in slippage against the
   quoted mid. Given section 5, this is worth more than any further parameter
   search — and it is the one number in this document I have had to model rather
   than observe.

---

*Runs behind this: `research/trade_quality.py`, `premium_band.py`, `stop_hunt.py`,
`stop_rules.py`, `reversal.py`, `moneyness_lab.py`, `volume_profile.py`,
`profile_targets.py`.*
