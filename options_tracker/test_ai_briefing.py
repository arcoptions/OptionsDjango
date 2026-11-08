import os
from io import StringIO
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .ai_briefing import generate_briefing
from .models import AIBriefingSnapshot, DhanOrderEvent


class AIBriefingTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="briefing-operator",
            is_staff=True,
        )

    def test_dry_run_retains_prompt_and_evidence_without_provider_call(self):
        result = generate_briefing(now=timezone.now())

        self.assertEqual(result["prompt_version"], "ARC-BRIEFING-1")
        self.assertIn("Do not invent", result["prompt"])
        self.assertIn("sources", result["evidence"])
        self.assertFalse(AIBriefingSnapshot.objects.exists())

    def test_briefing_daemon_requires_apply_and_valid_schedule(self):
        with self.assertRaises(CommandError):
            call_command(
                "generate_arc_briefing",
                daemon=True,
                stdout=StringIO(),
            )
        with self.assertRaises(CommandError):
            call_command(
                "generate_arc_briefing",
                hour=24,
                stdout=StringIO(),
            )

    @patch("options_tracker.ai_briefing.requests.post")
    def test_apply_stores_traceable_provider_response_without_broker_action(self, post):
        response = Mock()
        response.json.return_value = {
            "choices": [{"message": {"content": "Evidence is insufficient."}}],
        }
        response.raise_for_status.return_value = None
        post.return_value = response
        environment = {
            "AZURE_OPENAI_ENDPOINT": "https://approved.openai.azure.com",
            "AZURE_OPENAI_API_KEY": "test-key",
            "AZURE_OPENAI_DEPLOYMENT": "briefing-model",
        }

        with patch.dict(os.environ, environment):
            snapshot = generate_briefing(now=timezone.now(), apply=True)

        self.assertEqual(snapshot.response_text, "Evidence is insufficient.")
        self.assertEqual(snapshot.provider, "AZURE_OPENAI")
        self.assertTrue(snapshot.prompt_hash)
        self.assertTrue(snapshot.evidence_hash)
        self.assertEqual(DhanOrderEvent.objects.count(), 0)
        self.assertNotIn("test-key", post.call_args.args[0])

        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("home")), "Evidence is insufficient.")
