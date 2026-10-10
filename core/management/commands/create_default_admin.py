from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import User


class Command(BaseCommand):
    help = "Varsayılan admin kullanıcısını oluşturur (zaten varsa dokunmaz). --reset-password ile şifreyi sıfırlar."

    def add_arguments(self, parser):
        parser.add_argument("--reset-password", action="store_true")

    def handle(self, *args, **options):
        username = settings.DEFAULT_ADMIN_USERNAME
        user, created = User.objects.get_or_create(username=username, defaults={
            "email": settings.DEFAULT_ADMIN_EMAIL, "role": User.Role.ADMIN, "is_staff": True,
            "is_superuser": True, "is_temporary_password": False, "first_name": "Sistem", "last_name": "Yöneticisi",
        })
        if created or options["reset_password"]:
            user.set_password(settings.DEFAULT_ADMIN_PASSWORD)
            user.is_temporary_password = False
            user.save()
        self.stdout.write(self.style.SUCCESS(
            f"Admin {'oluşturuldu' if created else 'şifresi sıfırlandı' if options['reset_password'] else 'zaten mevcut'}: {username}"))
