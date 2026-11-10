import csv
import re
import json
import os
import secrets
import logging
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core import signing
from django.core.cache import cache
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db.models.deletion import ProtectedError
from django.db.models import Count, Max, Min, Q
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt

from .access import staff_required_for_unsafe_methods
from .api_contract import versioned_json
from .deployment import sync_status
from .execution_policy import execution_policy
from .forms import DhanCredentialsForm, SignalFilterForm, TelegramBulkForm, TipSignalForm, TrackedOptionEditForm, TradeExecutionForm, TriggerPromoteForm
from .jump_detector import historical_jump_report, jump_detector_state, live_jump_candidates
from .live_config import (
    live_settings,
    panel_rows,
    reset_live_settings,
    risk_surface,
    save_live_settings,
    warnings_for,
)
from .live_dashboard import (
    by_month,
    curve_svg,
    engine_snapshot,
    performance,
    recent_events,
    risk_panel,
    sizing_preview,
    today_summary,
    trade_rows,
)
from .rate_limit import rate_limit_exceeded
from .models import (
    AppSetting,
    ChartinkTrigger,
    ChatMessage,
    DhanOrderEvent,
    Direction,
    IndexOISnapshot,
    IndexOptionCandle,
    OptionOutcome,
    SignalStatus,
    TipSignal,
    TradeEmotion,
    TradeExecution,
    TradeExecutionMode,
    TradeState,
    TradeStyle,
)
from .services import (
    archive_expired_signals,
    archive_old_signals,
    classify_regime,
    get_oi_interval_seconds,
    get_dhan_credentials,
    parse_tip_text,
    place_super_order,
    refresh_dhan_option_prices,
    risk_guard,
    score_signal,
    set_oi_interval_seconds,
    sync_chartink_from_legacy,
    sync_index_oi_from_legacy,
    sync_telegram_from_legacy,
    validate_dhan_credentials, fetch_dhan_nifty_ltp, _token_expiry, _token_is_spent,
)
from .strategy_backtest import NIFTY_PUT_RESEARCH_SUMMARY
from .action_workspace import action_list_context
from .command_centre import command_centre_context


logger = logging.getLogger(__name__)
NIFTY_LTP_CACHE_KEY = "market_ticker_nifty_ltp"


def home(request):
    return render(
        request,
        "options_tracker/command_centre.html",
        {
            "title": "Command Centre",
            "command": command_centre_context(),
        },
    )


def action_list(request):
    return render(
        request,
        "options_tracker/action_list.html",
        {
            "title": "Action List",
            **action_list_context(request.GET),
        },
    )


def _session_bounds(session_date):
    start = timezone.make_aware(datetime.combine(session_date, time.min))
    return start, start + timedelta(days=1)


def _latest_session_dates(queryset, field_name, limit=30):
    dates = []
    timestamp = queryset.order_by(f"-{field_name}").values_list(field_name, flat=True).first()
    while timestamp and len(dates) < limit:
        session_date = timezone.localtime(timestamp).date()
        dates.append(session_date)
        session_start, _ = _session_bounds(session_date)
        timestamp = queryset.filter(
            **{f"{field_name}__lt": session_start}
        ).order_by(f"-{field_name}").values_list(field_name, flat=True).first()
    return dates


def market_ticker_api(request):
    snapshot = IndexOISnapshot.objects.filter(underlying="NIFTY").order_by("-created_at").first()
    live_quote = cache.get(NIFTY_LTP_CACHE_KEY)
    if live_quote is None:
        try:
            live_quote = {
                "price": fetch_dhan_nifty_ltp(timeout=3),
                "updated_at": timezone.localtime().isoformat(),
            }
        except Exception as exc:
            logger.warning("Dhan NIFTY ticker request failed; using stored snapshot: %s", exc)
        else:
            cache.set(NIFTY_LTP_CACHE_KEY, live_quote, timeout=25)

    if live_quote:
        price = live_quote["price"]
        updated_at = live_quote["updated_at"]
        source = "dhan_ltp"
        stale = False
    elif snapshot and snapshot.underlying_price is not None:
        price = float(snapshot.underlying_price)
        updated_at = timezone.localtime(snapshot.created_at).isoformat()
        source = "stored_index_oi"
        stale = (timezone.now() - snapshot.created_at).total_seconds() > 300
    else:
        return versioned_json(
            {"ok": False, "error": "No live or stored NIFTY quote is available."},
            status=503,
        )

    change = float(snapshot.underlying_change or 0) if snapshot else 0
    previous = price - change
    return versioned_json({
        "ok": True,
        "symbol": "NIFTY50",
        "price": price,
        "change": change,
        "change_percent": round(change / previous * 100, 2) if previous else 0,
        "updated_at": updated_at,
        "stale": stale,
        "source": source,
        "change_source": "stored_index_oi" if snapshot else "unavailable",
    })


@staff_member_required(login_url="admin:login")
@require_http_methods(["GET", "POST"])
def dhan_settings(request):
    token_setting = AppSetting.objects.filter(key="dhan_access_token").first()
    access_token, configured_client_id = get_dhan_credentials()
    form = DhanCredentialsForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        client_id = form.cleaned_data["client_id"] or configured_client_id
        if not client_id:
            form.add_error("client_id", "A Dhan client ID is required.")
        else:
            try:
                validate_dhan_credentials(form.cleaned_data["access_token"], client_id)
            except Exception as exc:
                form.add_error("access_token", f"Dhan validation failed: {exc}")
            else:
                AppSetting.objects.update_or_create(
                    key="dhan_access_token", defaults={"value": form.cleaned_data["access_token"]}
                )
                if form.cleaned_data["client_id"]:
                    AppSetting.objects.update_or_create(key="dhan_client_id", defaults={"value": client_id})
                messages.success(request, "Dhan access token validated and updated. Live feeds now use it.")
                return redirect("dhan_settings")
    token_expiry = _token_expiry(access_token) if access_token else None
    token_live = bool(access_token and configured_client_id and not _token_is_spent(access_token))
    return render(request, "options_tracker/dhan_settings.html", {
        "title": "Dhan API Setup",
        "form": form,
        "dhan_configured": token_live,
        "dhan_token_source": "Dashboard override" if token_setting else "Azure App Setting",
        "dhan_token_updated_at": token_setting.updated_at if token_setting else None,
        "dhan_token_expires_at": token_expiry,
        "dhan_token_expired": bool(access_token and _token_is_spent(access_token)),
    })


def _ingest_single_telegram_message(
    source_name,
    raw_text,
    trade_style,
    *,
    source_category="",
    telegram_chat_id=None,
    telegram_message_id=None,
    telegram_message_at=None,
    raw_payload="",
):
    source = str(source_name or "Telegram").strip() or "Telegram"
    text = str(raw_text or "").strip()
    if not text and (telegram_chat_id is None or telegram_message_id is None):
        return {"status": "empty"}

    normalized = " ".join(text.split())
    if telegram_chat_id is not None and telegram_message_id is not None:
        if ChatMessage.objects.filter(
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
        ).exists():
            return {"status": "duplicate"}
    elif ChatMessage.objects.filter(source_name=source, normalized_text=normalized).exists():
        return {"status": "duplicate"}

    parsed = parse_tip_text(text) if source_category == "TIPS" else {}
    is_tip = bool(parsed.get("symbol") and parsed.get("direction") and parsed.get("sl") and parsed.get("t1"))
    linked_signal = None
    tip_created = False

    if is_tip:
        linked_signal = TipSignal.objects.filter(source_type="TELEGRAM", source_name=source, raw_text=text).first()
        if not linked_signal:
            style_value = trade_style if trade_style in {TradeStyle.INTRADAY, TradeStyle.SWING} else TradeStyle.INTRADAY
            linked_signal = TipSignal(
                source_type="TELEGRAM",
                source_name=source,
                raw_text=text,
                option_symbol=parsed["symbol"],
                direction=parsed["direction"],
                trade_style=style_value,
                entry_price=parsed["entry"],
                stop_loss=parsed["sl"],
                target_1=parsed["t1"],
                target_2=parsed["t2"],
                target_3=parsed["t3"],
                status=SignalStatus.CANDIDATE,
            )
            linked_signal.score, linked_signal.recommendation, linked_signal.reason_tags = score_signal(linked_signal)
            linked_signal.save()
            tip_created = True

    row = ChatMessage.objects.create(
        source_name=source,
        source_category=source_category,
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
        telegram_message_at=telegram_message_at,
        raw_text=text,
        raw_payload=raw_payload,
        normalized_text=normalized,
        is_tip_candidate=is_tip,
        linked_signal=linked_signal,
    )

    return {
        "status": "saved",
        "chat_message_id": row.id,
        "tip_created": tip_created,
        "linked_signal_id": linked_signal.id if linked_signal else None,
    }


@csrf_exempt
@require_http_methods(["POST"])
def telegram_ingest_api(request):
    if rate_limit_exceeded(
        scope="telegram-ingest",
        identity=request.META.get("REMOTE_ADDR", "unknown"),
        limit=int(os.environ.get("TELEGRAM_INGEST_RATE_LIMIT", "120")),
        window_seconds=60,
    ):
        response = JsonResponse(
            {"ok": False, "error": "Rate limit exceeded"},
            status=429,
        )
        response["Retry-After"] = "60"
        return response
    expected_token = str(AppSetting.objects.filter(key="telegram_ingest_token").values_list("value", flat=True).first() or "").strip()
    if not expected_token:
        expected_token = str(os.environ.get("TELEGRAM_INGEST_TOKEN", "") or "").strip()
    if not expected_token:
        logger.error("Telegram ingestion rejected because no ingest token is configured.")
        return JsonResponse(
            {"ok": False, "error": "Telegram ingestion is not configured."},
            status=503,
        )
    incoming_token = str(
        request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        or request.headers.get("X-Telegram-Ingest-Token")
        or ""
    ).strip()
    if not secrets.compare_digest(incoming_token, expected_token):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=401)

    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON body"}, status=400)

    telegram_message = next(
        (
            payload.get(key)
            for key in ("message", "channel_post", "edited_message", "edited_channel_post")
            if isinstance(payload.get(key), dict)
        ),
        None,
    )
    if telegram_message:
        chat = telegram_message.get("chat") if isinstance(telegram_message.get("chat"), dict) else {}
        source_name = chat.get("title") or chat.get("username") or chat.get("first_name") or "Telegram"
        raw_text = telegram_message.get("text") or telegram_message.get("caption") or ""
        result = _ingest_single_telegram_message(source_name, raw_text, TradeStyle.INTRADAY)
        return JsonResponse({"ok": True, **result})

    source_name = payload.get("source_name") or payload.get("source") or "Telegram"
    trade_style = str(payload.get("trade_style") or TradeStyle.INTRADAY).strip().upper()
    raw_text = payload.get("raw_text")
    messages_bulk = payload.get("messages")

    if messages_bulk and isinstance(messages_bulk, list):
        total_saved = 0
        total_duplicates = 0
        total_tips = 0
        for msg in messages_bulk:
            if isinstance(msg, dict):
                msg_source = msg.get("source_name") or source_name
                msg_text = msg.get("raw_text") or msg.get("text") or ""
            else:
                msg_source = source_name
                msg_text = str(msg or "")

            result = _ingest_single_telegram_message(msg_source, msg_text, trade_style)
            if result["status"] == "saved":
                total_saved += 1
                total_tips += 1 if result.get("tip_created") else 0
            elif result["status"] == "duplicate":
                total_duplicates += 1

        return JsonResponse(
            {
                "ok": True,
                "saved": total_saved,
                "duplicates": total_duplicates,
                "tips_created": total_tips,
            }
        )

    if raw_text is None:
        return JsonResponse({"ok": False, "error": "Provide raw_text or messages[]"}, status=400)

    result = _ingest_single_telegram_message(source_name, raw_text, trade_style)
    return JsonResponse({"ok": True, **result})


@require_http_methods(["GET"])
def telegram_tracker_status_api(request):
    expected_token = os.environ.get("TELEGRAM_DIAGNOSTICS_TOKEN", "")
    incoming_token = request.headers.get("X-Telegram-Diagnostics-Token", "")
    if not expected_token or not secrets.compare_digest(incoming_token, expected_token):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=401)

    catchup_days = int(os.environ.get("TELEGRAM_CATCHUP_DAYS", "7"))
    cutoff_date = timezone.localdate() - timedelta(days=catchup_days - 1)
    cutoff = timezone.make_aware(datetime.combine(cutoff_date, time.min))
    rows = ChatMessage.objects.filter(telegram_message_at__gte=cutoff)
    status_value = AppSetting.objects.filter(key="telegram_tracker_status").values_list("value", flat=True).first()
    try:
        tracker_status = json.loads(status_value) if status_value else None
    except json.JSONDecodeError:
        tracker_status = {"state": "INVALID_STATUS"}

    return JsonResponse(
        {
            "ok": True,
            "cutoff": cutoff.isoformat(),
            "tracker": tracker_status,
            "total": rows.count(),
            "by_category": list(
                rows.values("source_category").annotate(count=Count("id")).order_by("source_category")
            ),
            "by_source": list(
                rows.values("source_name", "source_category")
                .annotate(count=Count("id"), first=Min("telegram_message_at"), last=Max("telegram_message_at"))
                .order_by("source_category", "source_name")
            ),
        }
    )


@staff_required_for_unsafe_methods
@require_http_methods(["GET", "POST"])
def options_tracker(request):
    if request.method == "POST" and request.POST.get("action") == "sync_legacy":
        created, status_msg = sync_telegram_from_legacy(limit=300, trade_style=TradeStyle.INTRADAY)
        if status_msg == "OK":
            messages.success(request, f"Synced {created} option tips from legacy Telegram feed.")
        else:
            messages.warning(request, f"Sync partial: {created}. {status_msg}")
        return redirect("options_tracker")

    if request.method == "POST":
        form = TipSignalForm(request.POST)
        if form.is_valid():
            signal = form.save(commit=False)
            signal.source_type = "MANUAL"
            signal.status = SignalStatus.CANDIDATE
            signal.score, signal.recommendation, signal.reason_tags = score_signal(signal)
            signal.save()
            messages.success(request, "Manual option tip added to tracker.")
            return redirect("options_tracker")
    else:
        form = TipSignalForm()

    panel = request.GET.get("panel", "").strip().lower()
    tracker_tab = request.GET.get("tab", "options").strip().lower()
    if tracker_tab not in {"options", "equities"}:
        tracker_tab = "options"
    f = SignalFilterForm(request.GET)
    today = timezone.localdate()
    tracked_tips = TipSignal.objects.exclude(status=SignalStatus.ARCHIVED).filter(
        Q(direction=Direction.EQ)
        | Q(expiry_date__isnull=True)
        | Q(expiry_date__gte=today)
    )
    options = tracked_tips.filter(direction__in=[Direction.CE, Direction.PE])
    equities = tracked_tips.filter(direction=Direction.EQ)
    tab_tips = equities if tracker_tab == "equities" else options
    items = tab_tips.order_by("-tip_time", "-id")
    if f.is_valid():
        status = f.cleaned_data.get("status")
        source = f.cleaned_data.get("source")
        style = f.cleaned_data.get("style")
        q = f.cleaned_data.get("q")
        score_min = request.GET.get("score_min", "").strip()
        score_max = request.GET.get("score_max", "").strip()
        if status:
            items = items.filter(status=status)
        if source:
            items = items.filter(source_name=source)
        if style:
            items = items.filter(trade_style=style)
        if q:
            items = items.filter(Q(option_symbol__icontains=q) | Q(raw_text__icontains=q))
        if score_min.isdigit():
            items = items.filter(score__gte=int(score_min))
        if score_max.isdigit():
            items = items.filter(score__lte=int(score_max))

    outcome = request.GET.get("outcome", "").strip()
    if outcome in OptionOutcome.values:
        items = items.filter(outcome_status=outcome)

    page = Paginator(items, 100).get_page(request.GET.get("page"))
    pagination_query = request.GET.copy()
    pagination_query.pop("page", None)
    ctx = {
        "title": "Options Tracker",
        "items": page.object_list,
        "page_obj": page,
        "pagination_query": pagination_query.urlencode(),
        "form": form,
        "filter_form": f,
        "score_min": request.GET.get("score_min", ""),
        "score_max": request.GET.get("score_max", ""),
        "open_trade_panel": panel in {"trade", "new"},
        "sources": tab_tips.order_by("source_name").values_list("source_name", flat=True).distinct(),
        "selected_source": request.GET.get("source", ""),
        "selected_outcome": outcome,
        "tracker_tab": tracker_tab,
        "outcomes": OptionOutcome.choices,
        "options_count": options.count(),
        "equities_count": equities.count(),
        "tracked_count": tab_tips.count(),
        "target_count": tab_tips.filter(outcome_status=OptionOutcome.TARGET_1).count(),
        "stop_loss_count": tab_tips.filter(outcome_status=OptionOutcome.STOP_LOSS).count(),
    }
    return render(request, "options_tracker/options_tracker.html", ctx)


@staff_required_for_unsafe_methods
@require_http_methods(["POST"])
def option_live_prices(request):
    archive_expired_signals()
    tracked_tips = TipSignal.objects.exclude(status=SignalStatus.ARCHIVED)
    options = tracked_tips.filter(direction__in=["CE", "PE"])
    tracker_tab = request.GET.get("tab", "options").strip().lower()
    selected_tips = tracked_tips.filter(direction=Direction.EQ) if tracker_tab == "equities" else options
    refresh_result = refresh_dhan_option_prices(selected_tips)
    rows = list(selected_tips.values(
        "id", "live_price", "entry_price", "outcome_status", "quote_updated_at", "security_id", "exchange_segment",
        "dhan_display_name", "expiry_date",
    ))
    return versioned_json({
        "ok": not bool(refresh_result["error"]),
        "error": refresh_result["error"],
        "market_closed": bool(refresh_result.get("market_closed")),
        "rate_limited": bool(refresh_result.get("rate_limited")),
        "refreshing": bool(refresh_result.get("refreshing")),
        "retry_after_seconds": refresh_result.get("retry_after_seconds"),
        "rows": rows,
        "counts": {
            "tracked": selected_tips.count(),
            "target": selected_tips.filter(outcome_status=OptionOutcome.TARGET_1).count(),
            "stop_loss": selected_tips.filter(outcome_status=OptionOutcome.STOP_LOSS).count(),
        },
    })


@staff_required_for_unsafe_methods
@require_http_methods(["GET", "POST"])
def option_edit(request, signal_id):
    signal = get_object_or_404(TipSignal, id=signal_id)
    tracker_tab = "equities" if signal.direction == Direction.EQ else "options"
    return_url = f"{reverse('options_tracker')}?tab={tracker_tab}"
    if request.method == "GET":
        return render(request, "options_tracker/option_edit.html", {
            "title": f"Edit {signal.option_symbol}",
            "signal": signal,
            "form": TrackedOptionEditForm(instance=signal),
            "return_url": return_url,
        })

    form = TrackedOptionEditForm(request.POST, instance=signal)
    if form.is_valid():
        signal = form.save(commit=False)
        signal.score, signal.recommendation, signal.reason_tags = score_signal(signal)
        signal.security_id = ""
        signal.exchange_segment = ""
        signal.dhan_display_name = ""
        signal.live_price = None
        signal.quote_updated_at = None
        signal.save()
        messages.success(request, f"Updated {signal.option_symbol}.")
    else:
        messages.error(request, "Could not update option: " + "; ".join(
            error for errors in form.errors.values() for error in errors
        ))
    return redirect(return_url)


@staff_required_for_unsafe_methods
@require_http_methods(["POST"])
def option_delete(request, signal_id):
    signal = get_object_or_404(TipSignal, id=signal_id)
    symbol = signal.option_symbol
    try:
        signal.delete()
        messages.success(request, f"Deleted {symbol} from tracked options.")
    except ProtectedError:
        messages.error(request, f"Cannot delete {symbol} because it has trade executions.")
    return redirect("options_tracker")


@staff_required_for_unsafe_methods
@require_http_methods(["GET", "POST"])
def scanners(request):
    if request.method == "POST" and request.POST.get("action") == "sync_legacy":
        created, status_msg = sync_chartink_from_legacy(limit=300)
        if status_msg == "OK":
            messages.success(request, f"Synced {created} Chartink triggers from legacy DB.")
        else:
            messages.warning(request, f"Sync partial: {created}. {status_msg}")
        return redirect("scanners")

    promote_form = TriggerPromoteForm(
        request.POST if request.method == "POST" and request.POST.get("action") == "promote" else None
    )
    if request.method == "POST" and promote_form.is_valid():
        trigger = get_object_or_404(ChartinkTrigger, id=promote_form.cleaned_data["trigger_id"])
        if trigger.promoted_signal_id:
            messages.warning(request, "Trigger already promoted.")
            return redirect("scanners")

        signal = TipSignal(
            source_type="CHARTINK",
            source_name=f"Chartink ({trigger.scanner_name})",
            raw_text=trigger.notes,
            option_symbol=trigger.symbol,
            direction=promote_form.cleaned_data["direction"],
            trade_style=promote_form.cleaned_data["trade_style"],
            entry_price=trigger.trigger_price,
            stop_loss=trigger.trigger_price if trigger.trigger_price else Decimal("0.01"),
            target_1=trigger.trigger_price,
            status=SignalStatus.CANDIDATE,
        )
        signal.score, signal.recommendation, signal.reason_tags = score_signal(signal)
        signal.save()
        trigger.promoted_signal = signal
        trigger.status = "MOVED"
        trigger.save(update_fields=["promoted_signal", "status"])
        messages.success(request, "Chartink trigger promoted to options tracker.")
        return redirect("scanners")

    scanner_filter = request.GET.get("scanner", "").strip()
    status_filter = request.GET.get("status", "").strip()
    q = request.GET.get("q", "").strip()
    triggers = ChartinkTrigger.objects.all()
    if scanner_filter:
        triggers = triggers.filter(scanner_name__icontains=scanner_filter)
    if status_filter:
        triggers = triggers.filter(status=status_filter)
    if q:
        triggers = triggers.filter(Q(symbol__icontains=q) | Q(notes__icontains=q))
    triggers = triggers[:300]

    return render(
        request,
        "options_tracker/scanners.html",
        {
            "title": "Scanners",
            "triggers": triggers,
            "promote_form": promote_form,
            "scanner_filter": scanner_filter,
            "status_filter": status_filter,
            "q": q,
        },
    )


@staff_required_for_unsafe_methods
@require_http_methods(["GET", "POST"])
def telegram_feed(request):
    if request.method == "POST" and request.POST.get("action") == "sync_legacy":
        style = request.POST.get("style", "INTRADAY")
        created, status_msg = sync_telegram_from_legacy(limit=300, trade_style=style)
        if status_msg == "OK":
            messages.success(request, f"Synced {created} telegram tips from legacy DB.")
        else:
            messages.warning(request, f"Sync partial: {created}. {status_msg}")
        return redirect("telegram_feed")

    form = TelegramBulkForm(
        request.POST if request.method == "POST" and request.POST.get("action") == "import" else None
    )
    if request.method == "POST" and form.is_valid():
        source = form.cleaned_data["source_name"]
        style = form.cleaned_data["trade_style"]
        raw_bulk = str(form.cleaned_data["raw_bulk_text"] or "")
        blocks = [x.strip() for x in re.split(r"\n\s*\n", raw_bulk) if x.strip()]
        if not blocks:
            blocks = [x.strip() for x in raw_bulk.splitlines() if x.strip()]

        created_tips = 0
        tracked_messages = 0
        skipped_messages = 0
        for block in blocks:
            result = _ingest_single_telegram_message(
                source,
                block,
                style,
                source_category="TIPS",
            )
            if result["status"] == "duplicate":
                skipped_messages += 1
                continue
            if result["status"] == "saved":
                tracked_messages += 1
                created_tips += int(result["tip_created"])

        messages.success(
            request,
            f"Tracked {tracked_messages} chat messages and added {created_tips} tips. Skipped duplicates: {skipped_messages}.",
        )
        return redirect("telegram_feed")

    source = request.GET.get("source", "").strip()
    style = request.GET.get("style", "").strip()
    q = request.GET.get("q", "").strip()
    discussion_q = request.GET.get("discussion_q", "").strip()
    feed = TipSignal.objects.filter(source_type="TELEGRAM")
    if source:
        feed = feed.filter(source_name__icontains=source)
    if style:
        feed = feed.filter(trade_style=style)
    if q:
        feed = feed.filter(Q(option_symbol__icontains=q) | Q(raw_text__icontains=q))
    feed = feed[:300]

    chat_rows = ChatMessage.objects.all()
    if source:
        chat_rows = chat_rows.filter(source_name__icontains=source)
    if discussion_q:
        chat_rows = chat_rows.filter(raw_text__icontains=discussion_q)
    chat_rows = chat_rows[:300]

    return render(
        request,
        "options_tracker/telegram_feed.html",
        {
            "title": "Telegram Feed",
            "form": form,
            "feed": feed,
            "chat_rows": chat_rows,
            "source": source,
            "style": style,
            "q": q,
            "discussion_q": discussion_q,
        },
    )


def recommendations(request):
    direction = request.GET.get("direction", "").strip()
    style = request.GET.get("style", "").strip()
    status = request.GET.get("status", "").strip()
    min_score = request.GET.get("min_score", "").strip()
    max_score = request.GET.get("max_score", "").strip()

    items = TipSignal.objects.filter(status__in=[SignalStatus.CANDIDATE, SignalStatus.NEW, SignalStatus.ACTIVE])
    if direction:
        items = items.filter(direction=direction)
    if style:
        items = items.filter(trade_style=style)
    if status:
        items = items.filter(status=status)
    if min_score.isdigit():
        items = items.filter(score__gte=int(min_score))
    if max_score.isdigit():
        items = items.filter(score__lte=int(max_score))
    items = items.order_by("-score", "-tip_time")[:200]

    return render(
        request,
        "options_tracker/recommendations.html",
        {
            "title": "Recommendations",
            "items": items,
            "direction": direction,
            "style": style,
            "status": status,
            "min_score": min_score,
            "max_score": max_score,
        },
    )


@staff_required_for_unsafe_methods
@require_http_methods(["GET", "POST"])
def index_oi(request):
    if request.method == "POST":
        interval = int(request.POST.get("interval_seconds", "30"))
        set_oi_interval_seconds(interval)
        messages.success(request, f"OI polling interval set to {interval} seconds.")
        return redirect("index_oi")

    selected_interval = get_oi_interval_seconds()
    underlying = request.GET.get("underlying", "SENSEX").upper()
    if underlying not in {"NIFTY", "SENSEX"}:
        underlying = "SENSEX"
    snapshot_dates = _latest_session_dates(
        IndexOISnapshot.objects.filter(underlying=underlying),
        "created_at",
    )
    candle_dates = _latest_session_dates(
        IndexOptionCandle.objects.filter(underlying=underlying),
        "timestamp",
    )
    available_dates = sorted(set(snapshot_dates + candle_dates), reverse=True)[:30]
    try:
        selected_date = datetime.strptime(request.GET.get("date", ""), "%Y-%m-%d").date()
    except ValueError:
        selected_date = available_dates[0] if available_dates else timezone.localdate()
    session_start, session_end = _session_bounds(selected_date)
    selected_snapshots = IndexOISnapshot.objects.filter(
        underlying=underlying,
        created_at__gte=session_start,
        created_at__lt=session_end,
    )
    latest = selected_snapshots.prefetch_related("strikes").first()
    selected_candles = IndexOptionCandle.objects.filter(
        underlying=underlying,
        timestamp__gte=session_start,
        timestamp__lt=session_end,
    )
    closing_candle = selected_candles.filter(
        spot__isnull=False,
    ).order_by("-timestamp").first()
    has_historical_candles = closing_candle is not None
    session_spot = closing_candle.spot if closing_candle else (latest.underlying_price if latest else None)
    candle_summary = selected_candles.aggregate(
        candles=Count("id"), contracts=Count("strike", distinct=True),
        spot_low=Min("spot"), spot_high=Max("spot"),
    )
    history_rows = list(
        selected_snapshots.order_by("-created_at")[:240]
    )
    history_rows.reverse()
    opening_call_oi = history_rows[0].call_oi if history_rows else 0
    opening_put_oi = history_rows[0].put_oi if history_rows else 0
    opening_pcr = opening_put_oi / opening_call_oi if opening_call_oi else 0
    history_data = []
    for row in history_rows:
        history_data.append({
            "time": timezone.localtime(row.created_at).strftime("%H:%M:%S"),
            "price": float(row.underlying_price or 0),
            "price_change": float(row.underlying_change or 0),
            "call_oi": row.call_oi,
            "put_oi": row.put_oi,
            "call_oi_change": row.call_oi - opening_call_oi,
            "put_oi_change": row.put_oi - opening_put_oi,
            "pcr": row.pcr,
            "pcr_change": row.pcr - opening_pcr if opening_pcr else 0,
        })
    strike_profile = []
    strike_chart_data = []
    depth_rows = []
    movement_rows = []
    latest_changes = history_data[-1] if history_data else {
        "call_oi_change": 0, "put_oi_change": 0, "pcr_change": 0,
    }
    if latest and latest.atm_strike is not None:
        all_strikes = list(latest.strikes.all())
        nearest_strikes = sorted(
            {row.strike for row in all_strikes},
            key=lambda strike: abs(strike - latest.atm_strike),
        )[:11]
        by_contract = {(row.strike, row.option_type): row for row in all_strikes}
        dashboard_strikes = [row for row in all_strikes if row.strike in nearest_strikes]
        latest_opening_call_oi = sum(row.previous_oi for row in dashboard_strikes if row.option_type == "CE")
        latest_opening_put_oi = sum(row.previous_oi for row in dashboard_strikes if row.option_type == "PE")
        latest_opening_pcr = latest_opening_put_oi / latest_opening_call_oi if latest_opening_call_oi else 0
        latest_changes = {
            "call_oi_change": sum(row.oi - row.previous_oi for row in dashboard_strikes if row.option_type == "CE"),
            "put_oi_change": sum(row.oi - row.previous_oi for row in dashboard_strikes if row.option_type == "PE"),
            "pcr_change": latest.pcr - latest_opening_pcr if latest_opening_pcr else 0,
        }
        max_oi = max((row.oi for row in all_strikes if row.strike in nearest_strikes), default=0) or 1
        for strike in sorted(nearest_strikes):
            call = by_contract.get((strike, "CE"))
            put = by_contract.get((strike, "PE"))
            strike_profile.append({
                "strike": strike,
                "call": call,
                "put": put,
                "call_width": round((call.oi / max_oi) * 100, 1) if call else 0,
                "put_width": round((put.oi / max_oi) * 100, 1) if put else 0,
            })
            strike_chart_data.append({
                "strike": float(strike),
                "call_oi": call.oi if call else 0,
                "put_oi": put.oi if put else 0,
                "call_change": call.oi - call.previous_oi if call else 0,
                "put_change": put.oi - put.previous_oi if put else 0,
                "atm": strike == latest.atm_strike,
            })
        for row in (strike for strike in all_strikes if strike.is_atm):
            buy_depth = (row.depth or {}).get("buy", [])
            sell_depth = (row.depth or {}).get("sell", [])
            for level in range(max(len(buy_depth), len(sell_depth), 5)):
                bid = buy_depth[level] if level < len(buy_depth) else {}
                ask = sell_depth[level] if level < len(sell_depth) else {}
                depth_rows.append({"contract": row.option_type, "level": level + 1, "bid": bid, "ask": ask})
        movement_rows = sorted(all_strikes, key=lambda row: abs(row.price_change), reverse=True)[:8]

    status_value = AppSetting.objects.filter(key="index_oi_collector_status").values_list("value", flat=True).first()
    try:
        collector_status = json.loads(status_value) if status_value else {"state": "STARTING"}
    except json.JSONDecodeError:
        collector_status = {"state": "UNKNOWN"}
    jump_report = historical_jump_report(underlying, session_date=selected_date)
    jump_candidates = live_jump_candidates(underlying) if selected_date == timezone.localdate() else []
    detector_state = jump_detector_state(underlying)
    suggested_option = next(
        (candidate for candidate in jump_candidates if candidate["score"] >= 55 and candidate.get("trade_ready")),
        None,
    )
    return render(
        request,
        "options_tracker/index_oi.html",
        {
            "title": "Index OI Intelligence",
            "underlying": underlying,
            "available_dates": available_dates,
            "selected_date": selected_date,
            "is_historical": selected_date < timezone.localdate(),
            "latest": latest,
            "has_historical_candles": has_historical_candles,
            "session_spot": session_spot,
            "candle_summary": candle_summary,
            "history_rows": history_rows,
            "history_data": history_data,
            "strike_chart_data": strike_chart_data,
            "strike_profile": strike_profile,
            "depth_rows": depth_rows,
            "movement_rows": movement_rows,
            "selected_interval": selected_interval,
            "collector_status": collector_status,
            "jump_report": jump_report,
            "jump_candidates": jump_candidates,
            "suggested_option": suggested_option,
            "detector_state": detector_state,
            "latest_changes": latest_changes,
            "nifty_strategy_summary": NIFTY_PUT_RESEARCH_SUMMARY if underlying == "NIFTY" else None,
        },
    )


def _manual_order_terms(signal):
    return {
        "security_id": str(signal.security_id or ""),
        "direction": signal.direction,
        "trade_style": signal.trade_style,
        "entry_price": str(signal.entry_price or ""),
        "stop_loss": str(signal.stop_loss or ""),
        "target_1": str(signal.target_1 or ""),
    }


@staff_member_required
@require_http_methods(["GET", "POST"])
def dhan_orders(request):
    order_preview = None
    selected_signal = None
    if request.method == "POST":
        action = request.POST.get("action", "preview")
        if action == "confirm":
            try:
                confirmed = signing.loads(
                    request.POST.get("order_token", ""),
                    salt="manual-dhan-order",
                    max_age=600,
                )
            except signing.BadSignature:
                messages.error(request, "Order review expired or was changed. Review the order again.")
                return redirect("dhan_orders")
            selected_signal = get_object_or_404(TipSignal, id=confirmed["signal_id"])
            if confirmed.get("terms") != _manual_order_terms(selected_signal):
                messages.error(request, "The signal terms changed after review. Review the order again.")
                return redirect("dhan_orders")
            _, current_client_id = get_dhan_credentials()
            if confirmed.get("account_suffix") != (current_client_id[-4:] if current_client_id else ""):
                messages.error(request, "The Dhan account changed after review. Review the order again.")
                return redirect("dhan_orders")
            form = TradeExecutionForm(
                {
                    "quantity": confirmed["quantity"],
                    "journal_reason": confirmed["journal_reason"],
                }
            )
        else:
            selected_signal = get_object_or_404(TipSignal, id=request.POST.get("signal_id"))
            form = TradeExecutionForm(request.POST)

        if form.is_valid():
            missing = [
                label
                for label, value in (
                    ("security ID", selected_signal.security_id),
                    ("entry price", selected_signal.entry_price),
                    ("stop loss", selected_signal.stop_loss),
                    ("target", selected_signal.target_1),
                )
                if not value
            ]
            if missing:
                messages.error(
                    request,
                    f"Cannot place this order until it has: {', '.join(missing)}.",
                )
                return redirect("dhan_orders")

            ok, reason = risk_guard()
            if not ok:
                messages.error(request, reason)
                return redirect("dhan_orders")

            quantity = form.cleaned_data["quantity"]
            max_loss = round(
                max(float(selected_signal.entry_price) - float(selected_signal.stop_loss), 0)
                * quantity,
                2,
            )
            if action != "confirm":
                _, client_id = get_dhan_credentials()
                order_preview = {
                    "signal": selected_signal,
                    "quantity": quantity,
                    "journal_reason": form.cleaned_data["journal_reason"],
                    "deployed": round(float(selected_signal.entry_price or 0) * quantity, 2),
                    "max_loss": max_loss,
                    "account": f"...{client_id[-4:]}" if client_id else "not configured",
                    "environment": "development" if settings.DEBUG else "production",
                    "token": signing.dumps(
                        {
                            "signal_id": selected_signal.id,
                            "quantity": quantity,
                            "journal_reason": form.cleaned_data["journal_reason"],
                            "terms": _manual_order_terms(selected_signal),
                            "account_suffix": client_id[-4:] if client_id else "",
                        },
                        salt="manual-dhan-order",
                    ),
                }
            elif request.POST.get("confirm_live_order") != "1":
                messages.error(request, "Confirm that this order will be sent to Dhan.")
                return redirect("dhan_orders")
            else:
                policy = execution_policy()
                if not policy.allows_broker_mutations:
                    messages.error(
                        request,
                        f"Broker orders are blocked in {policy.mode.value} mode.",
                    )
                    return redirect("dhan_orders")
                DhanOrderEvent.objects.create(
                    order_id="",
                    correlation_id="",
                    status="MANUAL_CONFIRM",
                    payload_json={
                        "at": timezone.localtime().isoformat(),
                        "actor": request.user.get_username(),
                        "signal_id": selected_signal.id,
                        "quantity": quantity,
                        "max_loss": max_loss,
                    },
                )
                result = place_super_order(selected_signal, quantity)
                if not result.get("ok"):
                    messages.error(request, f"Order failed: {result.get('error')}")
                    return redirect("dhan_orders")

                TradeExecution.objects.create(
                    signal=selected_signal,
                    dhan_order_id=result.get("order_id", ""),
                    correlation_id=result.get("correlation_id", ""),
                    quantity=quantity,
                    entry_price=selected_signal.entry_price,
                    stop_loss=selected_signal.stop_loss,
                    target_1=selected_signal.target_1,
                    target_2=selected_signal.target_2,
                    target_3=selected_signal.target_3,
                    execution_mode=TradeExecutionMode.MANUAL,
                    journal_reason=form.cleaned_data["journal_reason"],
                )
                selected_signal.status = SignalStatus.ACTIVE
                selected_signal.save(update_fields=["status", "updated_at"])
                messages.success(request, "Trade placed using Dhan Super Order policy A (T1+SL).")
                return redirect("dhan_orders")
    else:
        form = TradeExecutionForm()

    ideas = TipSignal.objects.filter(status__in=[SignalStatus.CANDIDATE, SignalStatus.NEW]).order_by("-score", "-id")[:100]
    executions = TradeExecution.objects.select_related("signal").all()[:120]
    events = DhanOrderEvent.objects.all()[:120]
    open_count = TradeExecution.objects.filter(state=TradeState.OPEN).count()
    now = timezone.localtime()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    today_count = TradeExecution.objects.filter(opened_at__range=(start, end)).count()

    return render(
        request,
        "options_tracker/dhan_orders.html",
        {
            "title": "Dhan Positions/Orders",
            "ideas": ideas,
            "executions": executions,
            "events": events,
            "form": form,
            "order_preview": order_preview,
            "selected_signal": selected_signal,
            "open_count": open_count,
            "today_count": today_count,
        },
    )


@staff_required_for_unsafe_methods
@require_http_methods(["GET", "POST"])
def trade_journal(request):
    if request.method == "POST":
        trade = get_object_or_404(TradeExecution, id=request.POST.get("trade_id"))
        action = request.POST.get("action")
        if action == "update_journal":
            journal_reason = request.POST.get("journal_reason", "").strip()
            emotion = request.POST.get("emotion", "").strip()
            screenshot_url = request.POST.get("screenshot_url", "").strip()
            if len(journal_reason) < 15:
                messages.error(request, "Journal reason must contain at least 15 characters.")
                return redirect("trade_journal")
            if emotion and emotion not in TradeEmotion.values:
                messages.error(request, "Unsupported trade emotion.")
                return redirect("trade_journal")
            if screenshot_url:
                try:
                    URLValidator(schemes=["https"])(screenshot_url)
                except ValidationError:
                    messages.error(request, "Screenshot must use a valid HTTPS URL.")
                    return redirect("trade_journal")
            trade.journal_reason = journal_reason
            trade.emotion = emotion
            trade.screenshot_url = screenshot_url
            trade.save(update_fields=["journal_reason", "emotion", "screenshot_url"])
            messages.success(request, "Journal reflection updated.")
            return redirect("trade_journal")
        if action == "close":
            if not trade.journal_reason or len(trade.journal_reason.strip()) < 15:
                messages.error(request, "Journal is mandatory before close.")
                return redirect("trade_journal")
            trade.state = TradeState.CLOSED
            trade.closed_at = timezone.now()
            trade.save(update_fields=["state", "closed_at"])
            trade.signal.status = SignalStatus.CLOSED
            trade.signal.save(update_fields=["status", "updated_at"])
            messages.success(request, "Trade closed and journal preserved.")
            return redirect("trade_journal")

    state = request.GET.get("state", "").strip()
    q = request.GET.get("q", "").strip()
    opened_from_raw = request.GET.get("opened_from", "").strip()
    opened_to_raw = request.GET.get("opened_to", "").strip()
    if state and state not in TradeState.values:
        return HttpResponseBadRequest("Unsupported trade state filter.")
    opened_from = parse_date(opened_from_raw) if opened_from_raw else None
    opened_to = parse_date(opened_to_raw) if opened_to_raw else None
    if opened_from_raw and opened_from is None:
        return HttpResponseBadRequest("Invalid opened-from date.")
    if opened_to_raw and opened_to is None:
        return HttpResponseBadRequest("Invalid opened-to date.")
    if opened_from and opened_to and opened_from > opened_to:
        return HttpResponseBadRequest("Opened-from date cannot follow opened-to date.")
    rows = TradeExecution.objects.select_related("signal").all()
    if state:
        rows = rows.filter(state=state)
    if q:
        rows = rows.filter(
            Q(signal__option_symbol__icontains=q)
            | Q(signal__source_name__icontains=q)
            | Q(signal__reason_tags__icontains=q)
            | Q(journal_reason__icontains=q)
            | Q(emotion__icontains=q),
        )
    if opened_from:
        rows = rows.filter(opened_at__date__gte=opened_from)
    if opened_to:
        rows = rows.filter(opened_at__date__lte=opened_to)
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="arc-trade-journal.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "trade_id",
            "opened_at",
            "closed_at",
            "symbol",
            "source",
            "state",
            "execution_mode",
            "quantity",
            "entry_price",
            "stop_loss",
            "target_1",
            "target_2",
            "target_3",
            "journal_reason",
            "emotion",
            "screenshot_url",
        ])
        for trade in rows[:5000]:
            reason = trade.journal_reason
            if reason.startswith(("=", "+", "-", "@")):
                reason = f"'{reason}"
            writer.writerow([
                trade.pk,
                trade.opened_at.isoformat(),
                trade.closed_at.isoformat() if trade.closed_at else "",
                trade.signal.option_symbol,
                trade.signal.source_name,
                trade.state,
                trade.execution_mode,
                trade.quantity,
                trade.entry_price,
                trade.stop_loss,
                trade.target_1,
                trade.target_2,
                trade.target_3,
                reason,
                trade.emotion,
                trade.screenshot_url,
            ])
        return response
    return render(request, "options_tracker/trade_journal.html", {
        "title": "Trade Journal",
        "rows": rows[:200],
        "state": state,
        "q": q,
        "opened_from": opened_from_raw,
        "opened_to": opened_to_raw,
        "emotion_choices": TradeEmotion.choices,
    })


@staff_required_for_unsafe_methods
@require_http_methods(["GET", "POST"])
def archive(request):
    if request.method == "POST" and request.POST.get("action") == "run":
        moved = archive_old_signals()
        messages.success(request, f"Archived {moved} expired contracts and older closed records.")
        return redirect("archive")

    month = request.GET.get("month", "")
    source = request.GET.get("source", "").strip()
    q = request.GET.get("q", "").strip()
    rows = TipSignal.objects.filter(
        Q(status=SignalStatus.ARCHIVED)
        | Q(
            direction__in=[Direction.CE, Direction.PE],
            expiry_date__lt=timezone.localdate(),
        )
    )
    if month:
        rows = rows.filter(Q(expiry_date__month=month) | Q(executions__closed_at__month=month)).distinct()
    if source:
        rows = rows.filter(source_name__icontains=source)
    if q:
        rows = rows.filter(Q(option_symbol__icontains=q) | Q(raw_text__icontains=q))

    return render(
        request,
        "options_tracker/archive.html",
        {"title": "Archive", "rows": rows[:300], "month": month, "source": source, "q": q},
    )


LIVE_TABS = ("live", "trades", "metrics", "risk")


def _proposed_settings(post):
    """Read the risk form back, keeping anything the user did not touch."""
    proposed = dict(live_settings())
    for row in panel_rows():
        raw = post.get(f"set_{row['key']}", "").strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        proposed[row["key"]] = value / 100 if row.get("percent") else value
    return proposed


@staff_member_required(login_url="admin:login")
@require_http_methods(["GET", "POST"])
def nifty_live(request):
    """The live strategy: what the engine is doing, what it did, and its risk."""
    preview_of = None
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "save":
            settings, notes = save_live_settings(_proposed_settings(request.POST))
            messages.success(request, "Risk settings saved. They apply from the next entry.")
            for note in notes:
                messages.warning(request, note)
            return redirect(f"{reverse('nifty_live')}?tab=risk")
        if action == "reset":
            reset_live_settings()
            messages.success(request, "Back to the validated settings.")
            return redirect(f"{reverse('nifty_live')}?tab=risk")
        if action == "preview":
            preview_of = _proposed_settings(request.POST)
        if action in ("arm", "disarm"):
            # The kill switch. Read by the engine every tick, so this stops or
            # starts entries within about fifteen seconds and without a redeploy.
            AppSetting.objects.update_or_create(
                key="nifty_live_enabled", defaults={"value": "1" if action == "arm" else "0"},
            )
            DhanOrderEvent.objects.create(
                order_id="", correlation_id="", status="ENGINE_SWITCH",
                payload_json={
                    "at": timezone.localtime().isoformat(),
                    "action": action,
                    "execution_mode": engine_snapshot()["execution_mode"],
                },
            )
            execution_mode = engine_snapshot()["execution_mode"]
            messages.success(
                request,
                (
                    "Engine armed in legacy automatic mode. It may take the next valid signal."
                    if action == "arm" and execution_mode == "legacy_auto"
                    else "Monitoring armed. Valid signals will be recorded for manual review."
                    if action == "arm"
                    else "Engine disarmed. Any open position is still managed to its stop."
                ),
            )
            return redirect(reverse("nifty_live"))

    tab = request.GET.get("tab", "live")
    if tab not in LIVE_TABS:
        tab = "live"
    if preview_of:
        # A preview POST carries no query string, so without this the result is
        # computed and then rendered on a tab that has nowhere to show it.
        tab = "risk"

    rows = trade_rows()
    settings = live_settings()
    engine = engine_snapshot()
    surface = risk_surface()
    results = performance(rows, settings["capital"])

    context = {
        "title": "NIFTY Live Strategy",
        "tab": tab,
        "engine": engine,
        "enabled": (AppSetting.objects.filter(key="nifty_live_enabled")
                    .values_list("value", flat=True).first() or "").strip() in ("1", "true", "True", "yes"),
        "today": today_summary(rows),
        "settings": settings,
        "rows": rows[:300],
        "row_count": len(rows),
        "performance": results,
        "curve": curve_svg(results["curve"]),
        "months": by_month(rows),
        "panel": risk_panel(),
        "deploy": sync_status(),
        "warnings": warnings_for(settings),
        "baseline": surface.get("baseline"),
        "sessions": surface.get("sessions"),
        "has_surface": bool(surface),
        "current_sizing": sizing_preview(settings),
        "preview": sizing_preview(preview_of) if preview_of else None,
        "preview_warnings": warnings_for(preview_of) if preview_of else [],
        "events": recent_events(40) if tab == "live" else [],
    }
    return render(request, "options_tracker/nifty_live.html", context)


@staff_member_required(login_url="admin:login")
@require_http_methods(["GET"])
def nifty_live_status_api(request):
    """Just the engine's heartbeat, for the Live tab to poll without a reload."""
    engine = engine_snapshot()
    today = today_summary(trade_rows())
    return versioned_json({
        "ok": True,
        "state": engine["state"],
        "dry_run": engine["dry_run"],
        "execution_mode": engine["execution_mode"],
        "automatic_entries": engine["automatic_entries"],
        "position_management_live": engine["position_management_live"],
        "configuration_error": engine["configuration_error"],
        "stale": engine["stale"],
        "age_seconds": engine["age_seconds"],
        "at": engine["at"],
        "notes": engine["notes"],
        "rejections": engine["rejections"],
        "session": engine["session"],
        "position": engine["position"],
        "trades_today": engine["trades_today"],
        "realized_r": engine["realized_r"],
        "net_pnl_today": today["net_pnl"],
        "observed_today": today["observed"],
    })
