import re
import json
import os
import secrets
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models.deletion import ProtectedError
from django.db.models import Count, Max, Min, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt

from .forms import DhanCredentialsForm, SignalFilterForm, TelegramBulkForm, TipSignalForm, TrackedOptionEditForm, TradeExecutionForm, TriggerPromoteForm
from .jump_detector import historical_jump_report, jump_detector_state, live_jump_candidates
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
    TradeExecution,
    TradeState,
    TradeStyle,
)
from .services import (
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
    validate_dhan_credentials,
)
from .strategy_backtest import NIFTY_PUT_RESEARCH_SUMMARY


def home(request):
    return redirect("options_tracker")


def _session_bounds(session_date):
    start = timezone.make_aware(datetime.combine(session_date, time.min))
    return start, start + timedelta(days=1)


def _latest_session_dates(queryset, field_name, limit=30):
    latest_timestamp = queryset.order_by(f"-{field_name}").values_list(field_name, flat=True).first()
    if not latest_timestamp:
        return []
    dates = []
    session_date = timezone.localtime(latest_timestamp).date()
    for _ in range(366):
        session_start, session_end = _session_bounds(session_date)
        if queryset.filter(
            **{
                f"{field_name}__gte": session_start,
                f"{field_name}__lt": session_end,
            }
        ).exists():
            dates.append(session_date)
            if len(dates) >= limit:
                break
        session_date -= timedelta(days=1)
    return dates


def market_ticker_api(request):
    snapshot = IndexOISnapshot.objects.filter(underlying="NIFTY").order_by("-created_at").first()
    if not snapshot or snapshot.underlying_price is None:
        return JsonResponse({"ok": False, "error": "No NIFTY snapshot is available."}, status=503)
    age_seconds = max(0, int((timezone.now() - snapshot.created_at).total_seconds()))
    price = float(snapshot.underlying_price)
    change = float(snapshot.underlying_change or 0)
    previous = price - change
    return JsonResponse({
        "ok": True,
        "symbol": "NIFTY50",
        "price": price,
        "change": change,
        "change_percent": round(change / previous * 100, 2) if previous else 0,
        "updated_at": timezone.localtime(snapshot.created_at).isoformat(),
        "stale": age_seconds > 300,
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
    return render(request, "options_tracker/dhan_settings.html", {
        "title": "Dhan API Setup",
        "form": form,
        "dhan_configured": bool(access_token and configured_client_id),
        "dhan_token_source": "Dashboard override" if token_setting else "Azure App Setting",
        "dhan_token_updated_at": token_setting.updated_at if token_setting else None,
        "dhan_token_expires_at": token_setting.updated_at + timedelta(hours=24) if token_setting else None,
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
    expected_token = str(AppSetting.objects.filter(key="telegram_ingest_token").values_list("value", flat=True).first() or "").strip()
    if not expected_token:
        expected_token = str(os.environ.get("TELEGRAM_INGEST_TOKEN", "") or "").strip()
    incoming_token = str(
        request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        or request.headers.get("X-Telegram-Ingest-Token")
        or ""
    ).strip()
    if expected_token and incoming_token != expected_token:
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


@require_http_methods(["GET", "POST"])
def options_tracker(request):
    if request.method == "GET" and request.GET.get("sync") == "legacy":
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
    tracked_tips = TipSignal.objects.exclude(status=SignalStatus.ARCHIVED)
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

    ctx = {
        "title": "Options Tracker",
        "items": items[:300],
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


@require_http_methods(["POST"])
def option_live_prices(request):
    tracked_tips = TipSignal.objects.exclude(status=SignalStatus.ARCHIVED)
    options = tracked_tips.filter(direction__in=["CE", "PE"])
    tracker_tab = request.GET.get("tab", "options").strip().lower()
    selected_tips = tracked_tips.filter(direction=Direction.EQ) if tracker_tab == "equities" else options
    refresh_result = refresh_dhan_option_prices(selected_tips)
    rows = list(selected_tips.values(
        "id", "live_price", "entry_price", "outcome_status", "quote_updated_at", "security_id", "exchange_segment",
        "dhan_display_name", "expiry_date",
    ))
    return JsonResponse({
        "ok": not bool(refresh_result["error"]),
        "error": refresh_result["error"],
        "market_closed": bool(refresh_result.get("market_closed")),
        "rows": rows,
        "counts": {
            "tracked": selected_tips.count(),
            "target": selected_tips.filter(outcome_status=OptionOutcome.TARGET_1).count(),
            "stop_loss": selected_tips.filter(outcome_status=OptionOutcome.STOP_LOSS).count(),
        },
    })


@require_http_methods(["POST"])
def option_edit(request, signal_id):
    signal = get_object_or_404(TipSignal, id=signal_id)
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
    return redirect("options_tracker")


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


@require_http_methods(["GET", "POST"])
def scanners(request):
    if request.GET.get("sync") == "1":
        created, status_msg = sync_chartink_from_legacy(limit=300)
        if status_msg == "OK":
            messages.success(request, f"Synced {created} Chartink triggers from legacy DB.")
        else:
            messages.warning(request, f"Sync partial: {created}. {status_msg}")
        return redirect("scanners")

    promote_form = TriggerPromoteForm(request.POST or None)
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


@require_http_methods(["GET", "POST"])
def telegram_feed(request):
    if request.GET.get("sync") == "1":
        style = request.GET.get("style", "INTRADAY")
        created, status_msg = sync_telegram_from_legacy(limit=300, trade_style=style)
        if status_msg == "OK":
            messages.success(request, f"Synced {created} telegram tips from legacy DB.")
        else:
            messages.warning(request, f"Sync partial: {created}. {status_msg}")
        return redirect("telegram_feed")

    form = TelegramBulkForm(request.POST or None)
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


@require_http_methods(["GET", "POST"])
def dhan_orders(request):
    if request.method == "POST":
        signal_id = request.POST.get("signal_id")
        signal = get_object_or_404(TipSignal, id=signal_id)
        form = TradeExecutionForm(request.POST)
        if form.is_valid():
            ok, reason = risk_guard()
            if not ok:
                messages.error(request, reason)
                return redirect("dhan_orders")

            result = place_super_order(signal, form.cleaned_data["quantity"])
            if not result.get("ok"):
                messages.error(request, f"Order failed: {result.get('error')}")
                return redirect("dhan_orders")

            TradeExecution.objects.create(
                signal=signal,
                dhan_order_id=result.get("order_id", ""),
                correlation_id=result.get("correlation_id", ""),
                quantity=form.cleaned_data["quantity"],
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                target_1=signal.target_1,
                target_2=signal.target_2,
                target_3=signal.target_3,
                journal_reason=form.cleaned_data["journal_reason"],
            )
            signal.status = SignalStatus.ACTIVE
            signal.save(update_fields=["status", "updated_at"])
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
            "open_count": open_count,
            "today_count": today_count,
        },
    )


@require_http_methods(["GET", "POST"])
def trade_journal(request):
    if request.method == "POST":
        trade = get_object_or_404(TradeExecution, id=request.POST.get("trade_id"))
        action = request.POST.get("action")
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
    rows = TradeExecution.objects.select_related("signal").all()
    if state:
        rows = rows.filter(state=state)
    if q:
        rows = rows.filter(signal__option_symbol__icontains=q)
    rows = rows[:200]
    return render(request, "options_tracker/trade_journal.html", {"title": "Trade Journal", "rows": rows, "state": state, "q": q})


def archive(request):
    if request.GET.get("run") == "1":
        moved = archive_old_signals()
        messages.success(request, f"Archived {moved} records by expiry/close month.")
        return redirect("archive")

    month = request.GET.get("month", "")
    source = request.GET.get("source", "").strip()
    q = request.GET.get("q", "").strip()
    rows = TipSignal.objects.filter(status=SignalStatus.ARCHIVED)
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
