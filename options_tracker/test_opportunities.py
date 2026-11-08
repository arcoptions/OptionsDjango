import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import Direction, OpportunityRevision, OpportunityStatus, OpportunityStatusEvent, TipSignal, UnifiedOpportunity
from .opportunities import transition_opportunity, upsert_opportunity


class UnifiedOpportunityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="opportunity-operator",
            password="test-password",
            is_staff=True,
        )

    def test_same_source_setup_deduplicates_but_new_evidence_creates_revision(self):
        first = upsert_opportunity(
            source_type="scanner",
            source_ref="scan-1",
            strategy="breakout",
            direction="long",
            timeframe="15m",
            evidence={"price": 100},
        )
        second = upsert_opportunity(
            source_type="scanner",
            source_ref="scan-1",
            strategy="breakout",
            direction="long",
            timeframe="15m",
            evidence={"price": 102},
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(OpportunityRevision.objects.filter(opportunity=first).count(), 2)

    def test_independent_setups_from_same_source_remain_separate(self):
        breakout = upsert_opportunity(
            source_type="scanner",
            source_ref="scan-1",
            strategy="breakout",
            evidence={"price": 100},
        )
        reversal = upsert_opportunity(
            source_type="scanner",
            source_ref="scan-1",
            strategy="reversal",
            evidence={"price": 100},
        )

        self.assertNotEqual(breakout.pk, reversal.pk)

    def test_lifecycle_rejects_skips_and_requires_actor_for_decisions(self):
        opportunity = upsert_opportunity(
            source_type="manual",
            source_ref="idea-1",
            strategy="swing",
            evidence={"note": "test"},
        )
        with self.assertRaises(ValueError):
            transition_opportunity(opportunity, OpportunityStatus.APPROVED, reason="skip")
        transition_opportunity(opportunity, OpportunityStatus.VALIDATED, reason="Evidence checked")
        transition_opportunity(opportunity, OpportunityStatus.PLANNED, reason="Risk plan added")
        with self.assertRaises(ValueError):
            transition_opportunity(opportunity, OpportunityStatus.APPROVED, reason="Approve")
        transition_opportunity(
            opportunity,
            OpportunityStatus.APPROVED,
            reason="Operator approved",
            actor=self.user,
        )

        self.assertEqual(opportunity.status, OpportunityStatus.APPROVED)
        self.assertEqual(OpportunityStatusEvent.objects.filter(opportunity=opportunity).count(), 4)

    def test_inbox_requires_login_and_displays_provenance(self):
        opportunity = upsert_opportunity(
            source_type="telegram",
            source_ref="chat-99",
            source_name="Verified feed",
            strategy="intraday",
            evidence={"message": "test"},
            next_action="Validate levels",
        )
        self.assertEqual(self.client.get(reverse("opportunity_inbox")).status_code, 302)
        self.client.force_login(self.user)

        response = self.client.get(reverse("opportunity_inbox"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, opportunity.source_ref)
        self.assertContains(response, "Validate levels")

    def test_derivative_plan_requires_complete_risk_and_cost_contract(self):
        opportunity = upsert_opportunity(
            source_type="manual",
            source_ref="option-plan-1",
            strategy="breakout",
            direction="CE",
            evidence={"price": 100},
            rationale="Breakout supported by stored price and OI evidence.",
        )
        transition_opportunity(
            opportunity,
            OpportunityStatus.VALIDATED,
            reason="Evidence checked",
        )
        with self.assertRaisesRegex(ValueError, "estimated_charges"):
            transition_opportunity(
                opportunity,
                OpportunityStatus.PLANNED,
                reason="Incomplete plan",
            )
        opportunity.plan = {
            "entry": 100,
            "stop_loss": 90,
            "targets": [120, 130, 140],
            "quantity": 50,
            "estimated_charges": 75,
            "risk_amount": 500,
            "invalidation": "Close below the validated breakout level.",
        }
        opportunity.save(update_fields=["plan", "updated_at"])

        transition_opportunity(
            opportunity,
            OpportunityStatus.PLANNED,
            reason="Complete risk plan recorded",
        )

        self.assertEqual(opportunity.status, OpportunityStatus.PLANNED)

    def test_legacy_sync_is_dry_run_by_default_and_idempotent_when_applied(self):
        TipSignal.objects.create(
            option_symbol="NIFTY 25000 CE",
            direction=Direction.CE,
            stop_loss=100,
            score=80,
        )
        output = StringIO()
        call_command("sync_opportunities", stdout=output)
        self.assertEqual(json.loads(output.getvalue())["candidates"]["tips"], 1)
        self.assertEqual(UnifiedOpportunity.objects.count(), 0)

        call_command("sync_opportunities", "--apply", stdout=StringIO())
        call_command("sync_opportunities", "--apply", stdout=StringIO())

        self.assertEqual(UnifiedOpportunity.objects.count(), 1)
