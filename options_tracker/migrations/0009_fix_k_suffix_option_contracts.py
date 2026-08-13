import re
from decimal import Decimal

from django.db import migrations


def fix_k_suffix_option_contracts(apps, schema_editor):
    TipSignal = apps.get_model("options_tracker", "TipSignal")
    pattern = re.compile(r"\bBUY\s+([A-Z][A-Z0-9&.-]{1,24})\s+(\d+(?:\.\d+)?)\s*K\s*(CE|PE)\b", re.IGNORECASE)
    for signal in TipSignal.objects.filter(source_type="TELEGRAM", direction="EQ").iterator():
        match = pattern.search(signal.raw_text or "")
        if not match:
            continue
        strike = format((Decimal(match.group(2)) * 1000).normalize(), "f")
        signal.option_symbol = f"{match.group(1).upper()} {strike} {match.group(3).upper()}"
        signal.direction = match.group(3).upper()
        signal.save(update_fields=["option_symbol", "direction"])


class Migration(migrations.Migration):
    dependencies = [("options_tracker", "0008_indexoptioncandle_indexoptionstrikesnapshot_and_more")]

    operations = [migrations.RunPython(fix_k_suffix_option_contracts, migrations.RunPython.noop)]