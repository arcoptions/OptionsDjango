from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("options_tracker", "0004_chatmessage_telegram_metadata")]

    operations = [
        migrations.AlterField(
            model_name="appsetting",
            name="value",
            field=models.TextField(),
        ),
    ]