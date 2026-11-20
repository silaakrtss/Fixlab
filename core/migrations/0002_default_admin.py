from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations


def create_default_admin(apps, schema_editor):
    User = apps.get_model("core", "User")
    if User.objects.filter(username=settings.DEFAULT_ADMIN_USERNAME).exists():
        return
    User.objects.create(
        username=settings.DEFAULT_ADMIN_USERNAME,
        email=settings.DEFAULT_ADMIN_EMAIL,
        password=make_password(settings.DEFAULT_ADMIN_PASSWORD),
        first_name="Sistem",
        last_name="Yöneticisi",
        role="admin",
        is_staff=True,
        is_superuser=True,
        is_temporary_password=False,
    )


def remove_default_admin(apps, schema_editor):
    apps.get_model("core", "User").objects.filter(username=settings.DEFAULT_ADMIN_USERNAME).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(create_default_admin, remove_default_admin)]
