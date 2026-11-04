from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("options_tracker", "0022_radar_price_alert_fair_polling"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="radarbar",
            index=models.Index(fields=["-timestamp"], name="radar_bar_time"),
        ),
    ]
