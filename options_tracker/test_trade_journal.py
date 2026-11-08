import csv
import io

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Direction, TipSignal, TradeExecution, TradeState


class TradeJournalTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="journal-operator",
            is_staff=True,
        )
        self.signal = TipSignal.objects.create(
            option_symbol="NIFTY TEST CE",
            source_name="Approved desk",
            direction=Direction.CE,
            stop_loss=90,
            reason_tags="breakout",
        )
        self.trade = TradeExecution.objects.create(
            signal=self.signal,
            quantity=50,
            entry_price=100,
            stop_loss=90,
            target_1=120,
            state=TradeState.CLOSED,
            journal_reason="=FORMULA() should remain inert in spreadsheet exports",
        )
        self.client.force_login(self.staff)

    def test_journal_filters_across_evidence_fields(self):
        url = reverse("trade_journal")

        response = self.client.get(url, {"q": "Approved desk", "state": "CLOSED"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "NIFTY TEST CE")
        self.assertContains(response, "Export filtered CSV")
        self.assertEqual(
            self.client.get(url, {"state": "INVALID"}).status_code,
            400,
        )

    def test_journal_exports_filtered_formula_safe_csv(self):
        response = self.client.get(reverse("trade_journal"), {
            "q": "breakout",
            "state": "CLOSED",
            "export": "csv",
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8"))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "NIFTY TEST CE")
        self.assertTrue(rows[0]["journal_reason"].startswith("'="))
