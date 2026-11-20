from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_stock_panel_notifications"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="assigned_at",
            field=models.DateTimeField(blank=True, editable=False, null=True, verbose_name="Teknisyene atanma zamanı"),
        ),
    ]
