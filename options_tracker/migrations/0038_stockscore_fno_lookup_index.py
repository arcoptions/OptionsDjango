from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("options_tracker", "0037_tradeexecution_execution_mode"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="stockscoresnapshot",
            index=models.Index(
                fields=["score_type", "symbol", "-source_at"],
                name="idx_score_type_sym_time",
            ),
        ),
    ]
