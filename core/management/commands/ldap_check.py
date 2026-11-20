from django.core.management.base import BaseCommand, CommandError

from core import accounts
from core.models import User


class Command(BaseCommand):
    help = "LDAP bağlantısını ve dizin ile veritabanı arasındaki tutarlılığı denetler."

    def handle(self, *args, **options):
        if not accounts.ldap_enabled():
            raise CommandError("LDAP kapalı (.env: LDAP_ENABLED=1 yapın).")
        directory = accounts.get_directory()
        try:
            directory.ping()
            entries = directory.list_users()
        except accounts.DirectoryError as exc:
            raise CommandError(f"LDAP sorunu: {exc}")
        self.stdout.write(self.style.SUCCESS(f"LDAP bağlantısı tamam. Dizinde {len(entries)} kullanıcı var."))
        by_role = {}
        for entry in entries:
            by_role[entry.role] = by_role.get(entry.role, 0) + 1
        for role, count in sorted(by_role.items()):
            self.stdout.write(f"  {role:<12}{count}")
        dir_uids = {e.uid.lower() for e in entries}
        missing = [u.username for u in User.objects.all() if u.username.lower() not in dir_uids]
        orphan = sorted(dir_uids - {u.username.lower() for u in User.objects.all()})
        if missing:
            self.stdout.write(self.style.WARNING(
                "Veritabanında olup dizinde olmayan (giriş yapamaz): " + ", ".join(missing)
                + "\n  -> python manage.py ldap_bootstrap"))
        if orphan:
            self.stdout.write("Dizinde olup henüz veritabanında kaydı olmayan (ilk girişte otomatik oluşur): " + ", ".join(orphan))
        if not missing:
            self.stdout.write(self.style.SUCCESS("Tüm veritabanı kullanıcılarının dizinde kaydı var."))
