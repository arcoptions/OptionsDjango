from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("options_tracker", "0003_chatmessage")]

    operations = [
        migrations.AddField(
            model_name="chatmessage",
            name="source_category",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="chatmessage",
            name="telegram_chat_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="chatmessage",
            name="telegram_message_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="chatmessage",
            name="telegram_message_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="chatmessage",
            name="raw_payload",
            field=models.TextField(blank=True),
        ),
        migrations.AddConstraint(
            model_name="chatmessage",
            constraint=models.UniqueConstraint(
                condition=models.Q(telegram_chat_id__isnull=False, telegram_message_id__isnull=False),
                fields=("telegram_chat_id", "telegram_message_id"),
                name="unique_telegram_chat_message",
            ),
        ),
    ]