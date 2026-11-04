"""Build the evening strength-dip alert, move the paper book on, and send it.

    python manage.py build_swing_alerts --once
    python manage.py build_swing_alerts --daemon
    python manage.py build_swing_alerts --rebuild-history 250
    python manage.py build_swing_alerts --chat-ids
    python manage.py build_swing_alerts --test-message

WHEN.  18:55 IST, after the shortlist's 18:40 build. The rule acts at the next
open, so an evening alert loses nothing, and the history API sheds load during
the session: a half-downloaded day ranks the universe on a partial close and
produces a list that looks ordinary and is wrong.

WHY A SESSION CAN BE REFUSED.  Strength is a rank across the universe, so a
session only counts once most of the universe has reported. And the book only
moves forward one session at a time, in order: a missed evening is caught up
session by session the next time the job runs, so a fill or a stop is never
skipped over.
"""
import datetime as dt
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from options_tracker import stock_data
from options_tracker import swing as W
from options_tracker import swing_options as WO
from options_tracker import telegram_bot
from options_tracker.models import AppSetting, PickSource, SwingSignal, SwingStatus, TrackedStock

BUILD_AT_HOUR, BUILD_AT_MINUTE = 18, 55
MIN_SYMBOLS_FOR_SESSION = 100
SENT_KEY = "swing_alert_last_sent"
BOOK_KEY = "swing_book_last_day_{}"


def next_run(now):
    target = now.replace(hour=BUILD_AT_HOUR, minute=BUILD_AT_MINUTE, second=0, microsecond=0)
    return target if target > now else target + dt.timedelta(days=1)


def _bars_by_day(bars):
    out = {}
    for row in bars.itertuples(index=False):
        out.setdefault(row.day, {})[row.symbol] = (row.o, row.h, row.l, row.c)
    return out


class Command(BaseCommand):
    help = "Record today's strength-dip signals, advance the paper book, and send the Telegram alert."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Build once and exit (the default).")
        parser.add_argument("--daemon", action="store_true", help="Build every evening at 18:55 IST.")
        parser.add_argument("--rebuild-history", type=int, default=0,
                            help="Replay the last N sessions as a BACKFILL book.")
        parser.add_argument("--no-download", action="store_true", help="Use stored bars only.")
        parser.add_argument("--no-send", action="store_true", help="Do not send the Telegram message.")
        parser.add_argument("--resend", action="store_true", help="Send even if today's alert already went.")
        parser.add_argument("--chat-ids", action="store_true", help="List chats that have messaged the bot.")
        parser.add_argument("--test-message", action="store_true", help="Send a one-line test through the bot.")
        parser.add_argument("--history-days", type=int, default=W.LOOKBACK_DAYS,
                            help="Calendar days of daily bars to download when topping up.")

    def handle(self, *args, **options):
        if options["chat_ids"]:
            chats, error = telegram_bot.chat_ids()
            if error:
                self.stderr.write(self.style.ERROR(error))
            for chat_id, name in chats:
                self.stdout.write(f"{chat_id}\t{name}")
            if not chats and not error:
                self.stdout.write("no chats yet -- send the bot /start from Telegram, then run this again")
            return
        if options["test_message"]:
            ok, error = telegram_bot.send("ARC: strength-dip alerts are connected.")
            self.stdout.write(self.style.SUCCESS("sent") if ok else self.style.ERROR(error))
            return
        if options["rebuild_history"]:
            self._rebuild(options["rebuild_history"], options["no_download"], options["history_days"])
            return
        if options["daemon"]:
            while True:
                now = timezone.localtime()
                due = next_run(now)
                self.stdout.write("next build {:%Y-%m-%d %H:%M %Z}".format(due))
                time.sleep(max((due - now).total_seconds(), 60))
                try:
                    self._build_once(options)
                except Exception as error:            # noqa: BLE001 - tomorrow still matters
                    self.stderr.write(self.style.ERROR(f"build failed: {error}"))
                time.sleep(90)
        self._build_once(options)

    # -- data ---------------------------------------------------------------

    def _download(self, days):
        stocks = list(TrackedStock.objects.filter(is_active=True).order_by("priority"))
        end = dt.date.today()
        rows, failed = stock_data.download_equity_daily(stocks, end - dt.timedelta(days=days), end)
        self.stdout.write(f"daily bars: {rows:,} rows returned ({failed} stocks failed)")
        # The history endpoint publishes a session's bar the next morning; the
        # evening build takes today's from the post-close quote instead.
        now = timezone.localtime()
        if now.weekday() < 5 and (now.hour, now.minute) >= (15, 40):
            written, error = stock_data.snapshot_today_daily(stocks)
            self.stdout.write(f"today's bar from quotes: {written} stocks" + (f" ({error})" if error else ""))

    def _frames(self):
        bars = W.daily_bars(since=dt.date.today() - dt.timedelta(days=W.LOOKBACK_DAYS + 900))
        if bars.empty:
            return bars, None
        return bars, W.features(bars)

    # -- the evening --------------------------------------------------------

    def _build_once(self, options):
        if not options["no_download"] and dt.date.today().weekday() < 5:
            # A fortnight tops up a stored history; a fresh database needs the
            # whole lookback once before the 12-month rank can exist.
            stored = W.daily_bars(since=dt.date.today() - dt.timedelta(days=W.LOOKBACK_DAYS)).day.nunique()
            self._download(14 if stored >= 260 else options["history_days"])

        bars, frame = self._frames()
        if frame is None or frame.empty:
            self.stderr.write(self.style.ERROR("no daily bars -- nothing to build"))
            return
        by_day = _bars_by_day(bars)
        complete = sorted(d for d, rows in by_day.items() if len(rows) >= MIN_SYMBOLS_FOR_SESSION)
        if not complete:
            self.stderr.write(self.style.ERROR("no session has enough of the universe reporting"))
            return
        newest = complete[-1]
        if (dt.date.today() - newest).days > 4:
            self.stderr.write(self.style.WARNING(
                f"newest complete session is {newest} -- too stale to record live signals"))
            return

        last = AppSetting.objects.filter(key=BOOK_KEY.format(PickSource.LIVE)).first()
        start = dt.date.fromisoformat(last.value) if last and last.value else newest
        pending_days = [d for d in complete if d > start] or ([newest] if not last else [])
        result = None
        for day in pending_days:
            result = W.advance(PickSource.LIVE, day, by_day[day], frame)
            AppSetting.objects.update_or_create(key=BOOK_KEY.format(PickSource.LIVE),
                                                defaults={"value": day.isoformat()})
            self.stdout.write(f"{day}: {len(result['signals'])} signal(s), {len(result['taken'])} taken, "
                              f"{len(result['filled'])} filled, {len(result['exited'])} exited")
        if result is None:
            self.stdout.write(f"{newest} already recorded")
            self._options(newest)                     # idempotent: opens/marks anything still due
            return
        option_text = self._options(newest)
        self._notify(newest, result, frame, options, option_text)

    def _options(self, day):
        """The option book can only be marked on the evening of the session itself."""
        if day != timezone.localdate():
            self.stdout.write(f"options: {day} is not today -- quotes cannot be replayed, skipping")
            return ""
        try:
            contracts = stock_data.option_contracts(stock_data.load_master())
            result = WO.advance(day, stock_data.option_quotes, contracts)
        except Exception as error:                    # noqa: BLE001 - the stock alert still goes
            self.stderr.write(self.style.ERROR(f"options: {error}"))
            return ""
        self.stdout.write(f"options: {len(result['opened'])} opened, {len(result['filled'])} filled, "
                          f"{len(result['exited'])} closed")
        return WO.format_alert(result)

    def _notify(self, day, result, frame, options, option_text=""):
        if options["no_send"]:
            return
        sent = AppSetting.objects.filter(key=SENT_KEY).first()
        if sent and sent.value == day.isoformat() and not options["resend"]:
            self.stdout.write(f"alert for {day} already sent")
            return
        if not telegram_bot.configured():
            self.stdout.write("Telegram bot not configured -- the page has today's list; no message sent")
            return
        open_rows = list(SwingSignal.objects.filter(
            source=PickSource.LIVE, status__in=[SwingStatus.OPEN, SwingStatus.PENDING]).order_by("entry_day", "symbol"))
        text = W.format_alert(day, result, open_rows, W.leaders(frame, day)) + option_text
        ok, error = telegram_bot.send(text)
        if ok:
            AppSetting.objects.update_or_create(key=SENT_KEY, defaults={"value": day.isoformat()})
            self.stdout.write(self.style.SUCCESS(f"alert for {day} sent"))
        else:
            self.stderr.write(self.style.ERROR(f"alert not sent: {error}"))

    # -- backfill -----------------------------------------------------------

    def _rebuild(self, sessions, no_download, history_days):
        """Replay the rule over past sessions as its own BACKFILL book.

        Replaced wholesale on every rebuild: these rows make no claim to have been
        known in advance, and are never counted beside the live record.
        """
        if not no_download:
            self._download(max(history_days, W.LOOKBACK_DAYS + int(sessions * 1.5)))
        bars, frame = self._frames()
        if frame is None or frame.empty:
            self.stderr.write(self.style.ERROR("no daily bars -- nothing to rebuild"))
            return
        by_day = _bars_by_day(bars)
        ranked_days = set(frame.loc[frame["ret252"].notna(), "day"])
        days = [d for d in sorted(by_day) if len(by_day[d]) >= MIN_SYMBOLS_FOR_SESSION and d in ranked_days]
        days = days[-sessions:]
        if not days:
            self.stderr.write(self.style.ERROR("not enough history to rebuild -- download more bars first"))
            return
        dropped, _ = SwingSignal.objects.filter(source=PickSource.BACKFILL).delete()
        for day in days:
            W.advance(PickSource.BACKFILL, day, by_day[day], frame)
        closed = SwingSignal.objects.filter(source=PickSource.BACKFILL, status=SwingStatus.CLOSED)
        rets = [float(r) for r in closed.values_list("net_return", flat=True)]
        mean = sum(rets) / len(rets) * 100 if rets else 0.0
        self.stdout.write(self.style.SUCCESS(
            f"backfill {days[0]} -> {days[-1]}: {len(rets)} closed trades, mean {mean:+.2f}% net, "
            f"win {sum(r > 0 for r in rets) / max(len(rets), 1) * 100:.1f}% ({dropped} old rows replaced)"))
