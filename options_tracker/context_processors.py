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

    options_url = reverse("options_tracker")
    tracker_tab = request.GET.get("tab", "options")
    is_tracker_path = request.path.startswith(options_url)
    nav_items = [
        {
            "area": "Command Centre",
            "label": "Home",
            "url": reverse("home"),
            "active": request.path == reverse("home"),
        },
        {
            "area": "Opportunity Inbox",
            "label": "Opportunity Inbox",
            "url": reverse("opportunity_inbox"),
            "active": request.path.startswith(reverse("opportunity_inbox")),
        },
        {
            "area": "Command Centre",
            "label": "Action List",
            "url": reverse("action_list"),
            "active": request.path.startswith(reverse("action_list")),
        },
        {
            "area": "Command Centre",
            "label": "Options",
            "url": f"{options_url}?tab=options",
            "active": is_tracker_path and tracker_tab != "equities",
        },
        {
            "area": "Command Centre",
            "label": "Equities",
            "url": f"{options_url}?tab=equities",
            "active": is_tracker_path and tracker_tab == "equities",
        },
        {"area": "Stock Intelligence", "label": "Stock & Option Data", "url": reverse("stock_options"), "active": request.path.startswith(reverse("stock_options"))},
        {"area": "Market Scanner", "label": "Scanner Builder", "url": reverse("scanner_builder"), "active": request.path.startswith(reverse("scanner_builder"))},
        {"area": "Opportunity Inbox", "label": "Trading Workspace", "url": reverse("radar"), "active": request.path.startswith(reverse("radar"))},
        {"area": "Trading Desk", "label": "NIFTY Execution", "url": reverse("nifty_live"), "active": request.path.startswith(reverse("nifty_live"))},
        {"area": "Trading Desk", "label": "F&O Research", "url": reverse("fno_desk"), "active": request.path.startswith(reverse("fno_desk"))},
        {"area": "Trading Desk", "label": "Trade Journal", "url": reverse("trade_journal"), "active": request.path.startswith(reverse("trade_journal"))},
        {"area": "Investment Desk", "label": "Long-term Portfolio", "url": reverse("investment_desk"), "active": request.path.startswith(reverse("investment_desk"))},
        {"area": "Strategy Lab", "label": "Strategy Builder", "url": reverse("strategy_lab"), "active": request.path.startswith(reverse("strategy_lab"))},
        {"area": "Strategy Lab", "label": "Research & Archive", "url": reverse("research_report"), "active": request.path.startswith(reverse("research_report"))},
        {"area": "Performance & AI", "label": "Performance", "url": reverse("performance_dashboard"), "active": request.path.startswith(reverse("performance_dashboard"))},
        {"area": "Performance & AI", "label": "Research Assistant", "url": reverse("research_assistant"), "active": request.path.startswith(reverse("research_assistant"))},
        {"area": "Opportunity Inbox", "label": "Signal Archive", "url": reverse("archive"), "active": request.path.startswith(reverse("archive"))},
        {"area": "System & Admin", "label": "Operations Health", "url": reverse("operations_health"), "active": request.path.startswith(reverse("operations_health"))},
        {"area": "System & Admin", "label": "Admin", "url": reverse("admin:index"), "active": request.path.startswith(reverse("admin:index"))},
    ]
    area_order = [
        "Command Centre",
        "Opportunity Inbox",
        "Stock Intelligence",
        "Market Scanner",
        "Trading Desk",
        "Investment Desk",
        "Strategy Lab",
        "Performance & AI",
        "System & Admin",
    ]
    nav_groups = [
        {
            "label": area,
            "items": [item for item in nav_items if item["area"] == area],
        }
        for area in area_order
    ]

    return {
        "shell_nav_items": nav_items,
        "shell_nav_groups": nav_groups,
        "shell_active_trades": active_trades,
        "shell_today_trades": today_trades,
        "shell_active_signals": active_signals,
        "shell_archived_signals": archived_signals,
        "shell_latest_oi": latest_oi,
        "shell_nifty_ticker": nifty_ticker,
        "shell_nifty_stale": not nifty_ticker or nifty_ticker.created_at < timezone.now() - timedelta(minutes=5),
        "shell_brand": "ARC Trading Journal",
        "shell_subtitle": "One evidence-aware queue for action, review, charts, and risk",
    }
