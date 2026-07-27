import re
import json
import os
from decimal import Decimal

from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt

from .forms import SignalFilterForm, TelegramBulkForm, TipSignalForm, TradeExecutionForm, TriggerPromoteForm
from .models import (
    AppSetting,
    ChartinkTrigger,
    ChatMessage,
    DhanOrderEvent,
    IndexOISnapshot,
    SignalStatus,
    TipSignal,
    TradeExecution,
    TradeState,
    TradeStyle,
)
from .services import (
    archive_old_signals,
    classify_regime,
    parse_tip_text,
    place_super_order,
    risk_guard,
    score_signal,
    set_oi_interval_seconds,
    sync_chartink_from_legacy,
    sync_index_oi_from_legacy,
    sync_telegram_from_legacy,
)


def home(request):
    return redirect("options_tracker")


def _ingest_single_telegram_message(source_name, raw_text, trade_style):
    source = str(source_name or "Telegram").strip() or "Telegram"
    text = str(raw_text or "").strip()
    if not text:
        return {"status": "empty"}

    normalized = " ".join(text.split())
    if ChatMessage.objects.filter(source_name=source, normalized_text=normalized).exists():
        return {"status": "duplicate"}

    parsed = parse_tip_text(text)
    is_tip = bool(parsed["symbol"] and parsed["direction"] and parsed["sl"])
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
        raw_text=text,
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
    incoming_token = str(request.headers.get("X-Telegram-Ingest-Token", "") or "").strip()
    if expected_token and incoming_token != expected_token:
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=401)

    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON body"}, status=400)

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
    f = SignalFilterForm(request.GET)
    items = TipSignal.objects.exclude(status=SignalStatus.ARCHIVED).order_by("-tip_time", "-id")
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
            items = items.filter(source_name__icontains=source)
        if style:
            items = items.filter(trade_style=style)
        if q:
            items = items.filter(Q(option_symbol__icontains=q) | Q(raw_text__icontains=q))
        if score_min.isdigit():
            items = items.filter(score__gte=int(score_min))
        if score_max.isdigit():
            items = items.filter(score__lte=int(score_max))

    ctx = {
        "title": "Options Tracker",
        "items": items[:300],
        "form": form,
        "filter_form": f,
        "score_min": request.GET.get("score_min", ""),
        "score_max": request.GET.get("score_max", ""),
        "open_trade_panel": panel in {"trade", "new"},
    }
    return render(request, "options_tracker/options_tracker.html", ctx)


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
        created_message_ids = []
        for block in blocks:
            normalized = " ".join(block.split())
            exists = ChatMessage.objects.filter(source_name=source, normalized_text=normalized).exists()
            if exists:
                skipped_messages += 1
                continue

            parsed = parse_tip_text(block)
            is_tip = bool(parsed["symbol"] and parsed["direction"] and parsed["sl"])
            linked_signal = None
            if is_tip:
                linked_signal = TipSignal.objects.filter(source_type="TELEGRAM", source_name=source, raw_text=block).first()
                if not linked_signal:
                    linked_signal = TipSignal(
                        source_type="TELEGRAM",
                        source_name=source,
                        raw_text=block,
                        option_symbol=parsed["symbol"],
                        direction=parsed["direction"],
                        trade_style=style,
                        entry_price=parsed["entry"],
                        stop_loss=parsed["sl"],
                        target_1=parsed["t1"],
                        target_2=parsed["t2"],
                        target_3=parsed["t3"],
                        status=SignalStatus.CANDIDATE,
                    )
                    linked_signal.score, linked_signal.recommendation, linked_signal.reason_tags = score_signal(linked_signal)
                    linked_signal.save()
                    created_tips += 1

            row = ChatMessage.objects.create(
                source_name=source,
                raw_text=block,
                normalized_text=normalized,
                is_tip_candidate=is_tip,
                linked_signal=linked_signal,
            )
            created_message_ids.append(row.id)
            tracked_messages += 1

        if created_tips == 0 and tracked_messages > 0:
            aggregate_parsed = parse_tip_text(raw_bulk)
            aggregate_is_tip = bool(aggregate_parsed["symbol"] and aggregate_parsed["direction"] and aggregate_parsed["sl"])
            if aggregate_is_tip:
                aggregate_signal = TipSignal.objects.filter(source_type="TELEGRAM", source_name=source, raw_text=raw_bulk).first()
                if not aggregate_signal:
                    aggregate_signal = TipSignal(
                        source_type="TELEGRAM",
                        source_name=source,
                        raw_text=raw_bulk,
                        option_symbol=aggregate_parsed["symbol"],
                        direction=aggregate_parsed["direction"],
                        trade_style=style,
                        entry_price=aggregate_parsed["entry"],
                        stop_loss=aggregate_parsed["sl"],
                        target_1=aggregate_parsed["t1"],
                        target_2=aggregate_parsed["t2"],
                        target_3=aggregate_parsed["t3"],
                        status=SignalStatus.CANDIDATE,
                    )
                    aggregate_signal.score, aggregate_signal.recommendation, aggregate_signal.reason_tags = score_signal(aggregate_signal)
                    aggregate_signal.save()
                    created_tips += 1

                if created_message_ids:
                    ChatMessage.objects.filter(id__in=created_message_ids).update(is_tip_candidate=True, linked_signal=aggregate_signal)

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
        interval = int(request.POST.get("interval_seconds", "60"))
        set_oi_interval_seconds(interval)
        messages.success(request, f"OI polling interval set to {interval} seconds.")
        return redirect("index_oi")

    interval_setting = AppSetting.objects.filter(key="oi_interval_seconds").first()
    selected_interval = int(interval_setting.value) if interval_setting else 60

    if request.GET.get("mock") == "1":
        for underlying, put_oi, call_oi in (("NIFTY", 1540000, 1320000), ("SENSEX", 940000, 1010000)):
            regime = classify_regime(put_oi, call_oi)
            pcr = round((put_oi / call_oi), 3) if call_oi else 0.0
            IndexOISnapshot.objects.create(
                underlying=underlying,
                put_oi=put_oi,
                call_oi=call_oi,
                pcr=pcr,
                regime=regime,
                interval_seconds=selected_interval,
            )
        messages.info(request, "Mock OI snapshots inserted.")
        return redirect("index_oi")

    if request.GET.get("sync") == "1":
        inserted, status_msg = sync_index_oi_from_legacy()
        if status_msg == "OK":
            messages.success(request, f"Synced {inserted} index OI snapshots from legacy DB.")
        else:
            messages.warning(request, f"Sync partial: {inserted}. {status_msg}")
        return redirect("index_oi")

    rows = IndexOISnapshot.objects.all()[:200]
    return render(
        request,
        "options_tracker/index_oi.html",
        {"title": "Index OI", "rows": rows, "selected_interval": selected_interval},
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
