import os
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import AIResearchSnapshot, DhanOrderEvent, TradeExecution


class AIResearchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "research-operator",
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.environment = patch.dict(
            os.environ,
            {
                "AZURE_OPENAI_ENDPOINT": "https://approved.openai.azure.com",
                "AZURE_OPENAI_API_KEY": "test-key",
                "AZURE_OPENAI_DEPLOYMENT": "approved-model",
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @patch("options_tracker.azure_openai.requests.post")
    def test_staff_can_store_traceable_research_without_broker_activity(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "choices": [{"message": {"content": "Evidence is limited [S1]."}}],
        }
        post.return_value = response

        result = self.client.post(
            reverse("research_assistant"),
            {"question": "What could invalidate this regime?", "symbol": "nifty"},
        )

        self.assertEqual(result.status_code, 200)
        snapshot = AIResearchSnapshot.objects.get()
        self.assertEqual(snapshot.symbol, "NIFTY")
        self.assertEqual(snapshot.user, self.user)
        self.assertIn("Do not give automatic execution", snapshot.prompt)
        self.assertTrue(snapshot.prompt_hash)
        self.assertTrue(snapshot.evidence_hash)
        self.assertFalse(DhanOrderEvent.objects.exists())
        self.assertFalse(TradeExecution.objects.exists())
        self.assertNotIn("test-key", post.call_args.args[0])

    def test_assistant_offers_provider_neutral_evidence_questions(self):
        result = self.client.get(reverse("research_assistant"))

        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Suggested evidence questions")
        self.assertContains(result, "Where is ARC&#x27;s current evidence missing")
        self.assertContains(result, "READY")

    def test_missing_provider_configuration_is_explicit(self):
        with patch.dict(
            os.environ,
            {
                "AZURE_OPENAI_ENDPOINT": "",
                "AZURE_OPENAI_API_KEY": "",
                "AZURE_OPENAI_DEPLOYMENT": "",
            },
        ):
            unavailable = self.client.get(reverse("research_assistant"))
            result = self.client.post(
                reverse("research_assistant"),
                {"question": "Summarize risk."},
            )
        self.assertContains(unavailable, "UNAVAILABLE")
        self.assertContains(unavailable, "will not generate or save a substitute response")
        self.assertContains(unavailable, "disabled")
        self.assertNotContains(unavailable, "test-key")
        self.assertEqual(result.status_code, 400)
        self.assertContains(result, "Azure OpenAI requires", status_code=400)
        self.assertFalse(AIResearchSnapshot.objects.exists())

    def test_non_staff_user_cannot_access_assistant(self):
        self.client.force_login(
            get_user_model().objects.create_user("research-viewer"),
        )
        result = self.client.get(reverse("research_assistant"))
        self.assertEqual(result.status_code, 302)
