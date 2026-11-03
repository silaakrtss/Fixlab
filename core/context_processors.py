from django.conf import settings
from django.db.models import Q

from .models import Device, User
from .notifications import visible_notifications


def new_devices_filter():
    """«Yeni kayıtlar»: servise ulaşmamış ön kayıtlar (ön kayıt / kargo bekleniyor / kargoda) ve
    servise ulaşmış ama henüz teknisyene atanmamış cihazlar."""
    S = Device.Status
    return Q(status__in=[S.PRE_REGISTERED, S.AWAITING_CARGO, S.IN_TRANSIT]) | Q(
        status=S.RECEIVED, technician__isnull=True)


def admin_nav_counts():
    """Admin sol menüsündeki «Cihazlar» altındaki sayaçlar.

    Yeni kayıtlar : new_devices_filter() ile eşleşen cihazlar.
    Kargolanacaklar: onarımı bitmiş (teslime hazır), müşteriye kargoyla geri gönderilecek cihazlar.
    """
    S = Device.Status
    new_count = Device.objects.filter(new_devices_filter()).count()
    cargo_count = Device.objects.filter(status=S.READY, delivery_method=Device.DeliveryMethod.CARGO).count()
    return {"nav_new_devices": new_count, "nav_cargo_devices": cargo_count, "nav_total_devices": new_count + cargo_count}


def tel_href(phone):
    """«0533 305 54 24» → «+905333055424» (telefonda arama ekranını numarayla açan tel: bağlantısı için)."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = "90" + digits[1:]
    elif len(digits) == 10:
        digits = "90" + digits
    return f"+{digits}" if digits else ""


def company(request):
    unread = 0
    user = getattr(request, "user", None)
    authenticated = user is not None and user.is_authenticated
    if authenticated:
        unread = visible_notifications(user).filter(read_at__isnull=True).count()
    context = {
        "COMPANY_NAME": settings.COMPANY_NAME,
        "COMPANY_ADDRESS": settings.COMPANY_ADDRESS,
        "KVKK_EMAIL": settings.KVKK_EMAIL,
        "SUPPORT_PHONE": settings.SUPPORT_PHONE,
        "SUPPORT_PHONE_TEL": tel_href(settings.SUPPORT_PHONE),
        "SERVICE_HOURS": settings.SERVICE_HOURS,
        "unread_notifications": unread,
        "nav_role": getattr(user, "role", "") if authenticated else "",
    }
    if authenticated and user.role == User.Role.ADMIN:
        context.update(admin_nav_counts())
    return context
