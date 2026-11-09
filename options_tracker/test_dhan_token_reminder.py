from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase

from .dhan_token_reminder import SENT_DATE_KEY, reminder_state, send_reminder
from .models import AppSetting


IST = ZoneInfo("Asia/Kolkata")


class DhanTokenReminderTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 10, 9, 0, tzinfo=IST)
        self.production = patch.dict(
            "os.environ",
            {"ARC_DEPLOYMENT_ENVIRONMENT": "production"},
            clear=False,
        )
        self.production.start()
        self.addCleanup(self.production.stop)

    @patch("options_tracker.dhan_token_reminder.telegram_bot.send")
    def test_non_production_and_quiet_hours_never_send(self, send):
        with patch.dict(
            "os.environ",
            {"ARC_DEPLOYMENT_ENVIRONMENT": "staging"},
            clear=False,
        ):
            self.assertEqual(
                send_reminder(now=self.now)["state"],
                "SKIPPED_NON_PRODUCTION",
            )
        quiet = self.now.replace(hour=7)
        self.assertEqual(send_reminder(now=quiet)["state"], "QUIET_HOURS")
        send.assert_not_called()

    @patch("options_tracker.dhan_token_reminder._token_is_spent", return_value=False)
    def test_current_ui_token_needs_no_reminder(self, token_is_spent):
        setting = AppSetting.objects.create(key="dhan_access_token", value="secret-token")
        AppSetting.objects.filter(pk=setting.pk).update(updated_at=self.now)

        result = reminder_state(now=self.now)

        self.assertEqual(result["state"], "CURRENT")
        token_is_spent.assert_called_once_with("secret-token")

    @patch("options_tracker.dhan_token_reminder.telegram_bot.send", return_value=(True, None))
    def test_missing_token_sends_once_without_token_value(self, send):
        first = send_reminder(now=self.now)
        second = send_reminder(now=self.now)

        self.assertEqual(first["state"], "SENT")
        self.assertEqual(second["state"], "ALREADY_SENT")
        self.assertEqual(send.call_count, 1)
        self.assertIn("Add and validate today&#39;s Dhan token", send.call_args.args[0].replace("'", "&#39;"))
        self.assertNotIn("secret", send.call_args.args[0].lower())
        self.assertEqual(
            AppSetting.objects.get(key=SENT_DATE_KEY).value,
            self.now.date().isoformat(),
        )

    @patch("options_tracker.dhan_token_reminder.telegram_bot.send", return_value=(False, "offline"))
    def test_failed_delivery_is_not_deduplicated(self, send):
        result = send_reminder(now=self.now)

        self.assertEqual(result["state"], "DELIVERY_FAILED")
        self.assertFalse(AppSetting.objects.filter(key=SENT_DATE_KEY).exists())

    def test_supervisor_enables_reminder_only_with_production_delivery_config(self):
        from .management.commands.radar_supervise import worker_specs

        with patch.dict(
            "os.environ",
            {
                "ARC_DEPLOYMENT_ENVIRONMENT": "production",
                "TELEGRAM_BOT_TOKEN": "bot-secret",
                "TELEGRAM_ALERT_CHAT_ID": "123",
            },
            clear=True,
        ):
            self.assertEqual(
                worker_specs()["token-reminder"],
                ["remind_dhan_token", "--apply", "--loop"],
            )
        with patch.dict(
            "os.environ",
            {"ARC_DEPLOYMENT_ENVIRONMENT": "staging"},
            clear=True,
        ):
            self.assertNotIn("token-reminder", worker_specs())
