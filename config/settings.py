"""FIXLAB - Teknik Servis Yönetim Sistemi ayarları."""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path):
    """Proje klasöründeki .env dosyasını okur (KEY=VALUE). Ek paket gerektirmez."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(BASE_DIR / ".env")

# --- Güvenlik -------------------------------------------------------------
# Üretimde SECRET_KEY ve DEBUG mutlaka ortam değişkeniyle verilmelidir.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me-in-production"
)
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core.apps.CoreConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Geçici şifreli kullanıcıyı şifre değiştirme ekranına kilitler.
    "core.middleware.ForcePasswordChangeMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.company",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Veritabanı: PostgreSQL (varsayılan çalışma biçimi) ya da SQLite ---------------
# .env içinde DB_ENGINE=postgres | sqlite   (belirtilmezse sqlite)
# `manage.py test` her zaman hızlı SQLite kullanır (DB_FOR_TESTS=postgres ile değiştirilebilir).
_TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"
DB_ENGINE = os.environ.get("DB_ENGINE", "sqlite").strip().lower()
if _TESTING and os.environ.get("DB_FOR_TESTS", "sqlite").strip().lower() != "postgres":
    DB_ENGINE = "sqlite"

if DB_ENGINE in ("postgres", "postgresql", "pg"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "fixlab"),
            "USER": os.environ.get("DB_USER", "fixlab"),
            "PASSWORD": os.environ.get("DB_PASSWORD", "fixlab_db_pass"),
            "HOST": os.environ.get("DB_HOST", "localhost"),
            "PORT": os.environ.get("DB_PORT", "5432"),
            "CONN_MAX_AGE": int(os.environ.get("DB_CONN_MAX_AGE", "60")),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# --- LDAP: kullanıcı ve müşteri giriş bilgileri dizinde tutulur ----------------------
# LDAP_ENABLED=1 iken: kullanıcı adı, e-posta, ad soyad, telefon, rol ve ŞİFRE LDAP'tadır;
# Django'daki kullanıcı satırı yalnızca gölge kayıttır (bkz. core/accounts.py).
# `manage.py test` sırasında LDAP kapalıdır (testler sahte dizin kullanır).
LDAP_ENABLED = os.environ.get("LDAP_ENABLED", "0") == "1" and not _TESTING
LDAP_URL = os.environ.get("LDAP_URL", "ldap://localhost:389")
LDAP_START_TLS = os.environ.get("LDAP_START_TLS", "0") == "1"   # canlıda ldaps:// ya da StartTLS kullanın
LDAP_BASE_DN = os.environ.get("LDAP_BASE_DN", "dc=fixlab,dc=local")
LDAP_BIND_DN = os.environ.get("LDAP_BIND_DN", "cn=admin,dc=fixlab,dc=local")   # hizmet hesabı
LDAP_BIND_PASSWORD = os.environ.get("LDAP_BIND_PASSWORD", "fixlab_ldap_admin")
LDAP_OU_ADMIN = os.environ.get("LDAP_OU_ADMIN", "admins")
LDAP_OU_TECHNICIAN = os.environ.get("LDAP_OU_TECHNICIAN", "technicians")
LDAP_OU_CUSTOMER = os.environ.get("LDAP_OU_CUSTOMER", "customers")
LDAP_TIMEOUT = int(os.environ.get("LDAP_TIMEOUT", "5"))
# Acil durum: LDAP'a ulaşılamazsa `createsuperuser` ile açılmış yerel hesapla girebilmek için 1 yapın.
LDAP_LOCAL_FALLBACK = os.environ.get("LDAP_LOCAL_FALLBACK", "0") == "1"

AUTHENTICATION_BACKENDS = (
    ["core.ldap_backend.LdapBackend"] + (["django.contrib.auth.backends.ModelBackend"] if LDAP_LOCAL_FALLBACK else [])
    if LDAP_ENABLED else ["django.contrib.auth.backends.ModelBackend"]
)

AUTH_USER_MODEL = "core.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Yerelleştirme --------------------------------------------------------
LANGUAGE_CODE = "tr"
TIME_ZONE = "Europe/Istanbul"
USE_I18N = True
USE_TZ = True

# --- Statik dosyalar ------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- Yüklenen dosyalar (cihaz fotoğraf/videoları) --------------------------
# Dosyalar herkese açık URL ile değil, yetki denetimli görünüm (core:attachment) ile sunulur.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
ATTACHMENT_MAX_FILES = 8
ATTACHMENT_MAX_PHOTO_MB = 10
ATTACHMENT_MAX_VIDEO_MB = 100

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Giriş / çıkış --------------------------------------------------------
LOGIN_URL = "core:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "core:login"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# --- E-posta --------------------------------------------------------------
# .env dosyasında EMAIL_HOST_USER ve EMAIL_HOST_PASSWORD (Gmail uygulama şifresi)
# varsa e-postalar GERÇEKTEN SMTP (Gmail) ile gönderilir.
# Yoksa geliştirme modunda e-postalar terminale (konsola) yazdırılır:
#   EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "").strip()
# Google uygulama şifresi boşluklu gösterilir (abcd efgh ijkl mnop); boşlukları temizle
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "").replace(" ", "")
_SMTP_READY = bool(EMAIL_HOST_USER and EMAIL_HOST_PASSWORD)

EMAIL_BACKEND = os.environ.get(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend" if _SMTP_READY
    else "django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL",
    f"FIXLAB <{EMAIL_HOST_USER}>" if EMAIL_HOST_USER else "FIXLAB <no-reply@fixlab.local>",
)

# KVKK / Gizlilik / Şartlar sayfalarında görünen kurum bilgileri (.env ile değiştirin)
COMPANY_NAME = os.environ.get("COMPANY_NAME", "FIXLAB Teknik Servis")
COMPANY_ADDRESS = os.environ.get("COMPANY_ADDRESS", "")
KVKK_EMAIL = os.environ.get("KVKK_EMAIL", EMAIL_HOST_USER or "kvkk@fixlab.local")

# Müşteri portalındaki «Yardıma mı ihtiyacınız var?» kutusu ve teslim bilgileri
SUPPORT_PHONE = os.environ.get("SUPPORT_PHONE", "0533 305 54 24")
SERVICE_HOURS = os.environ.get("SERVICE_HOURS", "Hafta içi 09:00 – 18:00")

# E-postadaki giriş bağlantısı için
SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000")

# --- Varsayılan admin (ilk migrate sırasında oluşturulur) -----------------
DEFAULT_ADMIN_USERNAME = os.environ.get("FIXLAB_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("FIXLAB_ADMIN_PASSWORD", "FixLab#Admin2025")
DEFAULT_ADMIN_EMAIL = os.environ.get("FIXLAB_ADMIN_EMAIL", "admin@fixlab.local")
