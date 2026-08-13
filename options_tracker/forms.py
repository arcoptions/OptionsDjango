from django import forms

from .models import ChartinkTrigger, SignalStatus, TipSignal, TradeExecution, TradeStyle
from .telegram_sources import SOURCES


class TipSignalForm(forms.ModelForm):
    class Meta:
        model = TipSignal
        fields = [
            "source_name",
            "source_ref",
            "raw_text",
            "option_symbol",
            "security_id",
            "direction",
            "trade_style",
            "entry_price",
            "stop_loss",
            "target_1",
            "target_2",
            "target_3",
            "expiry_date",
        ]


class TrackedOptionEditForm(forms.ModelForm):
    class Meta:
        model = TipSignal
        fields = [
            "source_name",
            "option_symbol",
            "entry_price",
            "stop_loss",
            "target_1",
            "target_2",
            "target_3",
            "expiry_date",
            "outcome_status",
        ]


class TelegramBulkForm(forms.Form):
    source_name = forms.ChoiceField(choices=[(source.name, source.name) for source in SOURCES if source.category == "TIPS"])
    trade_style = forms.ChoiceField(choices=TradeStyle.choices)
    raw_bulk_text = forms.CharField(widget=forms.Textarea)


class TriggerPromoteForm(forms.Form):
    trigger_id = forms.IntegerField()
    direction = forms.ChoiceField(choices=TipSignal._meta.get_field("direction").choices)
    trade_style = forms.ChoiceField(choices=TradeStyle.choices)


class TradeExecutionForm(forms.ModelForm):
    class Meta:
        model = TradeExecution
        fields = ["quantity", "journal_reason"]

    def clean_journal_reason(self):
        val = str(self.cleaned_data.get("journal_reason") or "").strip()
        if len(val) < 15:
            raise forms.ValidationError("Journal reason must be at least 15 characters.")
        return val


class SignalFilterForm(forms.Form):
    status = forms.ChoiceField(required=False, choices=[("", "All")] + list(SignalStatus.choices))
    source = forms.CharField(required=False)
    style = forms.ChoiceField(required=False, choices=[("", "All")] + list(TradeStyle.choices))
    q = forms.CharField(required=False)


class DhanCredentialsForm(forms.Form):
    access_token = forms.CharField(
        strip=True,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}, render_value=False),
        help_text="Generate a fresh access token in Dhan Web and paste it here.",
    )
    client_id = forms.CharField(
        required=False,
        strip=True,
        help_text="Leave blank to keep the configured Dhan client ID.",
    )
