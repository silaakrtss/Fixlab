import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_device_assigned_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="user", name="city",
            field=models.CharField(blank=True, max_length=60, verbose_name="İl"),
        ),
        migrations.AddField(
            model_name="user", name="district",
            field=models.CharField(blank=True, max_length=60, verbose_name="İlçe"),
        ),
        migrations.AddField(
            model_name="user", name="address",
            field=models.TextField(blank=True, verbose_name="Açık adres"),
        ),
        migrations.AddField(
            model_name="user", name="email_verified",
            field=models.BooleanField(default=True, verbose_name="E-posta doğrulandı"),
        ),
        migrations.CreateModel(
            name="EmailVerificationCode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code_hash", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField()),
                ("attempts", models.PositiveSmallIntegerField(default=0, verbose_name="Hatalı deneme")),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="verification_codes",
                                           to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "E-posta doğrulama kodu",
                "verbose_name_plural": "E-posta doğrulama kodları",
                "ordering": ["-created_at"],
            },
        ),
    ]
