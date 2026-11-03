from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core import accounts
from core.models import User
from core.utils import generate_temp_password, send_credentials_email


class Command(BaseCommand):
    help = ("LDAP dizinini hazırlar: ou=admins / technicians / customers kutularını oluşturur ve veritabanındaki "
            "mevcut kullanıcıları (admin, teknisyen, müşteri) dizine taşır. Tekrar çalıştırmak güvenlidir.")

    def add_arguments(self, parser):
        parser.add_argument("--notify", action="store_true",
                            help="Yeni geçici şifreleri kullanıcılara e-postayla da gönder.")

    def handle(self, *args, **options):
        if not accounts.ldap_enabled():
            raise CommandError(".env dosyasında LDAP_ENABLED=1 olmalı.")
        directory = accounts.get_directory()
        try:
            directory.ping()
            created_ous = directory.ensure_structure()
        except accounts.DirectoryError as exc:
            raise CommandError(f"LDAP sunucusuna bağlanılamadı: {exc}\n"
                               "docker compose up -d ile sunucuyu başlattınız mı? LDAP_URL / LDAP_BIND_* ayarlarını kontrol edin.")
        self.stdout.write(self.style.SUCCESS(
            f"Dizin yapısı hazır ({', '.join(created_ous) + ' oluşturuldu' if created_ous else 'zaten vardı'})."))

        moved, skipped, issued = 0, 0, []
        for user in User.objects.order_by("id"):
            if directory.find(user.username) is not None:
                skipped += 1
                continue
            is_default_admin = user.username == settings.DEFAULT_ADMIN_USERNAME
            # Eski şifre özetleri LDAP'a taşınamaz: varsayılan admin ayardaki şifreyi, diğerleri geçici şifre alır.
            password = settings.DEFAULT_ADMIN_PASSWORD if is_default_admin else generate_temp_password(14)
            try:
                directory.create(accounts.entry_from_user(user), password)
            except accounts.DirectoryConflict as exc:
                self.stderr.write(self.style.WARNING(f"ATLANDI {user.username}: {exc}"))
                skipped += 1
                continue
            user.set_unusable_password()  # yerel şifre özeti artık kullanılmaz
            user.is_temporary_password = user.role != User.Role.CUSTOMER and not is_default_admin
            user.save(update_fields=["password", "is_temporary_password"])
            moved += 1
            if not is_default_admin:
                issued.append((user, password))

        self.stdout.write(self.style.SUCCESS(f"{moved} kullanıcı LDAP'a taşındı, {skipped} kullanıcı atlandı (zaten dizinde)."))
        if issued:
            self.stdout.write("\nYENİ GEÇİCİ ŞİFRELER (yalnızca şimdi gösterilir; personel ilk girişte değiştirir,")
            self.stdout.write("müşteriler isterse «Şifremi unuttum» ile kendi şifresini belirleyebilir):\n")
            self.stdout.write(f"  {'KULLANICI ADI':<28}{'ROL':<12}GEÇİCİ ŞİFRE")
            for user, password in issued:
                self.stdout.write(f"  {user.username:<28}{user.get_role_display():<12}{password}")
            if options["notify"]:
                sent = sum(1 for user, password in issued if send_credentials_email(user, password))
                self.stdout.write(f"\n{sent}/{len(issued)} kullanıcıya e-posta gönderildi.")
        if any(u.username == settings.DEFAULT_ADMIN_USERNAME for u in User.objects.all()):
            self.stdout.write(f"\nVarsayılan admin ({settings.DEFAULT_ADMIN_USERNAME}) şifresi .env'deki FIXLAB_ADMIN_PASSWORD'dür.")
