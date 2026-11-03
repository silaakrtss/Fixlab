import logging
import secrets
import string

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

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


def send_device_registered_email(device):
    """Cihaz kaydı oluşunca müşteriye sorgulama kodunu ve kayıt bilgisini e-postayla gönderir.

    Alıcı: müşteri hesabının e-postası, yoksa kayıttaki müşteri e-postası. Adres yoksa sessizce atlanır.
    Gönderim hatası kayıt işlemini asla bozmaz (loglanır, False döner)."""
    recipient = ((device.customer.email if device.customer_id else "") or device.customer_email or "").strip()
    if not recipient:
        return False
    name = (device.customer.display_name if device.customer_id else "") or device.customer_name or "Müşterimiz"
    context = {
        "name": name, "tracking_id": device.tracking_id,
        "device_label": f"{device.brand} {device.model}".strip() or device.get_device_type_display(),
        "issue_title": device.issue_title, "status_label": device.get_status_display(),
        "is_cargo": device.is_cargo, "login_url": f"{settings.SITE_URL}{reverse('core:customer_login')}",
    }
    subject = f"FIXLAB servis kaydınız oluşturuldu · {device.tracking_id}"
    text = render_to_string("core/email/device_registered.txt", context)
    html = render_to_string("core/email/device_registered.html", context)
    try:
        sent = send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [recipient],
                         html_message=html, fail_silently=False)
        return sent > 0
    except Exception:
        logger.exception("Cihaz kayıt e-postası gönderilemedi: %s (%s)", recipient, device.tracking_id)
        return False


def mask_email(email):
    """ornek@site.com -> o•••••@•••••.com"""
    local, _, domain = (email or "").partition("@")
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    return f"{local[:1]}•••••@•••••{'.' + tld if tld else ''}"


def send_return_shipped_email(device):
    """Ürün müşteriye geri kargoya verilince bilgilendirme e-postası; içindeki «Ürünü teslim aldım» bağlantısı
    güvenli (imzalı) bir onay sayfasını açar. Alıcı: müşteri hesabı e-postası, yoksa kayıttaki e-posta."""
    from .cargo import receipt_token
    recipient = ((device.customer.email if device.customer_id else "") or device.customer_email or "").strip()
    if not recipient:
        return False
    name = (device.customer.display_name if device.customer_id else "") or device.customer_name or "Müşterimiz"
    place = " / ".join(x for x in (device.return_district, device.return_city) if x)
    context = {
        "name": name, "tracking_id": device.tracking_id,
        "device_label": f"{device.brand} {device.model}".strip() or device.get_device_type_display(),
        "return_code": device.return_cargo_code, "cargo_company": device.return_cargo_company,
        "cargo_tracking": device.return_cargo_tracking_code,
        "address": ", ".join(x for x in (device.return_address, place) if x), "recipient": device.return_recipient,
        "confirm_url": f"{settings.SITE_URL}{reverse('core:receipt_confirm', args=[receipt_token(device)])}",
    }
    subject = f"FIXLAB cihazınız kargoya verildi · {device.tracking_id}"
    text = render_to_string("core/email/return_shipped.txt", context)
    html = render_to_string("core/email/return_shipped.html", context)
    try:
        sent = send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [recipient], html_message=html, fail_silently=False)
        return sent > 0
    except Exception:
        logger.exception("Geri kargo e-postası gönderilemedi: %s (%s)", recipient, device.tracking_id)
        return False


def _money(value):
    """1850 -> «1.850,00 ₺» (yerel biçim)."""
    from django.utils.formats import number_format
    return f"{number_format(value, decimal_pos=2, use_l10n=True, force_grouping=True)} ₺"


def send_payment_receipt_email(device):
    """Ödeme alındığında müşteriye ödeme dökümünü (makbuz) e-postayla gönderir.

    Döküm: servis kaydı, ödeme tarihi, işçilik, kullanılan parçalar ve toplam. Alıcı: müşteri hesabının e-postası,
    yoksa kayıttaki müşteri e-postası. Adres yoksa sessizce atlanır; gönderim hatası kaydı asla bozmaz."""
    from django.utils import timezone
    recipient = ((device.customer.email if device.customer_id else "") or device.customer_email or "").strip()
    if not recipient:
        return False
    name = (device.customer.display_name if device.customer_id else "") or device.customer_name or "Müşterimiz"
    lines = []
    if device.labor_cost:
        lines.append({"label": "İşçilik", "detail": "", "amount": _money(device.labor_cost)})
    for usage in device.part_usages.select_related("part").order_by("added_at", "id"):
        lines.append({"label": usage.part.name, "detail": f"{usage.quantity} adet × {_money(usage.unit_price)}",
                      "amount": _money(usage.line_total)})
    total = device.total_cost
    if not lines or total <= 0:
        # Kalem kalem döküm yoksa tahmini ücret tek satır olarak gösterilir.
        total = device.estimated_cost or total
        lines = [{"label": "Servis ücreti", "detail": "", "amount": _money(total)}]
    context = {
        "name": name, "tracking_id": device.tracking_id,
        "device_label": f"{device.brand} {device.model}".strip() or device.get_device_type_display(),
        "issue_title": device.issue_title, "paid_at": timezone.localtime(timezone.now()),
        "lines": lines, "total": _money(total),
        "login_url": f"{settings.SITE_URL}{reverse('core:customer_login')}",
    }
    subject = f"FIXLAB ödeme dökümünüz · {device.tracking_id}"
    text = render_to_string("core/email/payment_receipt.txt", context)
    html = render_to_string("core/email/payment_receipt.html", context)
    try:
        sent = send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [recipient], html_message=html, fail_silently=False)
        return sent > 0
    except Exception:
        logger.exception("Ödeme dökümü e-postası gönderilemedi: %s (%s)", recipient, device.tracking_id)
        return False


def send_verification_code_email(user, code, minutes):
    context = {"user": user, "code": code, "minutes": minutes}
    subject = "FIXLAB e-posta doğrulama kodunuz"
    text = render_to_string("core/email/verify_code.txt", context)
    html = render_to_string("core/email/verify_code.html", context)
    try:
        sent = send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [user.email],
                         html_message=html, fail_silently=False)
        return sent > 0
    except Exception:
        logger.exception("E-posta doğrulama kodu gönderilemedi: %s", user.email)
        return False


def send_reset_code_email(user, code, minutes):
    context = {"user": user, "code": code, "minutes": minutes}
    subject = "FIXLAB şifre sıfırlama kodunuz"
    text = render_to_string("core/email/reset_code.txt", context)
    html = render_to_string("core/email/reset_code.html", context)
    try:
        sent = send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [user.email],
                         html_message=html, fail_silently=False)
        return sent > 0
    except Exception:
        logger.exception("Şifre sıfırlama kodu e-postası gönderilemedi: %s", user.email)
        return False
