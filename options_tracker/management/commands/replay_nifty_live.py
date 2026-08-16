"""Replay a stored session through the live engine and compare it to the backtest.

The live engine reimplements the entry filters against a different data path
(Dhan `/charts/intraday` polled minute by minute, plus the option-chain snapshot)
than the backtest, which reads `IndexOptionCandle`. That is a real risk: two
implementations of one rule set drift silently. This command removes the guess.

It feeds `live_engine.detect_signal` the stored candles for one session, minute by
minute, with the option chain synthesised from the same rows -- then prints what
the live path found next to what `backtest_strategy` found. They should match.

Nothing is written: the synthesised snapshot rows are rolled back.

    python manage.py replay_nifty_live --date 2026-08-05
"""
from collections import defaultdict
from datetime import date as date_type, datetime, timedelta
from unittest.mock import patch

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from options_tracker import live_engine
from options_tracker.models import IndexOISnapshot, IndexOptionCandle
from options_tracker.nifty_trail_strategy import nifty_trail_config
from options_tracker.strategy_backtest import backtest_strategy


class Command(BaseCommand):
    help = "Replay one stored session through the live signal path and diff it against the backtest."

    def add_arguments(self, parser):
        parser.add_argument("--date", action="append", default=[], help="Session date, YYYY-MM-DD. Repeatable.")
        parser.add_argument(
            "--all-signals",
            action="store_true",
            help="Replay every session the backtest traded. This is the parity proof.",
        )
        parser.add_argument("--underlying", default="NIFTY")
        parser.add_argument("--expiry-code", type=int, default=1)
        parser.add_argument(
            "--spread-percent",
            type=float,
            default=0.4,
            help="Synthetic bid-ask, as a percent of the mid. The stored candles have no quote.",
        )
        parser.add_argument(
            "--verbose-rejections",
            action="store_true",
            help="Print why every minute that had a spot setup did not produce a trade.",
        )

    def handle(self, *args, **options):
        underlying = options["underlying"]
        config = nifty_trail_config()

        if not options["date"] and not options["all_signals"]:
            raise CommandError("Pass --date YYYY-MM-DD (repeatable) or --all-signals.")

        self.stdout.write("Running the backtest for the reference trades...")
        trades = backtest_strategy(underlying, options["expiry_code"], config)
        # backtest_strategy serialises its timestamps; the live path keeps objects.
        for trade in trades:
            trade["signal_at"] = datetime.fromisoformat(trade["signal_at"])

        by_date = defaultdict(list)
        for trade in trades:
            by_date[str(trade["date"])].append(trade)

        if options["all_signals"]:
            session_dates = [date_type.fromisoformat(key) for key in sorted(by_date)]
        else:
            session_dates = [date_type.fromisoformat(value) for value in options["date"]]

        matched = missed = extra = 0
        gapped = []
        for session_date in session_dates:
            session_missed, session_extra, feed_gap = self._replay(
                session_date, underlying, options, config, by_date[session_date.isoformat()],
            )
            if feed_gap:
                gapped.append((session_date, feed_gap))
                continue
            matched += len(by_date[session_date.isoformat()]) - session_missed
            missed += session_missed
            extra += session_extra

        self.stdout.write("")
        summary = (
            f"{len(session_dates) - len(gapped)} comparable session(s): {matched} backtest "
            f"signal(s) reproduced, {missed} missed, {extra} extra beyond the daily cap."
        )
        self.stdout.write(
            self.style.ERROR(summary) if missed else self.style.SUCCESS(summary)
        )
        if gapped:
            # The backtest builds its opening range from whatever minutes it has;
            # the live path insists on all fifteen. These sessions are missing some
            # in the stored cache, so they cannot be compared -- the live index feed
            # is a different source and does not share the gap.
            self.stdout.write(self.style.WARNING(
                f"{len(gapped)} session(s) skipped, opening range incomplete in the "
                "stored cache: "
                + ", ".join(f"{day} ({note})" for day, note in gapped)
            ))

    def _replay(self, session_date, underlying, options, config, expected):
        rows = self._session_rows(underlying, options["expiry_code"], session_date)
        if not rows:
            raise CommandError(f"No stored {underlying} candles for {session_date}.")

        spot_rows = {
            timestamp: float(row["spot"])
            for timestamp, row in sorted(self._atm_by_minute(rows).items())
            if row["spot"] is not None
        }
        if not spot_rows:
            raise CommandError(f"Stored candles for {session_date} carry no spot column.")

        bars_by_contract = {
            self._security_id(strike, option_type): [
                {
                    "timestamp": row["local_timestamp"],
                    "open": live_engine._number(row["open"]),
                    "high": live_engine._number(row["high"]),
                    "low": live_engine._number(row["low"]),
                    "close": live_engine._number(row["close"]),
                    "volume": live_engine._number(row["volume"]),
                }
                for row in sorted(contract_rows, key=lambda item: item["local_timestamp"])
            ]
            for (strike, option_type), contract_rows in self._by_contract(rows).items()
        }

        found = []
        rejections = []
        feed_gap = ""
        with transaction.atomic():
            snapshot = IndexOISnapshot.objects.create(
                underlying=underlying, underlying_price=0, atm_strike=0,
            )
            with patch.object(
                live_engine, "intraday_bars",
                side_effect=lambda security_id, *_args, **_kwargs: bars_by_contract.get(
                    str(security_id), [],
                ),
            ):
                covered = live_engine.opening_minutes_present(spot_rows, config)
                if covered < config.opening_range_minutes:
                    feed_gap = f"{covered}/{config.opening_range_minutes} opening minutes"
                for signal_at in sorted(spot_rows):
                    window = config.entry_windows[0]
                    if not (window[0] <= signal_at.time() <= window[1]):
                        continue
                    if not self._dress_snapshot(
                        snapshot, rows, signal_at, options["spread_percent"],
                    ):
                        continue
                    candidate, reasons = live_engine.detect_signal(
                        # A few seconds into the next minute, which is when the
                        # engine actually wakes up and reads the closed bar.
                        now=signal_at + timedelta(minutes=1, seconds=5),
                        spot_rows=spot_rows,
                        snapshot=snapshot,
                    )
                    if candidate:
                        found.append(candidate)
                    elif reasons and options["verbose_rejections"]:
                        rejections.append((signal_at, reasons))
            transaction.set_rollback(True)

        self.stdout.write(f"\n{underlying} {session_date} -- {len(spot_rows)} stored minutes")
        if feed_gap:
            self.stdout.write(self.style.WARNING(f"  not comparable: {feed_gap} in the cache"))

        for trade in expected:
            self.stdout.write(
                f"  backtest  {trade['signal_at']:%H:%M}  {trade['option_type']:<4} {trade['strike']:.0f}"
                f"  close Rs {trade['signal_close']:.2f}  volume x{trade['volume_ratio']:.2f}"
                f"  -> {trade['outcome']} {trade['realized_r']:+.2f}R"
            )
        if not expected:
            self.stdout.write("  backtest  (no trade)")

        for candidate in found:
            self.stdout.write(
                f"  live      {candidate['signal_at']:%H:%M}  {candidate['option_type']:<4}"
                f" {candidate['strike']:.0f}  close Rs {candidate['signal_close']:.2f}"
                f"  volume x{candidate['volume_ratio']:.2f}"
            )
        if not found:
            self.stdout.write("  live      (no signal)")

        for signal_at, reasons in rejections:
            self.stdout.write(f"  {signal_at:%H:%M} rejected: {'; '.join(reasons)}")

        missed, extra = self._report_match(expected, found, feed_gap)
        return missed, extra, feed_gap

    # ----------------------------------------------------------------- #

    def _report_match(self, expected, found, feed_gap=""):
        side = {"CALL": "CE", "PUT": "PE"}
        want = {
            (trade["signal_at"].replace(second=0, microsecond=0),
             side[trade["option_type"]], float(trade["strike"]))
            for trade in expected
        }
        got = {
            (candidate["signal_at"], candidate["option_type"], candidate["strike"])
            for candidate in found
        }
        # The live engine can legitimately find more than the backtest books: the
        # backtest stops at max_trades_per_day and needs the *next* bar to exist
        # before it will enter. A backtest trade the live path missed is the real
        # failure, so that is what is checked.
        missed = want - got
        if missed and not feed_gap:
            self.stdout.write(self.style.ERROR(
                "  MISMATCH: missed "
                + ", ".join(f"{stamp:%H:%M} {option_type} {strike:.0f}" for stamp, option_type, strike in sorted(missed))
            ))
        return len(missed), len(got - want)

    def _security_id(self, strike, option_type):
        return f"{float(strike):.0f}-{'CE' if option_type == 'CALL' else 'PE'}"

    def _session_rows(self, underlying, expiry_code, session_date):
        start = timezone.make_aware(datetime.combine(session_date, datetime.min.time()))
        query = IndexOptionCandle.objects.filter(
            underlying=underlying, expiry_code=expiry_code, interval_minutes=1,
            timestamp__gte=start, timestamp__lt=start + timedelta(days=1),
        ).values(
            "timestamp", "strike", "relative_strike", "option_type",
            "open", "high", "low", "close", "volume", "spot",
        )
        rows = []
        for row in query.iterator(chunk_size=10000):
            row["local_timestamp"] = timezone.localtime(row["timestamp"])
            rows.append(row)
        return rows

    def _atm_by_minute(self, rows):
        """One row per minute, from whichever contract was ATM then."""
        return {
            row["local_timestamp"]: row
            for row in rows if row["relative_strike"] == "ATM"
        }

    def _by_contract(self, rows):
        contracts = defaultdict(list)
        for row in rows:
            contracts[(row["strike"], row["option_type"])].append(row)
        return contracts

    def _dress_snapshot(self, snapshot, rows, signal_at, spread_percent):
        """Point the snapshot at the strike that was ATM on this minute.

        The stored candles have no bid/ask -- that is exactly the number day one
        exists to measure -- so a symmetric synthetic spread stands in. It is set
        tight on purpose: this replay is testing signal parity, and a wide
        synthetic spread would just switch the liquidity gate on and hide it.
        """
        minute_rows = [
            row for row in rows
            if row["local_timestamp"] == signal_at and row["relative_strike"] == "ATM"
        ]
        if not minute_rows:
            return False
        strike = minute_rows[0]["strike"]
        snapshot.atm_strike = strike
        snapshot.underlying_price = minute_rows[0]["spot"] or 0
        snapshot.save(update_fields=["atm_strike", "underlying_price"])

        half = spread_percent / 200
        for row in minute_rows:
            price = live_engine._number(row["close"])
            option_type = "CE" if row["option_type"] == "CALL" else "PE"
            snapshot.strikes.update_or_create(
                strike=strike, option_type=option_type,
                defaults={
                    "security_id": self._security_id(strike, row["option_type"]),
                    "last_price": price,
                    "top_bid_price": round(price * (1 - half), 2),
                    "top_ask_price": round(price * (1 + half), 2),
                    "top_bid_quantity": 750, "top_ask_quantity": 750,
                    "delta": 0.5 if option_type == "CE" else -0.5,
                    "is_atm": True,
                },
            )
        return True
