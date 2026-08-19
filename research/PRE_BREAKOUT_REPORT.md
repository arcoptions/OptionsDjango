# Can a breakout be seen coming?

**22 breakout strategies, 61,992 stock-days, 189 F&O names, Mar 2025 – Jul 2026.**

Reproduce with `python research/breakout_study.py`. Raw table in
`research/breakout_results.csv`, full console output in
`research/breakout_output.txt`, daily panel built by
`research/build_daily_panel.py`.

---

## The answer in one paragraph

**No coiling signal anticipates a breakout.** Every squeeze, NR7, inside bar,
volume dry-up and volatility-contraction pattern tested lands within noise of
its own control — matched lift between 0.86 and 1.01, none with |t| above 1.2.
The one setup that looked like it worked, a squeeze in a relatively strong
stock, turns out to be **entirely relative strength**: strip the squeeze out and
every number improves. Relative strength itself does genuinely predict *which*
stocks make a +10% move — 1.28× the rate of same-volatility peers on the same
day, t +2.8, stable across both halves of the sample — but it does **not**
predict return, and it does not survive being traded. This is a null, but a
much better-behaved one than the option programme: nothing here loses money the
way option buying did. It simply doesn't pay enough to bother.

---

## Why this was worth testing at all

The stock-option programme closed on friction: a real +1.02% gross edge that
could not carry a 1.64% round-trip toll
(`research/INTRADAY_REPORT.md`). At the **stock** level the toll is about
**0.30%** — delivery STT 0.20% both sides, stamp/exchange/GST ~0.02%, spread
~0.08% on liquid F&O names. That is **5.5× cheaper**. A signal far too small to
survive as an option can be perfectly real in cash, so the coiling family
deserved a clean test on its own terms rather than inheriting the option
verdict.

## How the measurement was kept honest

Four controls, each of which has killed a result in this project before.

**No look-ahead.** Signals are computed from data through day *t*'s close;
the position is entered at day *t+1*'s **open**. Every rolling extreme is
shifted, so the level a breakout must clear was knowable before the bar that
cleared it. A prior option test returned +6.57% (t +5.09) purely from reading
the close of the bar it bought the open of.

**Market-relative, not raw.** The headline number is excess over the *same
day's* cross-sectional mean across the whole universe. Raw return would reward
a signal for firing on strong market days. It also absorbs most of the
universe's survivorship tilt — these are today's 189 F&O names, so they
over-represent stocks that did well, but the control is drawn from that same
tilted universe.

**Volatility-matched.** This one changed the answer. A "+10% in 10 days" label
is a *volatility* label as much as a direction one, so a coiled stock is
penalised for being coiled. Against the whole universe `squeeze_on` scores lift
0.84 and looks actively harmful; against same-day stocks *in its own volatility
decile* it scores **0.98**. The apparent effect was the label, not the signal.
Every lift below is reported both ways, plus against a second label — a move of
3× the stock's own ATR — that is volatility-neutral by construction.

**De-overlapped significance.** Day-clustering removes the correlation between
stocks. It does nothing about **serial overlap**: a 10-day forward return
sampled every session shares nine tenths of its window with the session before,
so the daily means are autocorrelated and t is inflated by roughly √horizon.
Both are reported. The de-overlapped column is the one that counts.

*Two pipeline checks.* The all-bars control row has an excess of exactly zero by
construction; its measured daily value is 4.1e-17, so its printed t is
floating-point dust, not a result. And permuting each signal across symbols
within a day drives its edge to −0.002% / −0.028% / −0.037% where it must be
zero — no leakage.

---

## 1. Anticipation: does coiling predict anything?

| signal | n | /day | +10% lift | **matched** | 3×ATR **matched** | lift t | ex+10d | honest t |
|---|---|---|---|---|---|---|---|---|
| squeeze_on | 14,936 | 45.5 | 0.84 | **0.98** | 0.96 | −0.7 | −0.09% | −0.9 |
| squeeze_6day | 8,131 | 25.1 | 0.81 | **1.01** | 1.00 | −0.3 | −0.09% | −0.9 |
| nr7 | 9,529 | 29.1 | 0.91 | **0.97** | 1.00 | −0.3 | +0.00% | +0.4 |
| inside_bar | 8,121 | 24.8 | 0.87 | **0.98** | 1.01 | −0.5 | +0.02% | −0.2 |
| bbw_bottom_decile | 6,716 | 25.2 | 0.50 | **0.86** | 0.92 | −1.2 | −0.32% | −0.8 |
| atr_contraction | 239 | 1.7 | 1.25 | **0.93** | 0.48 | −0.5 | +0.62% | +0.3 |
| volume_dryup | 11,533 | 35.2 | 0.82 | **0.92** | 0.95 | −0.6 | −0.08% | −0.1 |
| coil_3bar | 3,363 | 10.4 | 0.92 | **0.96** | 1.01 | −0.0 | −0.02% | +0.2 |

Base rate is 11.75% for +10%, 17.04% for 3×ATR. **Not one signal clears its
matched control.** `squeeze_6day` — six consecutive sessions of a genuine TTM
squeeze, the textbook coil — lands at 1.01 and 1.00, which is the cleanest way
of saying a coil tells you nothing about whether a move is coming.

The raw lifts (0.50–0.92) look like coiling actively *predicts against* a
breakout. It doesn't. Those columns are measuring that quiet stocks are quiet.
`bbw_bottom_decile` carries a mean ATR of 2.43% against the universe's 2.80%;
once matched it recovers from 0.50 to 0.86.

`atr_contraction` fires 239 times, gets an eye-catching +0.62% at 10 days, and
should be ignored — its t is +0.3, its two halves are +0.83% and −0.27%, and its
3×ATR matched lift is 0.48.

## 2. Confirmation: what happens after the level goes

| signal | n | matched lift | ex+1d | ex+3d | ex+10d | ex+20d | honest t (10d) |
|---|---|---|---|---|---|---|---|
| donchian20 | 4,182 | 1.13 | −0.12% | −0.14% | +0.08% | +0.10% | −0.1 |
| donchian55 | 2,260 | **1.29** | −0.10% | −0.02% | +0.30% | +0.47% | +0.3 |
| high_200d | 646 | **1.33** | −0.06% | −0.02% | +0.45% | +0.43% | +0.3 |
| darvas_box | 2,301 | 1.17 | −0.13% | −0.07% | +0.08% | +0.17% | +0.0 |
| squeeze_fire | 528 | 1.01 | +0.04% | −0.13% | +0.10% | −0.04% | +0.2 |
| vol_expansion | 1,030 | 1.13 | −0.12% | −0.13% | +0.04% | +0.17% | −0.1 |
| pocket_pivot | 6,824 | 1.11 | −0.05% | −0.02% | −0.00% | +0.06% | −0.3 |

Confirmation beats anticipation on lift — a stock that has *already* cleared a
55-day or 200-day high is 1.29–1.33× as likely as a same-volatility peer to run
another 10%. That is the momentum effect showing up where you'd expect it. But
the lift t values are +0.3 to +0.8: **not significant**, and the mean returns are
flat. Breaking out fattens both tails, it doesn't shift the middle.

**One consistent pattern is worth naming.** Every confirmation signal is
negative at +1d and +3d — donchian20 −0.12%/−0.14%, darvas −0.13%, vol_expansion
−0.12%, and `squeeze_then_break` −0.14%/−0.29%. Buying the morning after a
breakout costs you 10–14 basis points against the market over the next few days.
Under day-clustering these reach t −2.2 to −4.1; de-overlapped they fall to −1.0
to −1.5, so it is suggestive rather than proven. It is the familiar breakout
give-back, and it is the single most consistent sign in the study.

## 3. Conditioned setups — and the deconfound that killed them

Bare "squeeze on" fires on 45 names a session and says nothing about direction,
so the coil was conditioned the way it's actually traded: near the top of its
base, above the 100-day average, on accumulation, as a VCP near 200-day highs,
and filtered by cross-sectional relative strength.

Exactly one came out positive — `squeeze_rs_top`: matched lift 1.20, +0.17% at
10 days, **+0.58% at 20 days (clustered t +3.4)**, positive in both halves.

It has two ingredients. Separating them settles it:

| | n | matched lift | lift t | ex+5d | ex+10d | ex+20d | honest t (10d) | h1 / h2 |
|---|---|---|---|---|---|---|---|---|
| squeeze_rs_top | 2,030 | 1.20 | +0.8 | −0.00% | +0.17% | +0.58% | +0.6 | +0.14 / +0.19 |
| rs_top_only | 10,944 | 1.27 | **+3.0** | +0.21% | +0.37% | +0.51% | +1.4 | +0.33 / +0.40 |
| **rs_top_NOT_coiled** | 8,914 | **1.28** | **+2.8** | **+0.26%** | **+0.42%** | +0.50% | +1.4 | +0.38 / +0.44 |
| squeeze_rs_middle | 3,067 | 0.97 | −0.5 | −0.10% | −0.15% | −0.14% | −0.7 | −0.17 / −0.14 |

**Taking the squeeze away makes every number better.** Strong stocks that are
explicitly *not* coiled beat strong coiled stocks on matched lift (1.28 vs
1.20), on 5-day return (+0.26% vs −0.00%), on 10-day return (+0.42% vs +0.17%)
and on significance (t +2.8 vs +0.8). The coil isn't neutral here — it is a
drag. `squeeze_rs_middle`, a coil with unremarkable strength, is the most
negative row in the study.

So `squeeze_rs_top` was 60-day relative strength wearing a squeeze costume, and
the costume cost it two thirds of its sample and all of its significance.

## 4. Is relative strength even a factor here?

It survived the deconfound, so it gets the standard test: a real factor pays
monotonically across its own ranking.

| RS decile | +10% hit | ex+10d | ex+20d |
|---|---|---|---|
| 1 (weakest) | 13.0% | **+0.22%** | **+0.37%** |
| 2 | 8.5% | −0.15% | −0.15% |
| 3 | 8.6% | +0.03% | +0.19% |
| 4 | 8.7% | −0.12% | −0.11% |
| 5 | 9.2% | −0.08% | −0.20% |
| 6 | 8.9% | −0.12% | −0.27% |
| 7 | 7.7% | −0.40% | −0.66% |
| 8 | 9.0% | −0.11% | −0.21% |
| 9 | 12.0% | +0.37% | +0.39% |
| 10 (strongest) | 16.9% | +0.37% | +0.64% |

**It is not monotone — it is U-shaped.** Both extremes are positive and the
middle is negative, with the *weakest* decile at +0.22%/+0.37%. There is no
version of momentum that pays you at both ends. What this actually sorts is
attention and dispersion: stocks that have just moved a long way in either
direction have fatter right tails, and a mean-based excess picks that up at both
ends. Decile 7 at −0.40% (t −5.6 clustered) has no economic story at all and is
the clearest sign that this ranking is not a clean factor.

The +10% hit rate *is* U-shaped too but far more lopsided — 16.9% at the top
against 13.0% at the bottom — which is consistent with the honest reading:
**relative strength genuinely raises the chance of a large up-move; it does not
raise the average outcome.**

## 5. Would it survive being traded?

Top-N by relative strength, equal weight, rebalanced every 10 sessions,
**non-overlapping**, charged 0.30% a round trip.

| basket | periods | gross/period | net/period | vs equal-weight | net t | winning periods |
|---|---|---|---|---|---|---|
| top 5 | 33 | +1.13% | +0.83% | **+0.25%** | 1.0 | 58% |
| top 10 | 33 | +0.70% | +0.40% | −0.18% | 0.5 | 52% |
| top 20 | 33 | +1.02% | +0.72% | +0.15% | 1.1 | 61% |
| top 30 | 33 | +1.08% | +0.78% | +0.21% | 1.4 | 61% |
| top 50 | 33 | +0.84% | +0.54% | −0.03% | 1.0 | 52% |
| equal-weight universe | 33 | +0.57% | +0.27% | +0.00% | 0.5 | 42% |

Every basket beats simply holding the universe, and all of them are profitable
net of costs. **And none of it is significant.** The best t is 1.4. The ordering
across basket sizes is 5 > 30 > 20 > 50 > 10 — random. Thirty-three
non-overlapping periods is roughly 16 months; that is not enough to distinguish
+0.78% a fortnight from zero.

Note how well this agrees with the de-overlapped t of +1.4 computed a completely
different way in §3. Two independent routes to the same conclusion is the
reassuring part of an otherwise negative result.

### A correction to my own method

The first version of this study reported `rs_top_NOT_coiled` at **t +4.6** and
`squeeze_rs_top` at **t +3.4**. Those were day-clustered but still overlapping,
and overlap inflates t by about √10 at a 10-day horizon. The honest figures are
**+1.4** and **+0.6**. Day-clustering alone was not enough here, and any earlier
result in this project that used a multi-day forward return sampled daily has
the same inflation in it.

---

## What this means

- **The premise doesn't hold.** Coiling is not a precursor. The literature's
  squeeze/NR7/VCP family is, on this universe and window, a volatility sort with
  no directional content. Once you compare a quiet stock to other quiet stocks,
  the effect is gone.
- **Confirmation carries more information than anticipation** — 1.29–1.33
  matched lift for a 55-day or 200-day high against ~1.00 for every coil — but
  not enough to be significant, and buying the next morning gives back 10–14 bp.
- **The only real signal is relative strength, and the squeeze subtracts from
  it.** That is the finding worth keeping: a strong stock that is *not* coiled
  beats a strong stock that is.
- **Friction is no longer the obstacle.** This is the important contrast with
  the option work. At 0.30% a round trip, every basket in §5 is net positive.
  The problem is no longer that the edge can't carry the toll — it's that the
  edge can't be distinguished from zero.
- **What would change the answer:** more history. 33 non-overlapping periods is
  the binding constraint, not the data quality. Three to four years would take a
  genuine +0.5%/fortnight to t ≈ 2.5–3. Everything needed to re-run it is in
  place, so this is worth revisiting once the panel is longer rather than
  re-derived from scratch.

## Reusable pieces

- `research/build_daily_panel.py` — 15-min cache → daily OHLCV panel, with the
  partial-session filter that stops thin feed days from firing NR7 detectors.
- `research/breakout_study.py` — 22 signals in four families, volatility-matched
  and day-matched controls, both breakout labels, `overlap_safe_t`, the shuffle
  placebo, and the non-overlapping portfolio test.
