"""Canonical instrument registration and provider-alias resolution."""

from django.db import transaction

from .models import CanonicalInstrument, InstrumentAlias, InstrumentKind


def canonical_kind(kind):
    normalized = str(kind or "").strip().upper()
    if normalized in InstrumentKind.values:
        return normalized
    if normalized in {"EQ", "STOCK"}:
        return InstrumentKind.EQUITY
    if normalized in {"CE", "PE", "CALL", "PUT"}:
        return InstrumentKind.OPTION
    raise ValueError(f"Unsupported instrument kind: {kind!r}")


def resolve_instrument(*, provider="", alias="", exchange_segment="", security_id=""):
    segment = str(exchange_segment or "").strip().upper()
    identifier = str(security_id or "").strip()
    if segment and identifier:
        match = CanonicalInstrument.objects.filter(
            exchange_segment=segment,
            security_id=identifier,
        ).first()
        if match:
            return match
    provider_name = str(provider or "").strip().upper()
    alias_value = str(alias or "").strip().upper()
    if not provider_name or not alias_value:
        return None
    alias_row = (
        InstrumentAlias.objects.select_related("instrument")
        .filter(
            provider=provider_name,
            value=alias_value,
            exchange_segment=segment,
        )
        .first()
    )
    return alias_row.instrument if alias_row else None


@transaction.atomic
def register_instrument(
    *,
    canonical_symbol,
    kind,
    exchange_segment,
    security_id,
    display_name="",
    underlying_symbol="",
    expiry_date=None,
    strike=None,
    option_type="",
    lot_size=1,
    aliases=(),
):
    segment = str(exchange_segment or "").strip().upper()
    identifier = str(security_id or "").strip()
    if not segment or not identifier:
        raise ValueError("exchange_segment and security_id are required")
    symbol = str(canonical_symbol or "").strip().upper()
    if not symbol:
        raise ValueError("canonical_symbol is required")
    instrument, _ = CanonicalInstrument.objects.update_or_create(
        exchange_segment=segment,
        security_id=identifier,
        defaults={
            "canonical_symbol": symbol,
            "display_name": str(display_name or "").strip(),
            "kind": canonical_kind(kind),
            "underlying_symbol": str(underlying_symbol or "").strip().upper(),
            "expiry_date": expiry_date,
            "strike": strike,
            "option_type": str(option_type or "").strip().upper(),
            "lot_size": max(1, int(lot_size or 1)),
            "active": True,
        },
    )
    for provider, value in aliases:
        alias_row, created = InstrumentAlias.objects.get_or_create(
            provider=str(provider or "").strip().upper(),
            value=str(value or "").strip().upper(),
            exchange_segment=segment,
            defaults={"instrument": instrument},
        )
        if not created and alias_row.instrument_id != instrument.pk:
            raise ValueError(
                f"Alias {alias_row.provider}:{alias_row.value} already maps to "
                f"instrument {alias_row.instrument_id}"
            )
    return instrument
