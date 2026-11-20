"""Eski SQLite verisini (db.sqlite3) PostgreSQL'e taşır.

Kullanım (proje klasöründen, docker compose up -d ile PostgreSQL çalışırken):
    python tools/sqlite_to_postgres.py

Adımlar:
  1) SQLite'taki tüm veriyi eski_veri.json dosyasına yedekler (dosya silinmez, db.sqlite3'e dokunulmaz)
  2) PostgreSQL'de tabloları oluşturur (migrate)
  3) PostgreSQL'i boşaltır (flush) ve yedeği yükler (loaddata)
Ardından:  python manage.py ldap_bootstrap   (kullanıcıları LDAP'a taşır)
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DUMP = ROOT / "eski_veri.json"


def run(args, **env):
    full_env = {**os.environ, **env, "LDAP_ENABLED": "0"}  # taşıma sırasında LDAP'a dokunulmaz
    print("\n>>", "python manage.py", " ".join(args))
    result = subprocess.run([sys.executable, "manage.py", *args], cwd=ROOT, env=full_env)
    if result.returncode != 0:
        sys.exit(f"\nHATA: '{' '.join(args)}' başarısız oldu. Yukarıdaki mesajı kontrol edin; hiçbir şey silinmedi.")


def main():
    sqlite_file = ROOT / "db.sqlite3"
    if not sqlite_file.exists():
        sys.exit("db.sqlite3 bulunamadı: taşınacak eski veri yok. Doğrudan 'python manage.py migrate' çalıştırın.")
    print(f"1/3  SQLite verisi yedekleniyor -> {DUMP.name}")
    run(["dumpdata", "--indent", "2", "--output", str(DUMP),
         "--exclude", "contenttypes", "--exclude", "auth.permission",
         "--exclude", "admin.logentry", "--exclude", "sessions.session"], DB_ENGINE="sqlite")
    print("2/3  PostgreSQL tabloları oluşturuluyor")
    run(["migrate", "--no-input"], DB_ENGINE="postgres")
    print("3/3  PostgreSQL'e veri yükleniyor")
    run(["flush", "--no-input"], DB_ENGINE="postgres")
    run(["loaddata", str(DUMP)], DB_ENGINE="postgres")
    print("\nTAMAM. Sonraki adım:  python manage.py ldap_bootstrap")


if __name__ == "__main__":
    main()
