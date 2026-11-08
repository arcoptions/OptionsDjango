from django import forms

from .models import InvestmentDividend, InvestmentHolding, InvestmentIdea


class DateInput(forms.DateInput):
    input_type = "date"


class InvestmentIdeaForm(forms.ModelForm):
    class Meta:
        model = InvestmentIdea
        fields = [
            "symbol",
            "thesis",
            "invalidation",
            "accumulation_low",
            "accumulation_high",
            "target_allocation_pct",
            "review_on",
            "status",
        ]
        widgets = {"review_on": DateInput()}

    def clean_symbol(self):
        return self.cleaned_data["symbol"].strip().upper()


class InvestmentHoldingForm(forms.ModelForm):
    class Meta:
        model = InvestmentHolding
        fields = [
            "idea",
            "quantity",
            "average_cost",
            "current_price",
            "opened_on",
            "closed_on",
            "notes",
        ]
        widgets = {"opened_on": DateInput(), "closed_on": DateInput()}

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["idea"].queryset = InvestmentIdea.objects.filter(owner=owner)


class InvestmentDividendForm(forms.ModelForm):
    class Meta:
        model = InvestmentDividend
        fields = ["holding", "ex_date", "amount_total", "source", "received_on"]
        widgets = {"ex_date": DateInput(), "received_on": DateInput()}

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["holding"].queryset = InvestmentHolding.objects.filter(
            idea__owner=owner,
        ).select_related("idea")
