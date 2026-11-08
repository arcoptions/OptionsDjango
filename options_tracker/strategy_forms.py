from django import forms

from .models import StrategyDefinition
from .strategy_lab import TEMPLATES


class StrategyDefinitionForm(forms.Form):
    name = forms.CharField(max_length=100)
    template = forms.ChoiceField(choices=[(name, name) for name in TEMPLATES])
    symbol = forms.CharField(max_length=40)
    interval_minutes = forms.TypedChoiceField(
        choices=[(value, value) for value in (1, 5, 15, 30, 60, 1440)],
        coerce=int,
    )
    parameters = forms.JSONField()
    cost_model = forms.JSONField(initial={
        "brokerage_per_order": 20,
        "tax_bps": 5,
        "slippage_bps": 5,
        "spread_bps": 5,
    })


class StrategyManifestForm(forms.Form):
    definition = forms.ModelChoiceField(queryset=StrategyDefinition.objects.none())
    data_start = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    data_end = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["definition"].queryset = StrategyDefinition.objects.filter(
            owner=owner,
            active=True,
        )
