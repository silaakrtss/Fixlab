import core.models
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

STATUS_CHOICES = [
    ("pre_registered", "Ön kayıt alındı"),
    ("awaiting_cargo", "Kargo bekleniyor"),
    ("in_transit", "Kargoda · Servise geliyor"),
    ("received", "Teslim alındı"),
    ("diagnosing", "İnceleniyor"),
    ("waiting_approval", "Müşteri onayı bekliyor"),
    ("waiting_part", "Parça bekliyor"),
    ("repairing", "Tamirde"),
    ("testing", "Test aşamasında"),
    ("ready", "Teslime hazır"),
    ("return_shipping", "Geri kargoda"),
    ("delivered", "Teslim edildi"),
    ("unrepairable", "Onarılamaz"),
    ("cancelled", "İptal edildi"),
]


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_passwordresetcode"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="device", name="status",
            field=models.CharField(choices=STATUS_CHOICES, db_index=True, default="received", max_length=20, verbose_name="Durum"),
        ),
        migrations.AlterField(
            model_name="devicestatushistory", name="old_status",
            field=models.CharField(blank=True, choices=STATUS_CHOICES, max_length=20),
        ),
        migrations.AlterField(
            model_name="devicestatushistory", name="new_status",
            field=models.CharField(choices=STATUS_CHOICES, max_length=20),
        ),
        migrations.AlterField(
            model_name="device", name="customer_phone",
            field=models.CharField(blank=True, max_length=20, validators=[core.models.phone_validator], verbose_name="Müşteri telefonu"),
        ),
        migrations.AddField(
            model_name="device", name="issue_title",
            field=models.CharField(blank=True, max_length=150, verbose_name="Arıza başlığı"),
        ),
        migrations.AddField(
            model_name="device", name="delivery_method",
            field=models.CharField(choices=[("self", "Kendim teslim edeceğim"), ("cargo", "Kargo ile göndereceğim")], default="self", max_length=10, verbose_name="Teslim yöntemi"),
        ),
        migrations.AddField(
            model_name="device", name="cargo_company",
            field=models.CharField(blank=True, max_length=80, verbose_name="Kargo firması (müşteri → servis)"),
        ),
        migrations.AddField(
            model_name="device", name="cargo_tracking_code",
            field=models.CharField(blank=True, max_length=60, verbose_name="Kargo takip kodu (müşteri → servis)"),
        ),
        migrations.AddField(
            model_name="device", name="cargo_sent_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Kargoya verilme zamanı"),
        ),
        migrations.AddField(
            model_name="device", name="return_cargo_company",
            field=models.CharField(blank=True, max_length=80, verbose_name="Geri kargo firması (servis → müşteri)"),
        ),
        migrations.AddField(
            model_name="device", name="return_cargo_tracking_code",
            field=models.CharField(blank=True, max_length=60, verbose_name="Geri kargo takip kodu (servis → müşteri)"),
        ),
        migrations.AddField(
            model_name="device", name="return_recipient",
            field=models.CharField(blank=True, max_length=150, verbose_name="Geri gönderim alıcısı"),
        ),
        migrations.AddField(
            model_name="device", name="return_phone",
            field=models.CharField(blank=True, max_length=20, verbose_name="Geri gönderim telefonu"),
        ),
        migrations.AddField(
            model_name="device", name="return_city",
            field=models.CharField(blank=True, max_length=60, verbose_name="Geri gönderim ili"),
        ),
        migrations.AddField(
            model_name="device", name="return_district",
            field=models.CharField(blank=True, max_length=60, verbose_name="Geri gönderim ilçesi"),
        ),
        migrations.AddField(
            model_name="device", name="return_address",
            field=models.TextField(blank=True, verbose_name="Geri gönderim açık adresi"),
        ),
        migrations.CreateModel(
            name="DeviceAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to=core.models._attachment_path, verbose_name="Dosya")),
                ("original_name", models.CharField(max_length=255, verbose_name="Dosya adı")),
                ("kind", models.CharField(choices=[("photo", "Fotoğraf"), ("video", "Video")], max_length=10, verbose_name="Tür")),
                ("size", models.PositiveBigIntegerField(default=0, verbose_name="Boyut (bayt)")),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("device", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="core.device")),
            ],
            options={"verbose_name": "Cihaz eki", "verbose_name_plural": "Cihaz ekleri", "ordering": ["uploaded_at", "id"]},
        ),
        migrations.CreateModel(
            name="Notification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=150, verbose_name="Başlık")),
                ("body", models.CharField(blank=True, max_length=300, verbose_name="Metin")),
                ("url", models.CharField(blank=True, max_length=255, verbose_name="Bağlantı")),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("device", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="notifications", to="core.device")),
                ("recipient", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notifications", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "Bildirim", "verbose_name_plural": "Bildirimler", "ordering": ["-id"]},
        ),
        migrations.AddIndex(
            model_name="notification",
            index=models.Index(fields=["recipient", "read_at"], name="notif_recipient_read_idx"),
        ),
    ]
