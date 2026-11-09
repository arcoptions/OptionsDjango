from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .action_workspace import action_list_context, breakout_action_row
from .radar_models import RadarInstrument, RadarOpportunity


class BreakoutIntelligenceTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.instrument = RadarInstrument.objects.create(
            security_id="breakout-1",
            segment="NSE_EQ",
            symbol="BREAKOUT",
            kind="EQUITY",
        )

    def _opportunity(self, **overrides):
        values = {
            "key": f"breakout-{RadarOpportunity.objects.count()}",
            "symbol": self.instrument.symbol,
            "strategy": "S2",
            "version": "experimental-v3",
            "side": "CE",
            "state": RadarOpportunity.State.TRIGGERED,
            "setup_at": self.now - timedelta(minutes=2),
            "trigger_at": self.now - timedelta(minutes=1),
            "valid_until": self.now + timedelta(minutes=1),
            "trigger": 102,
            "stop": 100,
            "atr": 1,
            "reason": "Six-bar compression confirmed above its range.",
        }
        values.update(overrides)
        return RadarOpportunity.objects.create(**values)

    def test_breakout_row_has_transparent_taxonomy_rank_and_two_r_target(self):
        row = breakout_action_row(
            self._opportunity(),
            {self.instrument.symbol},
        )

        self.assertEqual(row["breakout_classification"], "COMPRESSION_BREAKOUT")
        self.assertEqual(row["score"], 80)
        self.assertEqual(row["score_reason"], "70 taxonomy points + 10 state points")
        self.assertEqual(row["target"], 106)
        self.assertEqual(row["action"], "TRIGGERED")
        self.assertFalse(row["is_buy"])

    def test_action_list_includes_only_current_breakout_setups(self):
        current = self._opportunity()
        self._opportunity(
            key="expired",
            valid_until=self.now - timedelta(seconds=1),
        )

        context = action_list_context()

        self.assertEqual(context["breakout_count"], 1)
        self.assertEqual(context["rows"][0]["id"], current.pk)

    def test_put_target_is_two_r_below_trigger(self):
        row = breakout_action_row(
            self._opportunity(side="PE", trigger=100, stop=103),
            {self.instrument.symbol},
        )

        self.assertEqual(row["target"], 94)
