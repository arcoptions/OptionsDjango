"""Download real deep-OTM stock option history from the live option chain.

WHY THIS EXISTS.  Every stock-option result in this programme was measured on
the rolling ATM+-3 feed, which spans about +-2% of spot on a Rs50 strike ladder.
The trades the brief is about do not live there.  The worked example that forced
this: HAL on 2026-08-05, spot 4,637.  The rolling cache's furthest call was the
4,750 strike at Rs92.  The contract that actually went Rs22.20 -> Rs199.00 that
week -- 8.96x, verified bar by bar against security id 97484 -- was the 5,000
strike, ATM+7, 7.8% out of the money.  The cache could not see it, so no study
run on the cache could rule it in or out.  Reporting those studies as a null on
"stock options" overstated their reach, and this file is the correction.

WHAT IS AND IS NOT REACHABLE, established by `chain_depth.py`:
  - The chain exposes a real security id for every listed strike, -45% to +35%.
  - The intraday and historical endpoints honour those ids.
  - EXPIRED contracts return DH-907. Their ids are published nowhere, so a
    year-long deep-OTM backtest on real contracts is impossible. Not a matter of
    effort -- the data is not served.
  - A LIVE contract carries roughly SIX WEEKS of real trading. It appears to
    carry three months, and that appearance is a trap: see the padding note in
    `fetch()`. HAL-Aug2026-5000-CE reports bars from 2026-05-29 but the first 26
    are a frozen line at zero volume; it actually starts trading 2026-07-07.

So this cache is real, deep, and about six weeks long. Six weeks is one regime
and cannot settle a strategy on its own. It CAN settle three things the rolling
feed never could: the true base rate of 2x/5x/10x by moneyness, the real bid-ask
at strikes that thin out, and whether the far tail behaves as the ladder study's
gradient predicted when it ran out of rungs.

SCOPE.  Strikes are taken across the range worth buying rather than the whole
chain: calls from just ITM out to +25%, puts from just ITM down to -25%. Deep
ITM is not the trade and doubles the download for nothing.

DO NOT ANALYSE A PARTIAL RUN.  This is the single thing most likely to be got
wrong here, and it already cost a day.  The worklist is SORTED nearest-strike
first so that an interrupted run is still a usable dataset -- which is exactly
what makes any prefix of it a biased sample, because the strikes nearest today's
price are the ones the underlying walked towards.  The first 1,500 contracts said
a +8-12% OTM call reaches 2x within ten sessions 63% of the time; the finished
6,366 said 26.5%.  Same band, same code.  See `build_worklist()`.
"""
import datetime as dt
import glob
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django  # noqa: E402

django.setup()

from options_tracker.models import StockEquityCandle, TrackedStock  # noqa: E402
from options_tracker.stock_data import _headers  # noqa: E402

HISTORICAL = "https://api.dhan.co/v2/charts/historical"
MASTER = "research/scrip_master.csv"
OUT = "research/deep_otm.parquet"

FROM, TO = "2026-04-01", "2026-08-18"
SPOT_FROM = dt.date(2026, 4, 1)
CALL_BAND = (0.98, 1.25)   # strike / spot, evaluated against EVERY day's spot
PUT_BAND = (0.75, 1.02)
EXPIRIES = ("2026-08-25", "2026-09-29")
# Measured, not guessed: two competing processes at 10 workers each sustained
# ~4.3 contracts/s between them, so the ceiling is concurrency-bound rather than
# the ~1 req/s a single-threaded probe suggested.
WORKERS = 20
SHARD_EVERY = 1500         # contracts between partial writes

# Reentrant: the progress line is emitted from inside the counter lock, and a
# plain Lock deadlocks the whole pool the first time that happens.
_lock = threading.RLock()
_done = {"n": 0, "ok": 0, "bars": 0, "fail": 0, "throttled": 0}
_local = threading.local()
# Strikes that are listed but return nothing. 58% of the chain on the first run,
# and without a ledger a top-up re-asks for every one of them -- roughly 8,900
# wasted requests, half an hour of quota, to be told "no" a second time.
EMPTY_LEDGER = "research/deep_otm_empty.csv"
_empty = set()


def log(msg):
    with _lock:
        print("[{:%H:%M:%S}] {}".format(dt.datetime.now(), msg), flush=True)


def session():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


# Built once. `_headers()` reads credentials through the ORM, and calling it per
# request put a SQLite query in front of every fetch from six threads at once.
HEAD = {}


def _bump(ok, sid=None, bars=0, throttled=False):
    """Advance the counter and log every 250 CONTRACTS, hit or miss.

    This has to be shared by the success and failure paths. Keeping it in the
    success branch only made the run look stalled for eight minutes: the
    worklist is sorted by distance from spot, so its tail is far strikes that
    were listed and never traded, and past 6,500 contracts 62% returned no data.
    The counter sailed past seven multiples of 250 without printing one line.

    `throttled` exists because the two ways of getting nothing back are NOT the
    same fact and must not share a ledger. DH-907 on a listed strike means the
    contract has never traded, which is permanent and worth remembering. Five
    exhausted retries against DH-904 means the SERVER was busy, which is
    temporary -- and recording it as never-traded would delete that strike from
    every future run of this file. That is exactly the shape of error that has
    already cost this programme two results: missing data that looks like a fact
    about the market and is really a fact about the collection.
    """
    with _lock:
        _done["n"] += 1
        _done["ok" if ok else "fail"] += 1
        _done["bars"] += bars
        if throttled:
            _done["throttled"] += 1
        elif not ok and sid is not None:
            _empty.add(sid)
        if _done["n"] % 250 == 0:
            log("  {:,}/{:,} contracts, {:,} with data, {:,} bars, {:,} empty, "
                "{:,} throttled".format(
                    _done["n"], _done["total"], _done["ok"], _done["bars"],
                    _done["fail"] - _done["throttled"], _done["throttled"]))


def fetch(row):
    """One contract's daily history. Backs off through throttling rather than
    dropping the contract, because a silently missing strike looks identical to
    a strike that never traded."""
    payload = {"securityId": str(row["sid"]), "exchangeSegment": "NSE_FNO",
               "instrument": "OPTSTK", "expiryCode": 0, "oi": True,
               "fromDate": FROM, "toDate": TO}
    # Two budgets, because throttling and breakage are different problems. A
    # DH-904 burst limit on this endpoint lasted a full HOUR on 2026-08-18 and
    # a shared five-attempt budget with a 0.4s base gives up after 12 seconds,
    # so 250 contracts were abandoned to a condition that cleared on its own.
    # Waiting is nearly free here -- the contract is not going anywhere -- while
    # giving up costs a whole re-run to recover.
    throttle_left, error_left, delay = 20, 5, 0.4
    while throttle_left > 0 and error_left > 0:
        try:
            r = session().post(HISTORICAL, json=payload, headers=HEAD, timeout=30)
        except requests.RequestException:
            error_left -= 1
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            throttle_left -= 1
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
            continue
        if r.status_code != 200:
            # DH-907 on a listed strike means it has never traded. That is a
            # fact about liquidity, not an error, and it is worth recording.
            _bump(False, row["sid"])
            return None
        j = r.json()
        if not j.get("close"):
            _bump(False, row["sid"])
            return None
        n = len(j["close"])
        oi = j.get("open_interest") or [0] * n
        # THE PADDING TRAP.  Ask for a date before the contract was listed and
        # the API does not omit it -- it returns a frozen price at zero volume
        # and zero OI.  HAL-Aug2026-5000-CE looks like it starts 2026-05-29 and
        # actually starts 2026-07-07; the first 26 "bars" are a flat line at
        # 162.15.  Kept, those manufacture weeks of zero-volatility history and
        # would make every contract look calm before it moved.  Only the LEADING
        # run is cut: a zero-volume day after listing is real illiquidity, and
        # that is a fact this study specifically needs.
        start = 0
        while start < n and j["volume"][start] == 0 and oi[start] == 0:
            start += 1
        if start >= n:
            _bump(False, row["sid"])
            return None
        sl = slice(start, n)
        n = n - start
        out = pd.DataFrame({
            "ts": [dt.datetime.fromtimestamp(t, dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)
                   for t in j["timestamp"][sl]],
            "open": j["open"][sl], "high": j["high"][sl], "low": j["low"][sl],
            "close": j["close"][sl], "volume": j["volume"][sl], "oi": oi[sl],
        })
        out["symbol"] = row["symbol"]
        out["expiry"] = row["expiry"]
        out["strike"] = row["strike"]
        out["kind"] = row["kind"]
        out["sid"] = row["sid"]
        out["lot"] = row["lot"]
        _bump(True, row["sid"], n)
        return out
    # Fell out of the retry loop, which only happens on repeated 429/5xx/network
    # failure. The strike is unknown, NOT empty -- see `_bump`.
    _bump(False, None, throttled=True)
    return None


def shard_paths():
    return sorted(glob.glob(OUT.replace(".parquet", ".part*.parquet")))


def existing_sids():
    """Contracts a top-up run should not ask for again.

    Two populations, and missing the second is expensive. The first is the
    contracts already downloaded -- the corrected band is a strict superset of
    the biased one, so everything on disk stays valid. The second is the strikes
    that were asked for and returned NOTHING, which is 58% of the chain and
    invisible in the parquet precisely because there is no data to store. Without
    the ledger a top-up spends half an hour being told "no" a second time.
    """
    seen = set()
    for p in shard_paths() + ([OUT] if os.path.exists(OUT) else []):
        try:
            seen.update(pd.read_parquet(p, columns=["sid"])["sid"].unique().tolist())
        except Exception as exc:      # a shard still being written is not fatal
            log("  could not read {}: {}".format(p, exc))
    have = len(seen)
    if os.path.exists(EMPTY_LEDGER):
        prior = set(pd.read_csv(EMPTY_LEDGER)["sid"].tolist())
        _empty.update(prior)
        seen |= prior
        log("  {:,} with data, {:,} known-empty".format(have, len(prior)))
    return seen


def write_ledger():
    """Persist the never-traded strikes, merged with anything already recorded."""
    if not _empty:
        return
    pd.DataFrame({"sid": sorted(_empty)}).to_csv(EMPTY_LEDGER, index=False)
    log("{:,} listed-but-never-traded strikes recorded in {}".format(
        len(_empty), EMPTY_LEDGER))


def build_worklist():
    """Every strike that was inside the band on ANY day of the window.

    THE TRAP IS THE SORT AT THE BOTTOM, NOT THE BAND.  Ordering by distance from
    today's spot makes a partial run usable, and "usable" is not "unbiased": the
    prefix is precisely the strikes sitting at today's price, which run backwards
    are the strikes the underlying WALKED TOWARDS.  Reproducing the first 1,500
    by-distance out of the finished 6,366 brings the fake edge straight back --
    2x at 63.0% against a true 26.5%, median hold 1.28x against a true 0.61x --
    and the far-OTM share of the sample collapses 37.5% -> 8.7% across the window
    because for a strike K ~ S_today, moneyness on day D is S_today/S_D - 1,
    which goes to zero at the end by construction.  Finish the download, or
    shuffle within the sort key, before believing anything measured off it.

    THE BAND IS A SEPARATE AND SMALLER FLAW.  `strike / spot_TODAY` excludes
    strikes that were in band on earlier days -- both those that were OTM early
    and went ITM (winners) and those in band at the high that spot then fell away
    from (losers).  It cuts both tails, so it narrows the sample rather than
    manufacturing an edge, but there is no reason to accept the narrowing.  The
    fix is as-of-date: a strike qualifies if it was in band against the spot on
    any session, i.e. strike lies in [lo * min_spot, hi * max_spot].  Stocks
    ranged a median 21% peak-to-trough here, so this is 28,118 contracts against
    15,280, and `existing_sids()` makes it a resumable top-up.
    """
    span = {}
    for symbol in TrackedStock.objects.filter(is_active=True).values_list("symbol", flat=True):
        closes = (StockEquityCandle.objects
                  .filter(symbol=symbol, timestamp__gte=SPOT_FROM)
                  .values_list("close", flat=True))
        vals = [float(c) for c in closes if c is not None]
        if vals:
            span[symbol] = (min(vals), max(vals), vals[-1])
    log("spot range known for {} tracked symbols".format(len(span)))

    done = existing_sids()
    if done:
        log("{:,} contracts already on disk -- they will be skipped".format(len(done)))

    d = pd.read_csv(MASTER, low_memory=False)
    o = d[(d["SEM_INSTRUMENT_NAME"] == "OPTSTK") & (d["SEM_EXM_EXCH_ID"] == "NSE")].copy()
    o["symbol"] = o["SEM_TRADING_SYMBOL"].astype(str).str.split("-").str[0]
    o["expiry"] = o["SEM_EXPIRY_DATE"].astype(str).str[:10]
    o = o[o["expiry"].isin(EXPIRIES) & o["symbol"].isin(span)]

    work = []
    for _, r in o.iterrows():
        sid = int(r["SEM_SMST_SECURITY_ID"])
        if sid in done:
            continue
        lo_s, hi_s, last = span[r["symbol"]]
        k = float(r["SEM_STRIKE_PRICE"])
        band = CALL_BAND if r["SEM_OPTION_TYPE"] == "CE" else PUT_BAND
        if not (band[0] * lo_s <= k <= band[1] * hi_s):
            continue
        work.append({"symbol": r["symbol"], "expiry": r["expiry"],
                     "strike": k, "kind": r["SEM_OPTION_TYPE"],
                     "sid": sid, "lot": float(r["SEM_LOT_UNITS"]),
                     "spot_now": last, "dist": abs(k / last - 1.0)})

    # The endpoint runs at roughly six requests a second, so the full list takes
    # tens of minutes. Order it so a partial run is still a usable dataset:
    # every symbol's nearest strikes first, then progressively further out.
    work.sort(key=lambda w: w["dist"])
    return work


def main():
    HEAD.update(_headers())
    work = build_worklist()
    _done["total"] = len(work)
    log("{:,} contracts to pull across {} symbols, {} expiries".format(
        len(work), len(set(w["symbol"] for w in work)), len(EXPIRIES)))
    log("bands: calls {}-{} x spot, puts {}-{} x spot".format(*CALL_BAND, *PUT_BAND))

    frames = []
    # Continue the numbering rather than restarting it, or a top-up run
    # overwrites the shards the first run already earned.
    shard = len(shard_paths())
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for out in pool.map(fetch, work):
                if out is not None:
                    frames.append(out)
                # Partial writes. An hour of downloading is too much to hold in
                # memory only and lose to one bad exit.
                if len(frames) and len(frames) % SHARD_EVERY == 0:
                    path = OUT.replace(".parquet", ".part{}.parquet".format(shard))
                    pd.concat(frames, ignore_index=True).to_parquet(path, index=False)
                    log("  shard -> {}".format(path))
                    frames, shard = [], shard + 1
    finally:
        # Worth keeping even from an interrupted run: a strike that answered
        # "never traded" once does not need asking again.
        write_ledger()

    # Rebuild from EVERY source, not just the shards. The tail of a run -- the
    # last partial batch that never reached SHARD_EVERY -- exists only inside
    # the previous OUT, so a top-up run that reads shards alone silently drops
    # up to 1,499 contracts' worth of the first run's work.
    prior = [pd.read_parquet(OUT)] if os.path.exists(OUT) else []
    all_frames = prior + [pd.read_parquet(p) for p in shard_paths()] + frames
    if not all_frames:
        log("nothing returned")
        return
    df = pd.concat(all_frames, ignore_index=True)
    # A contract can legitimately appear in two sources (prior OUT and a shard).
    # One bar per contract per timestamp is the invariant; enforce it rather
    # than trusting the bookkeeping.
    before = len(df)
    df = df.drop_duplicates(subset=["sid", "ts"], keep="last").reset_index(drop=True)
    if before != len(df):
        log("dropped {:,} duplicate bars ({:.1%})".format(
            before - len(df), (before - len(df)) / before))
    df.to_parquet(OUT, index=False)
    log("wrote {} -- {:,} bars, {:,} contracts, {} symbols".format(
        OUT, len(df), df["sid"].nunique(), df["symbol"].nunique()))
    log("span {} .. {}".format(df["ts"].min().date(), df["ts"].max().date()))
    log("{:,} listed strikes never traded ({:.0%} of the chain we asked for)".format(
        _done["fail"], _done["fail"] / max(_done["total"], 1)))


if __name__ == "__main__":
    main()
