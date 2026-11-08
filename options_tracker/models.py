from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .radar_models import (
    RadarAlert, RadarBar, RadarDecision, RadarInstrument, RadarOpportunity,
    RadarOptionPoint, RadarQuote, RadarWatch, RadarWorker, RadarWorkspace,
    RadarNotificationRoute, RadarPriceAlert, RadarSector,
    RadarAssistantSnapshot, RadarResearchProtocol, RadarResearchObservation,
    RadarCatalyst, RadarHistoryConflict,
)


class TradeStyle(models.TextChoices):
    INTRADAY = "INTRADAY", "Intraday"
    SWING = "SWING", "Swing"


class Direction(models.TextChoices):
    CE = "CE", "CE"
    PE = "PE", "PE"
    EQ = "EQ", "Equity"


class SignalStatus(models.TextChoices):
    NEW = "NEW", "New"
    CANDIDATE = "CANDIDATE", "Candidate"
    ACTIVE = "ACTIVE", "Active"
    CLOSED = "CLOSED", "Closed"
    REJECTED = "REJECTED", "Rejected"
    ARCHIVED = "ARCHIVED", "Archived"


class OptionOutcome(models.TextChoices):
    TRACKING = "TRACKING", "Tracking"
    TARGET_1 = "TARGET_1", "Target 1 Hit"
    STOP_LOSS = "STOP_LOSS", "Stop Loss Hit"
    UNRESOLVED = "UNRESOLVED", "Instrument Not Found"


class InstrumentKind(models.TextChoices):
    EQUITY = "EQUITY", "Equity"
    INDEX = "INDEX", "Index"
    FUTURE = "FUTURE", "Future"
    OPTION = "OPTION", "Option"


class CanonicalInstrument(models.Model):
    canonical_symbol = models.CharField(max_length=80, db_index=True)
    display_name = models.CharField(max_length=160, blank=True)
    kind = models.CharField(max_length=12, choices=InstrumentKind.choices)
    exchange_segment = models.CharField(max_length=24)
    security_id = models.CharField(max_length=40)
    underlying_symbol = models.CharField(max_length=80, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    strike = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    option_type = models.CharField(max_length=2, choices=[("CE", "CE"), ("PE", "PE")], blank=True)
    lot_size = models.PositiveIntegerField(default=1)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["canonical_symbol", "expiry_date", "strike", "option_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["exchange_segment", "security_id"],
                name="canonical_instrument_security",
            ),
        ]


class InstrumentAlias(models.Model):
    instrument = models.ForeignKey(
        CanonicalInstrument,
        on_delete=models.CASCADE,
        related_name="aliases",
    )
    provider = models.CharField(max_length=30)
    value = models.CharField(max_length=120)
    exchange_segment = models.CharField(max_length=24, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["provider", "value"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "value", "exchange_segment"],
                name="instrument_alias_identity",
            ),
        ]


class OpportunityStatus(models.TextChoices):
    NEW = "NEW", "New"
    VALIDATED = "VALIDATED", "Validated"
    PLANNED = "PLANNED", "Planned"
    APPROVED = "APPROVED", "Approved"
    EXECUTED = "EXECUTED", "Executed"
    CLOSED = "CLOSED", "Closed"
    REJECTED = "REJECTED", "Rejected"


class UnifiedOpportunity(models.Model):
    instrument = models.ForeignKey(
        CanonicalInstrument,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="opportunities",
    )
    fingerprint = models.CharField(max_length=64, unique=True)
    source_type = models.CharField(max_length=30)
    source_ref = models.CharField(max_length=120)
    source_name = models.CharField(max_length=120, blank=True)
    strategy = models.CharField(max_length=80)
    direction = models.CharField(max_length=12, blank=True)
    timeframe = models.CharField(max_length=30, blank=True)
    status = models.CharField(
        max_length=16,
        choices=OpportunityStatus.choices,
        default=OpportunityStatus.NEW,
    )
    score = models.FloatField(default=0)
    confidence = models.FloatField(null=True, blank=True)
    rationale = models.TextField(blank=True)
    plan = models.JSONField(default=dict, blank=True)
    next_action = models.CharField(max_length=160, blank=True)
    source_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-score", "-source_at", "-pk"]
        indexes = [
            models.Index(fields=["status", "-score"], name="idx_opportunity_status_score"),
        ]


class OpportunityRevision(models.Model):
    opportunity = models.ForeignKey(
        UnifiedOpportunity,
        on_delete=models.PROTECT,
        related_name="revisions",
    )
    version = models.PositiveIntegerField()
    evidence_fingerprint = models.CharField(max_length=64)
    evidence = models.JSONField(default=dict)
    source_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["opportunity", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["opportunity", "version"],
                name="opportunity_revision_version",
            ),
            models.UniqueConstraint(
                fields=["opportunity", "evidence_fingerprint"],
                name="opportunity_revision_evidence",
            ),
        ]


class OpportunityStatusEvent(models.Model):
    opportunity = models.ForeignKey(
        UnifiedOpportunity,
        on_delete=models.PROTECT,
        related_name="status_events",
    )
    from_status = models.CharField(max_length=16, blank=True)
    to_status = models.CharField(max_length=16, choices=OpportunityStatus.choices)
    reason = models.TextField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    context = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-pk"]


class TipSignal(models.Model):
    instrument = models.ForeignKey(
        CanonicalInstrument,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tip_signals",
    )
    source_type = models.CharField(max_length=20, default="MANUAL")
    source_name = models.CharField(max_length=120, blank=True)
    source_ref = models.CharField(max_length=80, blank=True)
    raw_text = models.TextField(blank=True)
    option_symbol = models.CharField(max_length=80)
    security_id = models.CharField(max_length=40, blank=True)
    exchange_segment = models.CharField(max_length=20, blank=True)
    dhan_display_name = models.CharField(max_length=120, blank=True)
    live_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    quote_updated_at = models.DateTimeField(null=True, blank=True)
    outcome_status = models.CharField(max_length=20, choices=OptionOutcome.choices, default=OptionOutcome.TRACKING)
    outcome_at = models.DateTimeField(null=True, blank=True)
    direction = models.CharField(max_length=2, choices=Direction.choices)
    trade_style = models.CharField(max_length=12, choices=TradeStyle.choices, default=TradeStyle.INTRADAY)
    entry_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    stop_loss = models.DecimalField(max_digits=12, decimal_places=2)
    target_1 = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    target_2 = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    target_3 = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    score = models.PositiveSmallIntegerField(default=0)
    recommendation = models.CharField(max_length=40, blank=True)
    reason_tags = models.CharField(max_length=240, blank=True)
    status = models.CharField(max_length=20, choices=SignalStatus.choices, default=SignalStatus.NEW)
    tip_time = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-tip_time", "-id"]

    def __str__(self):
        return f"{self.option_symbol} {self.direction} ({self.trade_style})"


class ChatMessage(models.Model):
    source_name = models.CharField(max_length=120, blank=True)
    source_category = models.CharField(max_length=20, blank=True)
    telegram_chat_id = models.BigIntegerField(null=True, blank=True)
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    telegram_message_at = models.DateTimeField(null=True, blank=True)
    raw_text = models.TextField()
    raw_payload = models.TextField(blank=True)
    normalized_text = models.TextField(blank=True)
    is_tip_candidate = models.BooleanField(default=False)
    linked_signal = models.ForeignKey(TipSignal, null=True, blank=True, on_delete=models.SET_NULL, related_name="chat_messages")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["telegram_chat_id", "telegram_message_id"],
                condition=models.Q(telegram_chat_id__isnull=False, telegram_message_id__isnull=False),
                name="unique_telegram_chat_message",
            )
        ]


class TriggerStatus(models.TextChoices):
    MONITORING = "MONITORING", "Monitoring"
    MOVED = "MOVED", "Moved to Tracker"
    DISCARDED = "DISCARDED", "Discarded"


class ChartinkTrigger(models.Model):
    scanner_name = models.CharField(max_length=60)
    symbol = models.CharField(max_length=80)
    trigger_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    live_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    trigger_time = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=TriggerStatus.choices, default=TriggerStatus.MONITORING)
    notes = models.TextField(blank=True)
    promoted_signal = models.ForeignKey(TipSignal, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-trigger_time", "-id"]


class TradeState(models.TextChoices):
    OPEN = "OPEN", "Open"
    CLOSED = "CLOSED", "Closed"
    CANCELLED = "CANCELLED", "Cancelled"


class TradeExecution(models.Model):
    signal = models.ForeignKey(TipSignal, on_delete=models.PROTECT, related_name="executions")
    dhan_order_id = models.CharField(max_length=80, blank=True)
    correlation_id = models.CharField(max_length=40, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    entry_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    stop_loss = models.DecimalField(max_digits=12, decimal_places=2)
    target_1 = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    target_2 = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    target_3 = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    state = models.CharField(max_length=20, choices=TradeState.choices, default=TradeState.OPEN)
    journal_reason = models.TextField()
    opened_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-opened_at", "-id"]


class IndexOISnapshot(models.Model):
    underlying = models.CharField(max_length=10)
    expiry_date = models.DateField(null=True, blank=True)
    underlying_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    underlying_change = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    atm_strike = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    call_oi = models.BigIntegerField(default=0)
    put_oi = models.BigIntegerField(default=0)
    call_oi_change = models.BigIntegerField(default=0)
    put_oi_change = models.BigIntegerField(default=0)
    call_volume = models.BigIntegerField(default=0)
    put_volume = models.BigIntegerField(default=0)
    pcr = models.FloatField(default=0.0)
    pcr_change = models.FloatField(default=0.0)
    max_pain = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    support_strike = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    resistance_strike = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    regime = models.CharField(max_length=40, blank=True)
    interval_seconds = models.PositiveIntegerField(default=60)
    source = models.CharField(max_length=20, default="DHAN")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["underlying", "-created_at"], name="idx_oi_under_time")]


class IndexOptionStrikeSnapshot(models.Model):
    snapshot = models.ForeignKey(IndexOISnapshot, on_delete=models.CASCADE, related_name="strikes")
    strike = models.DecimalField(max_digits=14, decimal_places=2)
    option_type = models.CharField(max_length=2, choices=[("CE", "Call"), ("PE", "Put")])
    security_id = models.CharField(max_length=40, blank=True)
    last_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    price_change = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    average_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    previous_close = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    oi = models.BigIntegerField(default=0)
    previous_oi = models.BigIntegerField(default=0)
    oi_change = models.BigIntegerField(default=0)
    volume = models.BigIntegerField(default=0)
    previous_volume = models.BigIntegerField(default=0)
    implied_volatility = models.FloatField(default=0.0)
    delta = models.FloatField(default=0.0)
    theta = models.FloatField(default=0.0)
    gamma = models.FloatField(default=0.0)
    vega = models.FloatField(default=0.0)
    top_bid_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    top_bid_quantity = models.BigIntegerField(default=0)
    top_ask_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    top_ask_quantity = models.BigIntegerField(default=0)
    buy_quantity = models.BigIntegerField(default=0)
    sell_quantity = models.BigIntegerField(default=0)
    depth = models.JSONField(default=dict, blank=True)
    buildup = models.CharField(max_length=30, blank=True)
    is_atm = models.BooleanField(default=False)

    class Meta:
        ordering = ["strike", "option_type"]
        constraints = [
            models.UniqueConstraint(fields=["snapshot", "strike", "option_type"], name="unique_index_strike_snapshot")
        ]
        indexes = [
            models.Index(fields=["security_id", "-snapshot"], name="idx_strike_security_snap"),
            models.Index(fields=["option_type", "strike"], name="idx_strike_type_price"),
        ]


class IndexOptionCandle(models.Model):
    underlying = models.CharField(max_length=10)
    expiry_flag = models.CharField(max_length=10, default="WEEK")
    expiry_code = models.PositiveSmallIntegerField(default=1)
    relative_strike = models.CharField(max_length=10)
    option_type = models.CharField(max_length=4, choices=[("CALL", "Call"), ("PUT", "Put")])
    interval_minutes = models.PositiveSmallIntegerField(default=1)
    timestamp = models.DateTimeField()
    strike = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    spot = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    open = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    high = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    low = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    close = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    volume = models.BigIntegerField(default=0)
    oi = models.BigIntegerField(default=0)
    implied_volatility = models.FloatField(default=0.0)

    class Meta:
        ordering = ["-timestamp", "underlying", "relative_strike", "option_type"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "underlying", "expiry_flag", "expiry_code", "relative_strike",
                    "option_type", "interval_minutes", "timestamp",
                ],
                name="unique_index_option_candle",
            )
        ]
        indexes = [models.Index(fields=["underlying", "-timestamp"], name="idx_candle_under_time")]


class AppSetting(models.Model):
    key = models.CharField(max_length=60, unique=True)
    value = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)


class DhanOrderEvent(models.Model):
    order_id = models.CharField(max_length=80, blank=True)
    correlation_id = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=30, blank=True)
    payload_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class TrackedStock(models.Model):
    """A stock we have traded options on, harvested from the broker P&L files."""

    symbol = models.CharField(max_length=40, unique=True)
    security_id = models.CharField(max_length=20, blank=True)
    lot_size = models.PositiveIntegerField(default=0)
    strike_step = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    sector = models.CharField(max_length=60, blank=True)
    market_cap_band = models.CharField(max_length=20, blank=True)
    contracts_traded = models.PositiveIntegerField(default=0)
    realised_pnl = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    turnover = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    brokers = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    priority = models.PositiveSmallIntegerField(default=100)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-contracts_traded", "symbol"]
        indexes = [models.Index(fields=["is_active", "priority"], name="idx_stock_active_prio")]

    def __str__(self):
        return self.symbol


class StockEquityCandle(models.Model):
    symbol = models.CharField(max_length=40)
    interval_minutes = models.PositiveSmallIntegerField(default=15)
    timestamp = models.DateTimeField()
    open = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    high = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    low = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    close = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    volume = models.BigIntegerField(default=0)

    class Meta:
        ordering = ["symbol", "timestamp"]
        constraints = [
            models.UniqueConstraint(
                fields=["symbol", "interval_minutes", "timestamp"],
                name="unique_stock_equity_candle",
            )
        ]
        indexes = [models.Index(fields=["symbol", "interval_minutes", "timestamp"], name="idx_eq_sym_int_time")]


class StockOptionCandle(models.Model):
    """ATM-relative stock option bars from Dhan's rolling-option history."""

    symbol = models.CharField(max_length=40)
    expiry_code = models.PositiveSmallIntegerField(default=1)
    relative_strike = models.CharField(max_length=10)
    option_type = models.CharField(max_length=4, choices=[("CALL", "Call"), ("PUT", "Put")])
    interval_minutes = models.PositiveSmallIntegerField(default=15)
    timestamp = models.DateTimeField()
    strike = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    spot = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    open = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    high = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    low = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    close = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    volume = models.BigIntegerField(default=0)
    oi = models.BigIntegerField(default=0)
    implied_volatility = models.FloatField(default=0.0)

    class Meta:
        ordering = ["symbol", "timestamp", "relative_strike"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "symbol", "expiry_code", "relative_strike", "option_type",
                    "interval_minutes", "timestamp",
                ],
                name="unique_stock_option_candle",
            )
        ]
        indexes = [
            models.Index(fields=["symbol", "timestamp"], name="idx_opt_sym_time"),
            models.Index(fields=["symbol", "option_type", "relative_strike"], name="idx_opt_sym_type_rel"),
        ]


class StockFundamentalSnapshot(models.Model):
    symbol = models.CharField(max_length=40, db_index=True)
    provider = models.CharField(max_length=40)
    source_at = models.DateTimeField()
    received_at = models.DateTimeField(default=timezone.now)
    market_cap = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    pe_ratio = models.FloatField(null=True, blank=True)
    price_to_book = models.FloatField(null=True, blank=True)
    return_on_equity = models.FloatField(null=True, blank=True)
    debt_to_equity = models.FloatField(null=True, blank=True)
    revenue_growth = models.FloatField(null=True, blank=True)
    earnings_growth = models.FloatField(null=True, blank=True)
    dividend_yield = models.FloatField(null=True, blank=True)
    free_cash_flow = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-source_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["symbol", "provider", "source_at"],
                name="stock_fundamental_identity",
            ),
        ]


class StockFinancialResult(models.Model):
    symbol = models.CharField(max_length=40, db_index=True)
    provider = models.CharField(max_length=40)
    period_end = models.DateField()
    period_type = models.CharField(max_length=12, choices=[("QUARTER", "Quarter"), ("ANNUAL", "Annual")])
    revenue = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    operating_profit = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    net_profit = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    eps = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    revenue_growth = models.FloatField(null=True, blank=True)
    profit_growth = models.FloatField(null=True, blank=True)
    source_at = models.DateTimeField()
    received_at = models.DateTimeField(default=timezone.now)
    raw_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-period_end", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["symbol", "provider", "period_end", "period_type"],
                name="stock_result_identity",
            ),
        ]


class StockNewsItem(models.Model):
    symbol = models.CharField(max_length=40, db_index=True)
    provider = models.CharField(max_length=40)
    source_ref = models.CharField(max_length=200)
    headline = models.CharField(max_length=500)
    url = models.URLField(max_length=1000, blank=True)
    published_at = models.DateTimeField()
    received_at = models.DateTimeField(default=timezone.now)
    sentiment = models.CharField(max_length=16, blank=True)
    summary = models.TextField(blank=True)

    class Meta:
        ordering = ["-published_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "source_ref"],
                name="stock_news_identity",
            ),
        ]


class StockCorporateAction(models.Model):
    symbol = models.CharField(max_length=40, db_index=True)
    provider = models.CharField(max_length=40)
    source_ref = models.CharField(max_length=200)
    action_type = models.CharField(max_length=30)
    ex_date = models.DateField()
    description = models.TextField()
    ratio = models.CharField(max_length=40, blank=True)
    source_at = models.DateTimeField()
    received_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-ex_date", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "source_ref"],
                name="stock_action_identity",
            ),
        ]


class MarketContextCategory(models.TextChoices):
    GLOBAL_INDEX = "GLOBAL_INDEX", "Global index"
    MACRO = "MACRO", "Macro"
    FLOW = "FLOW", "Institutional flow"
    SECTOR = "SECTOR", "Sector"


class MarketContextSnapshot(models.Model):
    category = models.CharField(max_length=20, choices=MarketContextCategory.choices)
    key = models.CharField(max_length=100)
    value = models.DecimalField(max_digits=20, decimal_places=4)
    unit = models.CharField(max_length=20, blank=True)
    provider = models.CharField(max_length=60)
    source_ref = models.CharField(max_length=300)
    source_at = models.DateTimeField()
    received_at = models.DateTimeField(default=timezone.now)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["category", "key", "-source_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["category", "key", "provider", "source_at"],
                name="market_context_evidence_identity",
            ),
        ]
        indexes = [
            models.Index(
                fields=["category", "key", "-source_at"],
                name="idx_market_context_latest",
            ),
        ]


class EconomicEvent(models.Model):
    title = models.CharField(max_length=200)
    event_type = models.CharField(max_length=30)
    starts_at = models.DateTimeField()
    importance = models.CharField(
        max_length=10,
        choices=[("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High")],
    )
    provider = models.CharField(max_length=60)
    source_ref = models.CharField(max_length=300)
    received_at = models.DateTimeField(default=timezone.now)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["starts_at", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "source_ref", "starts_at"],
                name="economic_event_identity",
            ),
        ]


class StockScoreSnapshot(models.Model):
    symbol = models.CharField(max_length=40, db_index=True)
    score_type = models.CharField(
        max_length=16,
        choices=[("INVESTMENT", "Investment"), ("SWING", "Swing"), ("FNO", "F&O")],
    )
    version = models.CharField(max_length=20)
    score = models.FloatField(null=True, blank=True)
    confidence = models.FloatField(default=0)
    components = models.JSONField(default=dict)
    missing_data = models.JSONField(default=list)
    source_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-source_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["symbol", "score_type", "version", "source_at"],
                name="stock_score_identity",
            ),
        ]


class ScannerDefinition(models.Model):
    name = models.CharField(max_length=100, unique=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    enabled = models.BooleanField(default=True)
    conditions = models.JSONField(default=list)
    approaching_threshold_pct = models.FloatField(default=10)
    cooldown_minutes = models.PositiveIntegerField(default=30)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]


class ScannerRun(models.Model):
    definition = models.ForeignKey(
        ScannerDefinition,
        on_delete=models.PROTECT,
        related_name="runs",
    )
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, default="RUNNING")
    scanned = models.PositiveIntegerField(default=0)
    approaching = models.PositiveIntegerField(default=0)
    confirmed = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at", "-pk"]


class ScannerMatch(models.Model):
    definition = models.ForeignKey(
        ScannerDefinition,
        on_delete=models.PROTECT,
        related_name="matches",
    )
    symbol = models.CharField(max_length=40)
    state = models.CharField(
        max_length=16,
        choices=[("APPROACHING", "Approaching"), ("CONFIRMED", "Confirmed")],
    )
    evidence = models.JSONField(default=dict)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    last_alerted_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    telegram_sent_at = models.DateTimeField(null=True, blank=True)
    delivery_attempts = models.PositiveIntegerField(default=0)
    next_delivery_at = models.DateTimeField(default=timezone.now)
    last_delivery_error = models.CharField(max_length=300, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-last_seen_at", "symbol"]
        constraints = [
            models.UniqueConstraint(
                fields=["definition", "symbol"],
                name="scanner_match_identity",
            ),
        ]


class InvestmentIdeaStatus(models.TextChoices):
    WATCHING = "WATCHING", "Watching"
    HOLDING = "HOLDING", "Holding"
    EXITED = "EXITED", "Exited"
    REJECTED = "REJECTED", "Rejected"


class InvestmentIdea(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="investment_ideas",
    )
    instrument = models.ForeignKey(
        CanonicalInstrument,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="investment_ideas",
    )
    symbol = models.CharField(max_length=40, db_index=True)
    thesis = models.TextField()
    invalidation = models.TextField()
    accumulation_low = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    accumulation_high = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    target_allocation_pct = models.DecimalField(max_digits=5, decimal_places=2)
    review_on = models.DateField()
    status = models.CharField(
        max_length=12,
        choices=InvestmentIdeaStatus.choices,
        default=InvestmentIdeaStatus.WATCHING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if (
            self.accumulation_low is not None
            and self.accumulation_high is not None
            and self.accumulation_low > self.accumulation_high
        ):
            raise ValidationError({
                "accumulation_high": "Accumulation high must not be below the low.",
            })
        if not 0 < self.target_allocation_pct <= 100:
            raise ValidationError({
                "target_allocation_pct": "Target allocation must be above 0 and at most 100.",
            })

    class Meta:
        ordering = ["review_on", "symbol"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "symbol"],
                condition=models.Q(status__in=[
                    InvestmentIdeaStatus.WATCHING,
                    InvestmentIdeaStatus.HOLDING,
                ]),
                name="active_investment_idea",
            ),
        ]


class InvestmentHolding(models.Model):
    idea = models.OneToOneField(
        InvestmentIdea,
        on_delete=models.PROTECT,
        related_name="holding",
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    average_cost = models.DecimalField(max_digits=14, decimal_places=2)
    current_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    opened_on = models.DateField()
    closed_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.quantity <= 0:
            raise ValidationError({"quantity": "Quantity must be above 0."})
        if self.average_cost <= 0:
            raise ValidationError({"average_cost": "Average cost must be above 0."})
        if self.current_price is not None and self.current_price <= 0:
            raise ValidationError({"current_price": "Current price must be above 0."})
        if self.closed_on is not None and self.closed_on < self.opened_on:
            raise ValidationError({"closed_on": "Closed date cannot precede opened date."})


class InvestmentDividend(models.Model):
    holding = models.ForeignKey(
        InvestmentHolding,
        on_delete=models.PROTECT,
        related_name="dividends",
    )
    ex_date = models.DateField()
    amount_total = models.DecimalField(max_digits=14, decimal_places=2)
    source = models.CharField(max_length=80, blank=True)
    received_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.amount_total <= 0:
            raise ValidationError({"amount_total": "Dividend amount must be above 0."})

    class Meta:
        ordering = ["-ex_date", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["holding", "ex_date", "amount_total"],
                name="investment_dividend_identity",
            ),
        ]


class StrategyDefinition(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="strategy_definitions",
    )
    name = models.CharField(max_length=100)
    template = models.CharField(max_length=40)
    symbol = models.CharField(max_length=40)
    interval_minutes = models.PositiveSmallIntegerField(default=15)
    parameters = models.JSONField(default=dict)
    cost_model = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=1)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name", "version"],
                name="strategy_definition_version",
            ),
        ]


class StrategyRunManifest(models.Model):
    definition = models.ForeignKey(
        StrategyDefinition,
        on_delete=models.PROTECT,
        related_name="manifests",
    )
    manifest_hash = models.CharField(max_length=64, unique=True)
    definition_snapshot = models.JSONField()
    cost_snapshot = models.JSONField()
    data_start = models.DateTimeField()
    data_end = models.DateTimeField()
    data_rows = models.PositiveIntegerField()
    data_hash = models.CharField(max_length=64)
    code_version = models.CharField(max_length=80)
    result_snapshot = models.JSONField(default=dict)
    validation_snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Strategy run manifests are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Strategy run manifests are immutable.")

    class Meta:
        ordering = ["-created_at", "-pk"]


class DownloadJob(models.Model):
    """Resumable bookkeeping so a killed download picks up where it stopped."""

    kind = models.CharField(max_length=20)
    symbol = models.CharField(max_length=40)
    detail = models.CharField(max_length=60, blank=True)
    window_from = models.DateField()
    window_to = models.DateField()
    rows = models.IntegerField(default=0)
    status = models.CharField(max_length=12, default="DONE")
    error = models.TextField(blank=True)
    completed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "symbol", "detail", "window_from", "window_to"],
                name="unique_download_job",
            )
        ]
        indexes = [models.Index(fields=["kind", "symbol"], name="idx_job_kind_sym")]


class BrokerPnlEntry(models.Model):
    """One contract-level realised P&L row from a broker statement."""

    broker = models.CharField(max_length=20)
    account = models.CharField(max_length=30, blank=True)
    period_label = models.CharField(max_length=40, blank=True)
    source_file = models.CharField(max_length=120, blank=True)
    raw_symbol = models.CharField(max_length=90)
    underlying = models.CharField(max_length=40, blank=True)
    instrument_kind = models.CharField(max_length=10, blank=True)
    option_type = models.CharField(max_length=4, blank=True)
    strike = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    quantity = models.BigIntegerField(default=0)
    buy_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    sell_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    realised_pnl = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    unrealised_pnl = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    class Meta:
        ordering = ["broker", "raw_symbol"]
        indexes = [
            models.Index(fields=["broker", "period_label"], name="idx_pnl_broker_period"),
            models.Index(fields=["underlying"], name="idx_pnl_underlying"),
        ]


class BrokerPeriodSummary(models.Model):
    """Header totals for one broker statement, kept separate from the rows."""

    broker = models.CharField(max_length=20)
    account = models.CharField(max_length=30, blank=True)
    period_label = models.CharField(max_length=40)
    period_from = models.DateField(null=True, blank=True)
    period_to = models.DateField(null=True, blank=True)
    source_file = models.CharField(max_length=120, blank=True)
    gross_realised = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    charges = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    unrealised = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    charge_breakdown = models.JSONField(default=dict)

    class Meta:
        ordering = ["broker", "period_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["broker", "account", "period_label"], name="unique_broker_period"
            )
        ]

    @property
    def net_realised(self):
        return self.gross_realised - self.charges


class PickSource(models.TextChoices):
    LIVE = "LIVE", "Recorded before the outcome existed"
    BACKFILL = "BACKFILL", "Reconstructed from history"


class DailyPick(models.Model):
    """One name the shortlist put forward on one session's close.

    The row exists so the model can be held to account. Every performance
    number this project has had to withdraw was withdrawn because the result
    was computed over trades chosen with hindsight, so a pick is written down
    before its outcome exists and is never edited afterwards -- `created_at` is
    the evidence of when it was known.

    `source` is what keeps that guarantee usable. Reconstructing past picks
    from history is worth doing, because it makes the screen useful on day one
    and gives the live record something to be compared against, but it is a
    backtest and must never be counted as a decision made in advance. LIVE and
    BACKFILL therefore share a table and are never mixed in a statistic.
    """

    day = models.DateField(help_text="The session whose close produced the pick.")
    symbol = models.CharField(max_length=40)
    rank = models.PositiveSmallIntegerField(default=0, help_text="Position by turnover.")
    source = models.CharField(max_length=10, choices=PickSource.choices,
                              default=PickSource.LIVE)

    # The chart context as it stood at pick time, frozen so the screen shows
    # what was actually seen rather than what the numbers look like today.
    close = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    atr_pct = models.DecimalField(max_digits=8, decimal_places=5, null=True, blank=True)
    dollar_vol = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    pos20 = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    mom5 = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    # Filled once the five sessions have passed. Null means "not yet known",
    # which is a different thing from zero and is displayed differently.
    scored_on = models.DateField(null=True, blank=True)
    sessions_scored = models.PositiveSmallIntegerField(default=0)
    fwd_high_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    fwd_low_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    fwd_close_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    hit_up5 = models.BooleanField(null=True, blank=True)
    hit_dn5 = models.BooleanField(null=True, blank=True)
    hit_up10 = models.BooleanField(null=True, blank=True)

    class Meta:
        ordering = ["-day", "rank", "symbol"]
        constraints = [
            models.UniqueConstraint(
                fields=["day", "symbol", "source"], name="unique_daily_pick"
            )
        ]
        indexes = [
            models.Index(fields=["source", "-day"], name="idx_pick_source_day"),
            models.Index(fields=["symbol", "-day"], name="idx_pick_symbol_day"),
        ]

    def __str__(self):
        return "{} {} ({})".format(self.day, self.symbol, self.source)

    @property
    def is_scored(self):
        return self.scored_on is not None


class SwingStatus(models.TextChoices):
    WATCH = "WATCH", "Signal, no free slot"
    PENDING = "PENDING", "Buy at the next open"
    OPEN = "OPEN", "Held"
    CLOSED = "CLOSED", "Exited"


class SwingSignal(models.Model):
    """One strength-dip signal, and the paper trade it became if a slot was free.

    Written on the signal session's close and never re-ranked: the row is the
    evidence that the name was put forward before its outcome existed. LIVE and
    BACKFILL run as two separate ten-slot books and are never mixed in a
    statistic, for the same reason as `DailyPick`.
    """

    day = models.DateField(help_text="The session whose close produced the signal.")
    symbol = models.CharField(max_length=40)
    source = models.CharField(max_length=10, choices=PickSource.choices, default=PickSource.LIVE)
    rank = models.PositiveSmallIntegerField(default=0, help_text="Position by strength that day.")

    close = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    strength = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    rsi2 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    ret63 = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    ret252 = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    ret5 = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    turnover_cr = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    status = models.CharField(max_length=8, choices=SwingStatus.choices, default=SwingStatus.WATCH)
    entry_day = models.DateField(null=True, blank=True)
    entry_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    stop_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    sessions_held = models.PositiveSmallIntegerField(default=0)
    marked_on = models.DateField(null=True, blank=True, help_text="Last session this trade was marked.")
    last_close = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    exit_day = models.DateField(null=True, blank=True)
    exit_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    exit_reason = models.CharField(max_length=12, blank=True)
    net_return = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True,
                                     help_text="Exit over entry, less the 0.30% round trip.")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-day", "rank", "symbol"]
        constraints = [
            models.UniqueConstraint(fields=["day", "symbol", "source"], name="unique_swing_signal")
        ]
        indexes = [
            models.Index(fields=["source", "status"], name="idx_swing_source_status"),
            models.Index(fields=["source", "-day"], name="idx_swing_source_day"),
        ]

    def __str__(self):
        return "{} {} {} ({})".format(self.day, self.symbol, self.status, self.source)


class SwingOptionTrade(models.Model):
    """The call-option version of a strength-dip trade the stock book took.

    One per taken signal: the front-month-or-next call nearest the money with at
    least 15 sessions left, bought at the next open and sold on the close of the
    tenth session (or three sessions before expiry). No stop -- the premium is
    the stop, and every early-exit rule tested lowered the return
    (research/STRENGTH_DIP_OPTIONS_REPORT.md). Marked once each evening from the
    Dhan quote, so it only exists for the live book.
    """

    signal = models.OneToOneField(SwingSignal, on_delete=models.CASCADE, related_name="option_trade")
    symbol = models.CharField(max_length=40)
    security_id = models.CharField(max_length=20, blank=True)
    contract = models.CharField(max_length=60, blank=True)
    expiry = models.DateField(null=True, blank=True)
    strike = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    lot_size = models.PositiveIntegerField(default=0)

    status = models.CharField(max_length=8, choices=SwingStatus.choices, default=SwingStatus.PENDING)
    signal_premium = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                         help_text="Last traded premium on the signal evening.")
    signal_spread_pct = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    entry_day = models.DateField(null=True, blank=True)
    entry_premium = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    last_premium = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    marked_on = models.DateField(null=True, blank=True)
    sessions_held = models.PositiveSmallIntegerField(default=0)
    exit_day = models.DateField(null=True, blank=True)
    exit_premium = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    exit_reason = models.CharField(max_length=12, blank=True)
    net_return = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True,
                                     help_text="Exit over entry premium, less a 7% round trip.")
    note = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-signal__day", "symbol"]
        indexes = [models.Index(fields=["status"], name="idx_swing_opt_status")]

    def __str__(self):
        return "{} {} ({})".format(self.contract or self.symbol, self.status, self.signal.day)
class CasAuctionEvent(models.Model):
    """Durable CAS packets and forecasts used for replay and model tuning."""

    class EventType(models.TextChoices):
        INSTRUMENT = "INSTRUMENT", "Instrument packet"
        INDEX = "INDEX", "Index packet"
        NOWCAST = "NOWCAST", "Nowcast"
        ERROR = "ERROR", "Collector error"

    session_date = models.DateField()
    observed_at = models.DateTimeField()
    event_type = models.CharField(max_length=12, choices=EventType.choices)
    security_id = models.CharField(max_length=20, blank=True)
    index_name = models.CharField(max_length=40, blank=True)
    payload_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-observed_at", "-id"]
        indexes = [
            models.Index(
                fields=["session_date", "event_type", "observed_at"],
                name="idx_cas_session_type",
            ),
            models.Index(
                fields=["security_id", "observed_at"],
                name="idx_cas_security_time",
            ),
        ]
