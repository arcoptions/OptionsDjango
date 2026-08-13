from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("options_tracker", "0005_alter_appsetting_value"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tipsignal",
            name="direction",
            field=models.CharField(choices=[("CE", "CE"), ("PE", "PE"), ("EQ", "Equity")], max_length=2),
        ),
    ]