"""Build the daily call shortlist and score the picks that have come of age.

    python manage.py build_daily_shortlist --once
    python manage.py build_daily_shortlist --daemon
    python manage.py build_daily_shortlist --rebuild-history 400

WHY A PICK IS NEVER REWRITTEN.  The point of storing the list is to be able to
ask later whether the model was any good, and that question is only meaningful
if the answer was fixed before the outcome was known.  So a LIVE pick is
written once and never touched again: a re-run finds the row and leaves it
alone.  Backfilled picks are recomputable and are updated freely, because they
make no claim to have been known in advance.

WHY THE JOB REFUSES TO RUN ON A HALF-DOWNLOADED SESSION.  The close is what
every feature is built from, so a session that is only partly in the table
produces a list that looks ordinary and is wrong.  A day is only accepted once
enough of the universe has reported, and the job would rather record nothing
than record a plausible-looking list off a broken close.
"""
import datetime as dt
import time

import pandas as pd
from django.core.management.base import BaseCommand
from django.utils import timezone

from options_tracker import shortlist as S
from options_tracker import stock_data
from options_tracker.models import DailyPick, PickSource, TrackedStock

# Evening, not late afternoon. The history API sheds load during the session --
# measured 42-49% success between 10:00 and 14:00 IST against 100% after 18:00 --
# and a build that half-downloads the day is exactly what MIN_SYMBOLS_FOR_SESSION
# below then has to refuse. Waiting three hours costs nothing: the list only has
# to exist before the next morning.
BUILD_AT_HOUR = 18
BUILD_AT_MINUTE = 40

# A session with fewer reporting names than this is still arriving. The
# universe runs to ~180 stocks, so half of it is a generous floor that still
# catches a download that died a third of the way through.
MIN_SYMBOLS_FOR_SESSION = 60

# How stale the newest session may be and still be recorded as LIVE. A LIVE row
# asserts the list was known before the outcome was, and that assertion is only
# true if the job ran when the session was fresh. If the download has been
# broken for a week, the honest outcome is no row at all -- writing one would
# manufacture exactly the hindsight this table exists to rule out. One day of
# slack covers a build that slips past midnight or waits out a holiday.
MAX_LIVE_AGE_DAYS = 1


def next_run(now):
    target = now.replace(hour=BUILD_AT_HOUR, minute=BUILD_AT_MINUTE,
                         second=0, microsecond=0)
    return target if target > now else target + dt.timedelta(days=1)


class Command(BaseCommand):
    help = "Record today's call shortlist and score picks whose five sessions have passed."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true",
                            help="Build once and exit (the default).")
        parser.add_argument("--daemon", action="store_true",
                            help="Stay up and build every evening at 18:40 IST.")
        parser.add_argument("--rebuild-history", type=int, default=0,
                            help="Reconstruct the last N sessions as BACKFILL picks.")
        parser.add_argument("--no-download", action="store_true",
                            help="Use the candles already stored, fetch nothing.")

    def handle(self, *args, **options):
        if options["rebuild_history"]:
            self._rebuild(options["rebuild_history"])
            return
        if options["daemon"]:
            self._daemon(options["no_download"])
            return
        self._build_once(options["no_download"])

    # -- scheduling ---------------------------------------------------------

    def _daemon(self, no_download):
        while True:
            now = timezone.localtime()
            due = next_run(now)
            wait = (due - now).total_seconds()
            self.stdout.write("next build {:%Y-%m-%d %H:%M %Z} ({:.1f}h away)".format(
                due, wait / 3600))
            time.sleep(max(wait, 60))
            try:
                self._build_once(no_download)
            except Exception as error:            # noqa: BLE001
                # A bad afternoon must not take the daemon down -- tomorrow's
                # list is still worth building.
                self.stderr.write(self.style.ERROR("build failed: {}".format(error)))
            time.sleep(90)      # past the slot, so the next target is tomorrow

    # -- the work -----------------------------------------------------------

    def _refresh_equity(self):
        """Top up the candle table. Only the recent window -- the rest is stored.

        Twelve days rather than one, because a long weekend or a failed evening
        would otherwise leave a hole that nothing ever goes back for. The bars
        are written with `ignore_conflicts`, so re-fetching days already held
        costs one request per stock and changes nothing.
        """
        stocks = list(TrackedStock.objects.filter(is_active=True).order_by("priority"))
        end = dt.date.today()
        start = end - dt.timedelta(days=12)
        rows, failed = stock_data.download_equity(stocks, start, end, 15)
        self.stdout.write("equity: {:,} rows returned ({} windows failed)".format(
            rows, failed))

    def _frames(self):
        daily = S.daily_bars()
        if daily.empty:
            return None, None
        return daily, S.features(daily)

    def _build_once(self, no_download):
        if not no_download and dt.date.today().weekday() < 5:
            self._refresh_equity()

        daily, feats = self._frames()
        if feats is None or feats.empty:
            self.stderr.write(self.style.ERROR("no candles -- nothing to build"))
            return

        day = feats["day"].max()
        reporting = int((feats["day"] == day).sum())
        age = (dt.date.today() - day).days
        if reporting < MIN_SYMBOLS_FOR_SESSION:
            self.stderr.write(self.style.WARNING(
                "{} has only {} symbols reporting -- session looks incomplete, "
                "recording nothing".format(day, reporting)))
        elif age > MAX_LIVE_AGE_DAYS:
            self.stderr.write(self.style.WARNING(
                "newest session is {} ({} days old) -- too stale to record as a "
                "live pick. Fix the equity download; use --rebuild-history to "
                "reconstruct it as a backfill.".format(day, age)))
        else:
            hit = S.shortlist(feats, day)
            created = self._write(hit, day, PickSource.LIVE)
            self.stdout.write(self.style.SUCCESS(
                "{}: {} name(s) qualify, {} newly recorded ({} symbols reported)".format(
                    day, len(hit), len(created), reporting)))
            if not len(hit):
                self.stdout.write(
                    "  the tape is quiet -- this is the model declining to trade")

        self._score(daily)

    def _rebuild(self, sessions):
        """Reconstruct past lists so the screen has something to be judged against.

        These are explicitly BACKFILL. They were not known in advance and are
        kept out of every live statistic; mixing them in is exactly the error
        this project has had to withdraw results for before.

        Backfilled rows are replaced wholesale rather than updated in place.
        That is safe precisely because they carry no claim about when they were
        written -- and it is the difference between one query and seven thousand.
        """
        daily, feats = self._frames()
        if feats is None or feats.empty:
            self.stderr.write(self.style.ERROR("no candles -- nothing to rebuild"))
            return

        days = sorted(feats["day"].unique())[-sessions:]
        self.stdout.write("rebuilding {} sessions, {} to {}".format(
            len(days), days[0], days[-1]))

        rows = []
        for day in days:
            for rank, row in enumerate(
                    S.shortlist(feats, day).itertuples(index=False), start=1):
                rows.append(DailyPick(
                    day=day, symbol=row.symbol, source=PickSource.BACKFILL,
                    **_snapshot(row, rank)))

        dropped, _ = DailyPick.objects.filter(
            source=PickSource.BACKFILL, day__gte=days[0], day__lte=days[-1]).delete()
        DailyPick.objects.bulk_create(rows, batch_size=1000)
        self.stdout.write(self.style.SUCCESS(
            "{:,} backfilled picks over {} sessions ({:,} stale rows replaced)".format(
                len(rows), len(days), dropped)))
        self._score(daily)

    def _write(self, hit, day, source):
        """Persist one session's list. LIVE rows are created, never updated."""
        written = []
        for rank, row in enumerate(hit.itertuples(index=False), start=1):
            _, created = DailyPick.objects.get_or_create(
                day=day, symbol=row.symbol, source=source,
                defaults=_snapshot(row, rank))
            if created:
                written.append(row.symbol)
        return written

    def _score(self, daily):
        """Fill in what happened, for every pick whose five sessions have passed."""
        pending = DailyPick.objects.filter(scored_on__isnull=True)
        if not pending.exists():
            self.stdout.write("nothing awaiting a score")
            return

        out = S.outcomes(daily).set_index(["symbol", "day"])
        today = dt.date.today()
        ready, scored, contaminated = [], 0, 0
        for pick in pending.iterator(chunk_size=1000):
            try:
                row = out.loc[(pick.symbol, pick.day)]
            except KeyError:
                continue
            if row["sessions_ahead"] < S.HORIZON:
                continue                     # the window has not closed yet
            pick.scored_on = today
            if bool(row["contaminated"]) or pd.isna(row["fwd_high_pct"]):
                # A split is not a move. Stamp it scored so it stops being
                # retried, but leave the numbers null so nothing counts it.
                pick.sessions_scored = 0
                contaminated += 1
            else:
                pick.fwd_high_pct = _round(row["fwd_high_pct"], 4)
                pick.fwd_low_pct = _round(row["fwd_low_pct"], 4)
                pick.fwd_close_pct = _round(row["fwd_close_pct"], 4)
                pick.hit_up5 = bool(row["hit_up5"])
                pick.hit_dn5 = bool(row["hit_dn5"])
                pick.hit_up10 = bool(row["hit_up10"])
                pick.sessions_scored = S.HORIZON
                scored += 1
            ready.append(pick)

        DailyPick.objects.bulk_update(ready, [
            "fwd_high_pct", "fwd_low_pct", "fwd_close_pct", "hit_up5", "hit_dn5",
            "hit_up10", "sessions_scored", "scored_on"], batch_size=1000)
        self.stdout.write("scored {:,} picks ({} blanked as corporate actions)".format(
            scored, contaminated))


def _snapshot(row, rank):
    """The chart context as it stood at pick time, frozen for the screen."""
    return {
        "rank": rank,
        "close": _round(row.c, 2),
        "atr_pct": _round(row.atr_pct, 5),
        "dollar_vol": _round(row.dollar_vol, 2),
        "pos20": _round(row.pos20, 4),
        "mom5": _round(row.mom5, 4),
    }


def _round(value, places):
    if value is None or pd.isna(value):
        return None
    return round(float(value), places)
