from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from .models import IndexOISnapshot, SignalStatus, TipSignal, TradeExecution, TradeState


def layout_shell(request):
    active_trades = TradeExecution.objects.filter(state=TradeState.OPEN).count()
    today_trades = TradeExecution.objects.filter(opened_at__date__isnull=False).count()
    active_signals = TipSignal.objects.filter(status__in=[SignalStatus.NEW, SignalStatus.CANDIDATE, SignalStatus.ACTIVE]).count()
    archived_signals = TipSignal.objects.filter(status=SignalStatus.ARCHIVED).count()
    latest_oi = IndexOISnapshot.objects.order_by("-created_at").first()
    nifty_ticker = IndexOISnapshot.objects.filter(underlying="NIFTY").order_by("-created_at").first()

    nav_items = [
        ("Options Tracker", reverse("options_tracker")),
        ("Scanners", reverse("scanners")),
        ("Telegram Feed", reverse("telegram_feed")),
        ("Recommendations", reverse("recommendations")),
        ("Index OI", reverse("index_oi")),
        ("Dhan Orders", reverse("dhan_orders")),
        ("Trade Journal", reverse("trade_journal")),
        ("Archive", reverse("archive")),
        ("Admin", reverse("admin:index")),
    ]

    return {
        "shell_nav_items": nav_items,
        "shell_active_trades": active_trades,
        "shell_today_trades": today_trades,
        "shell_active_signals": active_signals,
        "shell_archived_signals": archived_signals,
        "shell_latest_oi": latest_oi,
        "shell_nifty_ticker": nifty_ticker,
        "shell_nifty_stale": not nifty_ticker or nifty_ticker.created_at < timezone.now() - timedelta(minutes=5),
        "shell_brand": "ARC Trading Journal",
        "shell_subtitle": "Options terminal for tips, triggers, OI, and execution control",
    }
