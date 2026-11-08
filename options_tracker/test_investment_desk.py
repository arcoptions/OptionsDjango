from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    DhanOrderEvent,
    InvestmentDividend,
    InvestmentHolding,
    InvestmentIdea,
)


class InvestmentDeskTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="investor",
            password="test-password",
            is_staff=True,
        )
        self.idea = InvestmentIdea.objects.create(
            owner=self.staff,
            symbol="TEST",
            thesis="Compounding earnings with improving returns on capital.",
            invalidation="Debt rises above the stated limit.",
            accumulation_low=Decimal("90"),
            accumulation_high=Decimal("100"),
            target_allocation_pct=Decimal("10"),
            review_on=timezone.localdate() - timedelta(days=1),
        )

    def test_idea_rejects_invalid_zone_and_allocation(self):
        self.idea.accumulation_low = Decimal("110")
        self.idea.target_allocation_pct = Decimal("101")
        with self.assertRaises(ValidationError):
            self.idea.full_clean()

    def test_desk_requires_staff_and_post_requires_csrf(self):
        url = reverse("investment_desk")
        self.assertEqual(self.client.get(url).status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        self.assertEqual(csrf_client.post(url, {"action": "create_idea"}).status_code, 403)
        self.client.force_login(self.staff)
        response = self.client.get(url)
        self.assertContains(response, "Long-term Investment Desk")
        self.assertContains(response, "Reviews due")

    def test_holding_allocation_and_dividend_render_without_broker_records(self):
        holding = InvestmentHolding.objects.create(
            idea=self.idea,
            quantity=Decimal("10"),
            average_cost=Decimal("100"),
            current_price=Decimal("120"),
            opened_on=timezone.localdate(),
        )
        InvestmentDividend.objects.create(
            holding=holding,
            ex_date=timezone.localdate(),
            amount_total=Decimal("50"),
            source="Broker statement",
        )
        self.client.force_login(self.staff)

        response = self.client.get(reverse("investment_desk"))

        self.assertContains(response, "1200.00")
        self.assertContains(response, "50.00")
        self.assertEqual(DhanOrderEvent.objects.count(), 0)

    def test_create_thesis_normalizes_symbol(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("investment_desk"), {
            "action": "create_idea",
            "idea-symbol": "second",
            "idea-thesis": "Evidence-backed long-term thesis.",
            "idea-invalidation": "Evidence deteriorates.",
            "idea-accumulation_low": "40",
            "idea-accumulation_high": "50",
            "idea-target_allocation_pct": "5",
            "idea-review_on": timezone.localdate() + timedelta(days=30),
            "idea-status": "WATCHING",
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(InvestmentIdea.objects.filter(symbol="SECOND").exists())

    def test_holding_and_dividend_mutations_use_separate_investment_records(self):
        self.client.force_login(self.staff)
        holding_response = self.client.post(reverse("investment_desk"), {
            "action": "save_holding",
            "holding-idea": self.idea.pk,
            "holding-quantity": "12",
            "holding-average_cost": "95",
            "holding-current_price": "105",
            "holding-opened_on": timezone.localdate(),
            "holding-closed_on": "",
            "holding-notes": "Long-term allocation",
        })
        holding = InvestmentHolding.objects.get(idea=self.idea)
        dividend_response = self.client.post(reverse("investment_desk"), {
            "action": "record_dividend",
            "dividend-holding": holding.pk,
            "dividend-ex_date": timezone.localdate(),
            "dividend-amount_total": "24",
            "dividend-source": "Broker statement",
            "dividend-received_on": "",
        })

        self.assertEqual(holding_response.status_code, 302)
        self.assertEqual(dividend_response.status_code, 302)
        self.idea.refresh_from_db()
        self.assertEqual(self.idea.status, "HOLDING")
        self.assertEqual(InvestmentDividend.objects.get().amount_total, Decimal("24"))
        self.assertEqual(DhanOrderEvent.objects.count(), 0)
