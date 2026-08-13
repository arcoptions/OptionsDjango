from django.core.management.base import BaseCommand
from django.db import transaction

from options_tracker.models import ChatMessage, SignalStatus, TipSignal, TradeStyle
from options_tracker.services import parse_tip_text, score_signal


class Command(BaseCommand):
    help = "Rebuild parsed Telegram tips from raw messages categorized as TIPS."

    @transaction.atomic
    def handle(self, *args, **options):
        valid_signal_ids = set()
        parsed_count = 0
        rejected_count = 0
        rows = ChatMessage.objects.filter(source_category="TIPS").order_by("telegram_message_at", "id")

        for row in rows.iterator():
            parsed = parse_tip_text(row.raw_text)
            is_tip = bool(parsed["symbol"] and parsed["direction"] and parsed["sl"] and parsed["t1"])
            if not is_tip:
                row.is_tip_candidate = False
                row.linked_signal = None
                row.save(update_fields=["is_tip_candidate", "linked_signal"])
                rejected_count += 1
                continue

            signal = TipSignal.objects.filter(
                source_type="TELEGRAM",
                source_name=row.source_name,
                raw_text=row.raw_text,
            ).first()
            if not signal:
                signal = TipSignal(source_type="TELEGRAM", source_name=row.source_name, raw_text=row.raw_text)

            signal.option_symbol = parsed["symbol"]
            signal.direction = parsed["direction"]
            signal.trade_style = signal.trade_style or TradeStyle.INTRADAY
            signal.entry_price = parsed["entry"]
            signal.stop_loss = parsed["sl"]
            signal.target_1 = parsed["t1"]
            signal.target_2 = parsed["t2"]
            signal.target_3 = parsed["t3"]
            signal.tip_time = row.telegram_message_at or row.created_at
            if not signal.pk:
                signal.status = SignalStatus.CANDIDATE
            signal.score, signal.recommendation, signal.reason_tags = score_signal(signal)
            signal.save()

            row.is_tip_candidate = True
            row.linked_signal = signal
            row.save(update_fields=["is_tip_candidate", "linked_signal"])
            valid_signal_ids.add(signal.id)
            parsed_count += 1

        stale = TipSignal.objects.filter(source_type="TELEGRAM").exclude(id__in=valid_signal_ids)
        deleted_count, _ = stale.filter(executions__isnull=True).delete()
        self.stdout.write(self.style.SUCCESS(
            f"Parsed {parsed_count} tips; rejected {rejected_count} raw messages; deleted {deleted_count} stale rows."
        ))