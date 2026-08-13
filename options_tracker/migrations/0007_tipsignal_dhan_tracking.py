from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("options_tracker", "0006_alter_tipsignal_direction"),
    ]

    operations = [
        migrations.AddField(
            model_name="tipsignal",
            name="exchange_segment",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="tipsignal",
            name="dhan_display_name",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="tipsignal",
            name="live_price",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="tipsignal",
            name="quote_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tipsignal",
            name="outcome_status",
            field=models.CharField(
                choices=[
                    ("TRACKING", "Tracking"),
                    ("TARGET_1", "Target 1 Hit"),
                    ("STOP_LOSS", "Stop Loss Hit"),
                    ("UNRESOLVED", "Instrument Not Found"),
                ],
                default="TRACKING",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="tipsignal",
            name="outcome_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]