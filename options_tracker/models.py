from django.db import models
from django.utils import timezone


class TradeStyle(models.TextChoices):
    INTRADAY = "INTRADAY", "Intraday"
    SWING = "SWING", "Swing"


class Direction(models.TextChoices):
    CE = "CE", "CE"
    PE = "PE", "PE"


class SignalStatus(models.TextChoices):
    NEW = "NEW", "New"
    CANDIDATE = "CANDIDATE", "Candidate"
    ACTIVE = "ACTIVE", "Active"
    CLOSED = "CLOSED", "Closed"
    REJECTED = "REJECTED", "Rejected"
    ARCHIVED = "ARCHIVED", "Archived"


class TipSignal(models.Model):
    source_type = models.CharField(max_length=20, default="MANUAL")
    source_name = models.CharField(max_length=120, blank=True)
    source_ref = models.CharField(max_length=80, blank=True)
    raw_text = models.TextField(blank=True)
    option_symbol = models.CharField(max_length=80)
    security_id = models.CharField(max_length=40, blank=True)
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
    raw_text = models.TextField()
    normalized_text = models.TextField(blank=True)
    is_tip_candidate = models.BooleanField(default=False)
    linked_signal = models.ForeignKey(TipSignal, null=True, blank=True, on_delete=models.SET_NULL, related_name="chat_messages")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


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
    call_oi = models.BigIntegerField(default=0)
    put_oi = models.BigIntegerField(default=0)
    pcr = models.FloatField(default=0.0)
    regime = models.CharField(max_length=40, blank=True)
    interval_seconds = models.PositiveIntegerField(default=60)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class AppSetting(models.Model):
    key = models.CharField(max_length=60, unique=True)
    value = models.CharField(max_length=240)
    updated_at = models.DateTimeField(auto_now=True)


class DhanOrderEvent(models.Model):
    order_id = models.CharField(max_length=80, blank=True)
    correlation_id = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=30, blank=True)
    payload_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
