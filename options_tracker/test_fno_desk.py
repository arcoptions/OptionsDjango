from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import IndexOISnapshot


class FnoDeskTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="fno-researcher",
            password="test-password",
        )

    def test_desk_requires_login_and_renders_index_evidence(self):
        IndexOISnapshot.objects.create(
            underlying="NIFTY",
            underlying_price=25000,
            pcr=1.1,
            support_strike=24900,
            resistance_strike=25100,
            regime="BALANCED",
        )
        url = reverse("fno_desk")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.user)

        response = self.client.get(url)

        self.assertContains(response, "F&amp;O research desk", html=True)
        self.assertContains(response, "25000")
        self.assertContains(response, "manual execution boundary")
