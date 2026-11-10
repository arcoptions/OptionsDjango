from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import DhanOrderEvent, OpportunityStatus, TradeExecution
from .opportunities import upsert_opportunity
from .radar_models import RadarInstrument, RadarPriceAlert


class PersonalDeskTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "personal-desk-operator",
            is_staff=True,
        )
        self.instrument = RadarInstrument.objects.create(
            security_id="personal-1",
            segment="NSE_EQ",
            symbol="VISIBLE",
            kind="EQUITY",
        )

    def test_staff_sees_manual_workflow_and_only_their_active_alerts(self):
        now = timezone.now()
        RadarPriceAlert.objects.create(
            user=self.user,
            instrument=self.instrument,
            direction="ABOVE",
            level=101,
            expires_at=now + timedelta(days=1),
        )
        other_user = get_user_model().objects.create_user("other-alert-user")
        other_instrument = RadarInstrument.objects.create(
            security_id="personal-2",
            segment="NSE_EQ",
            symbol="PRIVATE",
            kind="EQUITY",
        )
        RadarPriceAlert.objects.create(
            user=other_user,
            instrument=other_instrument,
            direction="BELOW",
            level=90,
            expires_at=now + timedelta(days=1),
        )
        opportunity = upsert_opportunity(
            source_type="manual",
            source_ref="personal-desk-plan",
            strategy="swing",
            evidence={"price": 100},
        )
        opportunity.status = OpportunityStatus.PLANNED
        opportunity.save(update_fields=["status", "updated_at"])
        self.client.force_login(self.user)

        response = self.client.get(reverse("personal_desk"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manual trading workflow")
        self.assertContains(response, "VISIBLE")
        self.assertNotContains(response, "PRIVATE")
        self.assertContains(response, "Reconciliation remains unavailable")
        self.assertContains(response, reverse("opportunity_inbox"))
        self.assertEqual(TradeExecution.objects.count(), 0)
        self.assertEqual(DhanOrderEvent.objects.count(), 0)

    def test_personal_desk_requires_staff_and_get(self):
        url = reverse("personal_desk")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(get_user_model().objects.create_user("desk-viewer"))
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(url).status_code, 405)
