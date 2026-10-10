from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "E-posta ayarlarını denemek için test e-postası gönderir: python manage.py send_test_email adres@gmail.com"

    def add_arguments(self, parser):
        parser.add_argument("to")

    def handle(self, *args, to, **options):
        backend = settings.EMAIL_BACKEND.rsplit(".", 2)[-2]
        self.stdout.write(f"Yöntem: {backend}  |  Sunucu: {settings.EMAIL_HOST}:{settings.EMAIL_PORT}  |  Gönderen: {settings.DEFAULT_FROM_EMAIL}")
        if backend == "console":
            self.stdout.write(self.style.WARNING(
                ".env dosyasında EMAIL_HOST_USER / EMAIL_HOST_PASSWORD yok; e-posta sadece terminale yazdırılacak."))
        try:
            send_mail("FIXLAB test e-postası", "E-posta ayarları çalışıyor.", settings.DEFAULT_FROM_EMAIL, [to], fail_silently=False)
        except Exception as exc:
            raise CommandError(f"Gönderilemedi: {exc!r}")
        self.stdout.write(self.style.SUCCESS(f"Gönderildi: {to}"))
