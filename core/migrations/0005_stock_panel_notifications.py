import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

KIND_CHOICES = [
    ("general", "Genel"), ("part_request", "Parça talebi"), ("leave", "İzin talebi"),
    ("delay", "Gecikme"), ("status", "Durum"), ("note", "Not"), ("stock", "Stok"),
]


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_cargo_attachments_notifications"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PartRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.PositiveIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)], verbose_name="Adet")),
                ("status", models.CharField(choices=[("pending", "Onay bekliyor"), ("approved", "Onaylandı"), ("rejected", "Reddedildi")], db_index=True, default="pending", max_length=10, verbose_name="Durum")),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("device", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="part_requests", to="core.device", verbose_name="Cihaz")),
                ("part", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="requests", to="core.stockpart", verbose_name="Parça")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("technician", models.ForeignKey(limit_choices_to={"role": "technician"}, on_delete=django.db.models.deletion.CASCADE, related_name="part_requests", to=settings.AUTH_USER_MODEL, verbose_name="Teknisyen")),
            ],
            options={"verbose_name": "Parça talebi", "verbose_name_plural": "Parça talepleri", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="NotificationPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("part_requests", models.BooleanField(default=True, verbose_name="Parça talepleri")),
                ("leaves", models.BooleanField(default=True, verbose_name="İzin talepleri")),
                ("delays", models.BooleanField(default=True, verbose_name="Gecikme uyarıları")),
                ("statuses", models.BooleanField(default=False, verbose_name="Durum güncellemeleri")),
                ("notes", models.BooleanField(default=True, verbose_name="Teknisyen notları")),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="notification_pref", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "Bildirim tercihi", "verbose_name_plural": "Bildirim tercihleri"},
        ),
        migrations.AddField(
            model_name="notification",
            name="kind",
            field=models.CharField(choices=KIND_CHOICES, default="general", max_length=20, verbose_name="Tür"),
        ),
        migrations.AddField(
            model_name="notification",
            name="actor",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="notification",
            name="leave",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="notifications", to="core.technicianleave"),
        ),
        migrations.AddField(
            model_name="notification",
            name="part_request",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="notifications", to="core.partrequest"),
        ),
    ]
