"""Synchronize legacy signal sources into canonical opportunities."""

import json

from django.core.management.base import BaseCommand

from options_tracker.instrument_identity import resolve_instrument
from options_tracker.models import (
    ChatMessage,
    ChartinkTrigger,
    ScannerMatch,
    SignalStatus,
    TipSignal,
    TriggerStatus,
)
from options_tracker.opportunities import upsert_opportunity
from options_tracker.radar_models import RadarOpportunity


ACTIVE_RADAR_STATES = (
    RadarOpportunity.State.ARMED,
    RadarOpportunity.State.TRIGGERED,
    RadarOpportunity.State.ELIGIBLE,
    RadarOpportunity.State.TAKEN,
    RadarOpportunity.State.EXIT_PENDING,
)


class Command(BaseCommand):
    help = "Report or apply canonical opportunities from active tips, scanner triggers, and unparsed Telegram tips."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--include-unparsed",
            action="store_true",
            help="Include unparsed Telegram messages as score-zero review items.",
        )

    def handle(self, *args, **options):
        tips = list(
            TipSignal.objects.filter(
                status__in=[SignalStatus.NEW, SignalStatus.CANDIDATE, SignalStatus.ACTIVE],
            )
        )
        triggers = list(ChartinkTrigger.objects.filter(status=TriggerStatus.MONITORING))
        scanner_matches = list(
            ScannerMatch.objects.select_related("definition").filter(
                is_active=True,
                state="CONFIRMED",
                definition__enabled=True,
            )
        )
        chats = list(
            ChatMessage.objects.filter(source_category="TIPS", linked_signal__isnull=True)
        )
        radar_opportunities = list(
            RadarOpportunity.objects.select_related("contract__canonical_instrument").filter(
                state__in=ACTIVE_RADAR_STATES,
            )
        )
        synchronized = 0
        if options["apply"]:
            for signal in tips:
                instrument = signal.instrument or resolve_instrument(
                    exchange_segment=signal.exchange_segment,
                    security_id=signal.security_id,
                )
                if instrument and signal.instrument_id != instrument.pk:
                    signal.instrument = instrument
                    signal.save(update_fields=["instrument", "updated_at"])
                upsert_opportunity(
                    source_type=signal.source_type or "TIP",
                    source_ref=f"TIP:{signal.pk}",
                    source_name=signal.source_name,
                    strategy=signal.trade_style,
                    direction=signal.direction,
                    timeframe=signal.trade_style,
                    instrument=instrument,
                    score=signal.score,
                    rationale=signal.reason_tags or signal.raw_text,
                    evidence={
                        "symbol": signal.option_symbol,
                        "recommendation": signal.recommendation,
                        "status": signal.status,
                    },
                    plan={
                        "entry": signal.entry_price,
                        "stop": signal.stop_loss,
                        "targets": [signal.target_1, signal.target_2, signal.target_3],
                        "expiry": signal.expiry_date,
                    },
                    next_action="Manage position" if signal.status == SignalStatus.ACTIVE else "Validate trade plan",
                    source_at=signal.tip_time,
                )
                synchronized += 1
            for radar in radar_opportunities:
                contract = radar.contract
                upsert_opportunity(
                    source_type="RADAR",
                    source_ref=f"RADAR:{radar.pk}",
                    source_name="F&O Paper Radar",
                    strategy=f"{radar.strategy}:{radar.version}",
                    direction=radar.side,
                    timeframe="INTRADAY",
                    instrument=contract.canonical_instrument if contract else None,
                    rationale=radar.reason,
                    evidence={
                        "key": radar.key,
                        "symbol": radar.symbol,
                        "state": radar.state,
                        "setup_at": radar.setup_at,
                        "trigger_at": radar.trigger_at,
                        "valid_until": radar.valid_until,
                        "policy": radar.policy,
                        "radar_evidence": radar.evidence,
                    },
                    plan={
                        "entry": radar.entry_limit or radar.trigger,
                        "stop_loss": radar.stop,
                        "quantity": radar.quantity,
                    },
                    next_action=(
                        "Manage paper position"
                        if radar.state in {
                            RadarOpportunity.State.TAKEN,
                            RadarOpportunity.State.EXIT_PENDING,
                        }
                        else "Validate complete risk plan"
                    ),
                    source_at=radar.trigger_at or radar.setup_at,
                    expires_at=radar.valid_until,
                )
                synchronized += 1
            for trigger in triggers:
                upsert_opportunity(
                    source_type="SCANNER",
                    source_ref=f"CHARTINK:{trigger.pk}",
                    source_name=trigger.scanner_name,
                    strategy="SCANNER_TRIGGER",
                    direction="LONG",
                    timeframe="INTRADAY",
                    score=55,
                    rationale=trigger.notes,
                    evidence={
                        "symbol": trigger.symbol,
                        "trigger_price": trigger.trigger_price,
                        "live_price": trigger.live_price,
                    },
                    next_action="Add stop and target before approval",
                    source_at=trigger.trigger_time,
                )
                synchronized += 1
            for match in scanner_matches:
                instrument = resolve_instrument(
                    provider="RADAR",
                    alias=match.symbol,
                    exchange_segment="NSE_EQ",
                )
                upsert_opportunity(
                    source_type="SCANNER",
                    source_ref=f"CONFIGURABLE:{match.pk}",
                    source_name=match.definition.name,
                    strategy="CONFIGURABLE_SCANNER",
                    direction="LONG",
                    timeframe="INTRADAY",
                    instrument=instrument,
                    score=60,
                    rationale=f"{match.symbol} met the configured scanner conditions.",
                    evidence={
                        "symbol": match.symbol,
                        "state": match.state,
                        "conditions": match.definition.conditions,
                        "scanner_evidence": match.evidence,
                    },
                    next_action="Validate a complete risk plan before approval",
                    source_at=match.last_seen_at,
                )
                synchronized += 1
            if options["include_unparsed"]:
                for chat in chats:
                    upsert_opportunity(
                        source_type="TELEGRAM",
                        source_ref=f"CHAT:{chat.pk}",
                        source_name=chat.source_name,
                        strategy="UNPARSED_TIP",
                        timeframe="UNKNOWN",
                        score=0,
                        rationale=(chat.normalized_text or chat.raw_text)[:500],
                        evidence={"message": chat.raw_text},
                        next_action="Parse and validate source claims",
                        source_at=chat.telegram_message_at or chat.created_at,
                    )
                    synchronized += 1
        self.stdout.write(json.dumps({
            "mode": "apply" if options["apply"] else "dry-run",
            "include_unparsed": options["include_unparsed"],
            "candidates": {
                "tips": len(tips),
                "radar_opportunities": len(radar_opportunities),
                "scanner_triggers": len(triggers),
                "configurable_scanner_matches": len(scanner_matches),
                "telegram_messages": len(chats),
            },
            "synchronized": synchronized,
        }))
