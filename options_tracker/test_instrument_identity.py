import json
from io import StringIO

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from .instrument_identity import register_instrument, resolve_instrument
from .models import CanonicalInstrument, InstrumentAlias
from .radar_models import RadarInstrument


class InstrumentIdentityTests(TestCase):
    def test_security_identity_and_provider_alias_resolve_same_instrument(self):
        instrument = register_instrument(
            canonical_symbol="RELIANCE",
            kind="equity",
            exchange_segment="NSE_EQ",
            security_id="2885",
            aliases=(("Dhan", "RELIANCE"),),
        )

        self.assertEqual(
            resolve_instrument(exchange_segment="nse_eq", security_id="2885"),
            instrument,
        )
        self.assertEqual(
            resolve_instrument(provider="dhan", alias="reliance", exchange_segment="nse_eq"),
            instrument,
        )

    def test_alias_identity_cannot_silently_point_to_two_instruments(self):
        first = register_instrument(
            canonical_symbol="FIRST",
            kind="equity",
            exchange_segment="NSE_EQ",
            security_id="1",
            aliases=(("TEST", "SAME"),),
        )
        second = register_instrument(
            canonical_symbol="SECOND",
            kind="equity",
            exchange_segment="NSE_EQ",
            security_id="2",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            InstrumentAlias.objects.create(
                instrument=second,
                provider="TEST",
                value="SAME",
                exchange_segment="NSE_EQ",
            )
        self.assertEqual(InstrumentAlias.objects.get().instrument, first)

    def test_reconciliation_is_dry_run_by_default_and_idempotent_when_applied(self):
        radar = RadarInstrument.objects.create(
            security_id="123",
            segment="NSE_EQ",
            symbol="TEST",
            name="Test Limited",
            kind="EQUITY",
        )

        dry_output = StringIO()
        call_command("reconcile_instruments", stdout=dry_output)
        radar.refresh_from_db()
        self.assertIsNone(radar.canonical_instrument_id)
        self.assertEqual(CanonicalInstrument.objects.count(), 0)
        self.assertEqual(json.loads(dry_output.getvalue())["mode"], "dry-run")

        call_command("reconcile_instruments", "--apply", stdout=StringIO())
        call_command("reconcile_instruments", "--apply", stdout=StringIO())
        radar.refresh_from_db()

        self.assertIsNotNone(radar.canonical_instrument_id)
        self.assertEqual(CanonicalInstrument.objects.count(), 1)

    def test_reconciliation_respects_batch_limit(self):
        for index in range(3):
            RadarInstrument.objects.create(
                security_id=str(index),
                segment="NSE_EQ",
                symbol=f"TEST{index}",
                kind="EQUITY",
            )

        output = StringIO()
        call_command("reconcile_instruments", "--apply", "--limit=2", stdout=output)
        report = json.loads(output.getvalue())

        self.assertEqual(report["applied"], 2)
        self.assertEqual(report["remaining"], 1)
        self.assertEqual(CanonicalInstrument.objects.count(), 2)
