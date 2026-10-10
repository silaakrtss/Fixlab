import logging
import secrets
import string

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from .models import AuditLog, User

logger = logging.getLogger(__name__)

_TR_MAP = str.maketrans({"ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "İ": "i",
                         "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u"})


def get_client_ip(request):
    return request.META.get("REMOTE_ADDR") if request else None


def log_action(action, user=None, request=None, target=None, description="", username=""):
    """İşlem geçmişine kayıt ekler. Log hatası asıl akışı bozmaz."""
    try:
        AuditLog.objects.create(
            user=user if (user and getattr(user, "pk", None)) else None,
            username_snapshot=username or (getattr(user, "username", "") if user else ""),
            action=action,
            target_type=target.__class__.__name__ if target is not None else "",
            target_id=str(getattr(target, "pk", "")) if target is not None else "",
            description=description,
            ip_address=get_client_ip(request),
        )
    except Exception:  # pragma: no cover
        logger.exception("Audit log yazılamadı")


def generate_username(first_name, last_name):
    """ad.soyad biçiminde benzersiz kullanıcı adı üretir (ahmet.yilmaz, ahmet.yilmaz2 ...)."""
    def clean(value):
        value = value.translate(_TR_MAP).lower().strip()
        return "".join(ch for ch in value if ch.isascii() and ch.isalnum())

    first, last = clean(first_name), clean(last_name)
    base = ".".join(part for part in (first, last) if part) or "kullanici"
    base = base[:140]
    candidate, counter = base, 1
    while User.objects.filter(username__iexact=candidate).exists():
        counter += 1
        candidate = f"{base}{counter}"
    return candidate


def generate_temp_password(length=12):
    """Güvenli geçici şifre: büyük/küçük harf, rakam ve sembol içerir; karışan karakterler yok."""
    upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    lower = "abcdefghijkmnopqrstuvwxyz"
    digits = "23456789"
    symbols = "!@#$%*-_"
    chars = [secrets.choice(upper), secrets.choice(lower), secrets.choice(digits), secrets.choice(symbols)]
    pool = upper + lower + digits + symbols
    chars += [secrets.choice(pool) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def send_credentials_email(user, raw_password):
    context = {"user": user, "raw_password": raw_password, "login_url": f"{settings.SITE_URL}/giris/",
               "role_label": user.get_role_display().lower()}
    subject = "FIXLAB hesabınız oluşturuldu"
    text = render_to_string("core/email/credentials.txt", context)
    html = render_to_string("core/email/credentials.html", context)
    try:
        sent = send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [user.email],
                         html_message=html, fail_silently=False)
        return sent > 0
    except Exception:
        logger.exception("Giriş bilgisi e-postası gönderilemedi: %s", user.email)
        return False
