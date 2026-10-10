"""FIXLAB - Teknik Servis Yönetim Sistemi ayarları."""
import os
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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

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

# E-postadaki giriş bağlantısı için
SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000")

# --- Varsayılan admin (ilk migrate sırasında oluşturulur) -----------------
DEFAULT_ADMIN_USERNAME = os.environ.get("FIXLAB_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("FIXLAB_ADMIN_PASSWORD", "FixLab#Admin2025")
DEFAULT_ADMIN_EMAIL = os.environ.get("FIXLAB_ADMIN_EMAIL", "admin@fixlab.local")
