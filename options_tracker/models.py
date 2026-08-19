from django.db import models
from django.utils import timezone


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


class TipSignal(models.Model):
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
