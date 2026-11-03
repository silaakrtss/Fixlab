"""Uygulama içi bildirimler. Tarayıcı bildirimi, bu kayıtları yoklayan notify.js ile gösterilir."""
from django.urls import reverse

from .models import Notification, NotificationPreference, User


def notify(users, title, body="", url="", device=None, kind=Notification.Kind.GENERAL,
           actor=None, leave=None, part_request=None):
    """Verilen kullanıcılara bildirim kaydı oluşturur (tek kullanıcı da verilebilir)."""
    if isinstance(users, User):
        users = [users]
    rows = [Notification(recipient=u, title=title[:150], body=body[:300], url=url[:255], device=device,
                         kind=kind, actor=actor, leave=leave, part_request=part_request)
            for u in users if u is not None]
    if rows:
        Notification.objects.bulk_create(rows)
    return rows


def visible_notifications(user):
    """Kullanıcının «Bildirim tercihleri»ne göre görebildiği bildirimler (zil, panel ve tarayıcı bildirimi)."""
    qs = Notification.objects.filter(recipient=user)
    hidden = NotificationPreference.hidden_kinds_for(user)
    return qs.exclude(kind__in=hidden) if hidden else qs


def admin_users():
    return User.objects.filter(role=User.Role.ADMIN, is_active=True)


def notify_admins(title, body="", url="", device=None, **extra):
    return notify(list(admin_users()), title, body, url, device, **extra)


def technician_device_url(device):
    return reverse("core:technician_device", args=[device.tracking_id])


def device_admin_url(device):
    return reverse("admin:core_device_change", args=[device.pk])


def notify_device_staff(device, title, body="", **extra):
    """Cihaz için tüm adminlere ve (atanmışsa) cihazın teknisyenine bildirim gönderir.
    Admin, kendi ekranına; teknisyen, cihaz sayfasına giden bağlantıyı alır."""
    rows = notify(list(admin_users()), title, body, device_admin_url(device), device, **extra)
    tech = device.technician
    if tech is not None and tech.is_active and tech.role == User.Role.TECHNICIAN:
        rows += notify(tech, title, body, technician_device_url(device), device, **extra)
    return rows


def customer_device_url(device):
    return reverse("core:customer_device_lookup") + f"?kod={device.tracking_id}"


def customer_cargo_url(device):
    return reverse("core:customer_cargo", args=[device.tracking_id])
