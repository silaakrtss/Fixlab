from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_profile_address_email_verification"),
    ]

    operations = [
        migrations.AddField(
            model_name="device", name="cargo_code",
            field=models.CharField(blank=True, max_length=20, verbose_name="Kargo kodu (müşteri → servis)"),
        ),
        migrations.AddField(
            model_name="device", name="cargo_code_created_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Kargo kodu oluşturulma zamanı"),
        ),
        migrations.AddField(
            model_name="device", name="return_cargo_code",
            field=models.CharField(blank=True, max_length=20, verbose_name="Geri kargo kodu (servis → müşteri)"),
        ),
        migrations.AddField(
            model_name="device", name="return_cargo_code_created_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Geri kargo kodu oluşturulma zamanı"),
        ),
        migrations.AddField(
            model_name="device", name="return_received_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Müşterinin teslim aldığını onayladığı zaman"),
        ),
    ]
