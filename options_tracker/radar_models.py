"""Persistent, paper-only stock radar records."""

from django.db import models
from django.conf import settings
from django.utils import timezone


class RadarInstrument(models.Model):
    canonical_instrument = models.ForeignKey(
        "options_tracker.CanonicalInstrument",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="radar_instruments",
    )
    security_id = models.CharField(max_length=40)
    segment = models.CharField(max_length=12)
    symbol = models.CharField(max_length=40, db_index=True)
    name = models.CharField(max_length=160, blank=True)
    kind = models.CharField(max_length=8, choices=[("EQUITY", "Equity"), ("OPTION", "Option")])
    expiry = models.DateField(null=True, blank=True)
    strike = models.FloatField(null=True, blank=True)
    side = models.CharField(max_length=2, blank=True)
    lot_size = models.PositiveIntegerField(default=1)
    active = models.BooleanField(default=True)
    refreshed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["segment", "security_id"], name="radar_instrument_identity"),
        ]


class RadarBar(models.Model):
    instrument = models.ForeignKey(RadarInstrument, on_delete=models.PROTECT)
    timestamp = models.DateTimeField()
    open = models.FloatField()
    high = models.FloatField()
    low = models.FloatField()
    close = models.FloatField()
    volume = models.PositiveBigIntegerField()
    complete = models.BooleanField(default=False)
    source = models.CharField(max_length=24, default="DHAN_STREAM")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["instrument", "timestamp"], name="radar_bar_identity"),
        ]
        indexes = [
            models.Index(fields=["-timestamp"], name="radar_bar_time"),
        ]
        ordering = ["timestamp"]


class RadarQuote(models.Model):
    instrument = models.OneToOneField(RadarInstrument, on_delete=models.PROTECT)
    source_at = models.DateTimeField()
    received_at = models.DateTimeField()
    last = models.FloatField()
    bid = models.FloatField(default=0)
    ask = models.FloatField(default=0)
    bid_qty = models.PositiveBigIntegerField(default=0)
    ask_qty = models.PositiveBigIntegerField(default=0)
    volume = models.PositiveBigIntegerField(default=0)
    previous_last = models.FloatField(null=True)
    previous_volume = models.PositiveBigIntegerField(null=True)
    previous_at = models.DateTimeField(null=True)
    oi = models.PositiveBigIntegerField(null=True)
    day_open = models.FloatField(null=True)
    day_high = models.FloatField(null=True)
    day_low = models.FloatField(null=True)
    previous_close = models.FloatField(null=True)


class RadarOptionPoint(models.Model):
    instrument = models.ForeignKey(RadarInstrument, on_delete=models.PROTECT)
    timestamp = models.DateTimeField()
    received_at = models.DateTimeField()
    last = models.FloatField()
    bid = models.FloatField()
    ask = models.FloatField()
    volume = models.PositiveBigIntegerField()
    oi = models.PositiveBigIntegerField(null=True)
    bid_qty = models.PositiveBigIntegerField(null=True)
    ask_qty = models.PositiveBigIntegerField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["instrument", "timestamp"], name="radar_option_point_identity"),
        ]
        ordering = ["timestamp"]


class RadarWatch(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    instrument = models.ForeignKey(RadarInstrument, on_delete=models.PROTECT)
    notes = models.TextField(blank=True, max_length=2000)
    watched_level = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "instrument"], name="radar_personal_watch"),
        ]


class RadarWorkspace(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    preferences = models.JSONField(default=dict)


class RadarNotificationRoute(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    telegram_chat_id = models.CharField(max_length=40)


class RadarPriceAlert(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    instrument = models.ForeignKey(RadarInstrument, on_delete=models.PROTECT)
    direction = models.CharField(max_length=5, choices=[("ABOVE", "Cross above"), ("BELOW", "Cross below")])
    level = models.FloatField()
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    previous_price = models.FloatField(null=True)
    previous_at = models.DateTimeField(null=True)
    checked_at = models.DateTimeField(null=True)
    triggered_at = models.DateTimeField(null=True)
    trigger_price = models.FloatField(null=True)
    sent_at = models.DateTimeField(null=True)
    cancelled_at = models.DateTimeField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["sent_at", "cancelled_at", "expires_at"], name="radar_price_pending")]


class RadarSector(models.Model):
    instrument = models.OneToOneField(RadarInstrument, on_delete=models.PROTECT)
    industry = models.CharField(max_length=100)
    source_url = models.URLField()
    verified_at = models.DateTimeField(default=timezone.now)


class MarketBreadthSnapshot(models.Model):
    universe = models.CharField(max_length=32)
    version = models.CharField(max_length=32)
    input_hash = models.CharField(max_length=64)
    source_at = models.DateTimeField()
    received_at = models.DateTimeField(default=timezone.now)
    active_instruments = models.PositiveIntegerField()
    covered_instruments = models.PositiveIntegerField()
    metrics = models.JSONField(default=dict)
    unavailable = models.JSONField(default=list)

    class Meta:
        ordering = ["-source_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["universe", "version", "input_hash"],
                name="market_breadth_snapshot_identity",
            ),
        ]
        indexes = [
            models.Index(
                fields=["universe", "version", "-source_at"],
                name="idx_market_breadth_latest",
            ),
        ]


class RadarOpportunity(models.Model):
    class State(models.TextChoices):
        ARMED = "ARMED", "Armed"
        TRIGGERED = "TRIGGERED", "Triggered"
        ELIGIBLE = "ELIGIBLE", "Paper entry eligible"
        REJECTED = "REJECTED", "Rejected"
        TAKEN = "TAKEN", "Paper position open"
        SKIPPED = "SKIPPED", "Skipped"
        EXPIRED = "EXPIRED", "Expired"
        INVALIDATED = "INVALIDATED", "Invalidated"
        EXIT_PENDING = "EXIT_PENDING", "Exit pending fresh quote"
        EXITED = "EXITED", "Paper position closed"

    key = models.CharField(max_length=220, unique=True)
    symbol = models.CharField(max_length=40, db_index=True)
    strategy = models.CharField(max_length=4)
    version = models.CharField(max_length=32)
    side = models.CharField(max_length=2)
    state = models.CharField(max_length=16, choices=State.choices)
    setup_at = models.DateTimeField()
    trigger_at = models.DateTimeField(null=True)
    valid_until = models.DateTimeField()
    trigger = models.FloatField()
    stop = models.FloatField()
    atr = models.FloatField()
    reason = models.TextField()
    evidence = models.JSONField(default=dict)
    policy = models.JSONField(default=dict)
    contract = models.ForeignKey(RadarInstrument, on_delete=models.PROTECT, null=True)
    entry_limit = models.FloatField(null=True)
    quantity = models.PositiveIntegerField(default=0)
    entry_price = models.FloatField(null=True)
    opened_at = models.DateTimeField(null=True)
    high_water = models.FloatField(null=True)
    exit_price = models.FloatField(null=True)
    closed_at = models.DateTimeField(null=True)
    exit_reason = models.CharField(max_length=120, blank=True)
    charges = models.FloatField(null=True)
    net_pnl = models.FloatField(null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class RadarAlert(models.Model):
    opportunity = models.ForeignKey(RadarOpportunity, on_delete=models.PROTECT, related_name="alerts")
    state = models.CharField(max_length=16)
    text = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["opportunity", "state"], name="radar_alert_transition"),
        ]


class RadarDecision(models.Model):
    opportunity = models.ForeignKey(RadarOpportunity, on_delete=models.PROTECT, related_name="decisions")
    state = models.CharField(max_length=16)
    reason = models.TextField()
    context = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-pk"]


class RadarWorker(models.Model):
    name = models.CharField(max_length=30, unique=True)
    owner = models.CharField(max_length=40, blank=True)
    lease_until = models.DateTimeField(default=timezone.now)
    heartbeat_at = models.DateTimeField(default=timezone.now)
    state = models.CharField(max_length=24, default="STOPPED")
    detail = models.TextField(blank=True)
    subscriptions = models.PositiveIntegerField(default=0)


class RadarAssistantSnapshot(models.Model):
    timestamp = models.DateTimeField(unique=True)
    expires_at = models.DateTimeField()
    payload = models.JSONField(default=dict)

    class Meta:
        ordering = ["-timestamp"]


class RadarResearchProtocol(models.Model):
    name = models.CharField(max_length=60, unique=True)
    registered_at = models.DateTimeField()
    starts_at = models.DateTimeField()
    fingerprint = models.CharField(max_length=64)
    specification = models.JSONField(default=dict)


class RadarResearchObservation(models.Model):
    opportunity = models.OneToOneField(RadarOpportunity, on_delete=models.PROTECT, related_name="research")
    protocol = models.ForeignKey(RadarResearchProtocol, on_delete=models.PROTECT)
    captured_at = models.DateTimeField()
    reference = models.JSONField(default=dict)
    outcomes = models.JSONField(default=dict)


class RadarCatalyst(models.Model):
    symbol = models.CharField(max_length=40)
    side = models.CharField(max_length=2, choices=[("CE", "Bullish"), ("PE", "Bearish")])
    summary = models.CharField(max_length=500)
    source_url = models.URLField()
    published_at = models.DateTimeField()
    verified_at = models.DateTimeField()
    expires_at = models.DateTimeField()

    def clean(self):
        from django.core.exceptions import ValidationError
        self.symbol = self.symbol.strip().upper()
        if not RadarInstrument.objects.filter(symbol=self.symbol, kind="EQUITY", active=True).exists():
            raise ValidationError("Catalyst requires an active F&O stock symbol.")
        if self.published_at and self.verified_at and self.expires_at and not self.published_at <= self.verified_at < self.expires_at:
            raise ValidationError("Require publication <= verification < expiry.")


class RadarHistoryConflict(models.Model):
    bar = models.ForeignKey(RadarBar, on_delete=models.PROTECT)
    source = models.CharField(max_length=24)
    fingerprint = models.CharField(max_length=64)
    proposed = models.JSONField(default=dict)
    original = models.JSONField(default=dict)
    observed_at = models.DateTimeField(default=timezone.now)
    accepted_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["bar", "fingerprint"], name="radar_conflict_identity"),
        ]
