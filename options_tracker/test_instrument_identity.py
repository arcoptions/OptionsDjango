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

    def test_reconciliation_processes_bounded_batches_with_resume_cursor(self):
        rows = [
            RadarInstrument.objects.create(
                security_id=str(index),
                segment="NSE_EQ",
                symbol=f"TEST{index}",
                kind="EQUITY",
            )
            for index in range(5)
        ]

        first_output = StringIO()
        call_command(
            "reconcile_instruments",
            "--apply",
            "--limit=2",
            "--batches=2",
            stdout=first_output,
        )
        first_report = json.loads(first_output.getvalue())

        self.assertEqual(first_report["completed_batches"], 2)
        self.assertEqual(first_report["applied"], 4)
        self.assertEqual(first_report["remaining"], 1)
        self.assertEqual(first_report["next_after_pk"], rows[3].pk)

        second_output = StringIO()
        call_command(
            "reconcile_instruments",
            "--apply",
            "--limit=2",
            f"--after-pk={first_report['next_after_pk']}",
            stdout=second_output,
        )
        second_report = json.loads(second_output.getvalue())

        self.assertEqual(second_report["applied"], 1)
        self.assertEqual(second_report["remaining"], 0)
        self.assertIsNone(second_report["next_after_pk"])

    def test_option_reconciliation_uses_contract_specific_radar_aliases(self):
        for security_id, strike, side in [
            ("101", 100, "CE"),
            ("102", 100, "PE"),
            ("103", 110, "CE"),
        ]:
            RadarInstrument.objects.create(
                security_id=security_id,
                segment="NSE_FNO",
                symbol="TEST",
                kind="OPTION",
                expiry="2026-11-26",
                strike=strike,
                side=side,
                lot_size=50,
            )

        output = StringIO()
        call_command(
            "reconcile_instruments",
            "--apply",
            "--kind=OPTION",
            stdout=output,
        )
        report = json.loads(output.getvalue())

        self.assertEqual(report["applied"], 3)
        self.assertEqual(report["errors"], [])
        self.assertEqual(CanonicalInstrument.objects.count(), 3)
        self.assertEqual(
            InstrumentAlias.objects.filter(provider="RADAR").count(),
            3,
        )
