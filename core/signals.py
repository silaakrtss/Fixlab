import functools
from datetime import timedelta

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.utils import timezone
from django.dispatch import receiver
from django.urls import reverse
from django.utils.formats import number_format

from .models import (AuditLog, Device, DeviceStatusHistory, Notification, PartRequest, StockMovement, StockPart,
                     TechnicianLeave, User)
from .notifications import customer_cargo_url, customer_device_url, notify, notify_admins, notify_device_staff
from .staffing import business_days as _business_days
from .utils import (link_guest_devices, log_action, send_device_registered_email, send_payment_receipt_email,
                    send_return_shipped_email, send_status_update_email)


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log_action(AuditLog.Action.LOGIN, user=user, request=request, target=user, description="Sisteme giriş yapıldı")
    if getattr(user, "role", None) == User.Role.CUSTOMER:
        # Önceden üye olmuş müşterinin serviste hesapsız açılmış kayıtları da girişte hesabına bağlanır.
        link_guest_devices(user, request=request)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user:
        log_action(AuditLog.Action.LOGOUT, user=user, request=request, target=user, description="Çıkış yapıldı")


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    log_action(AuditLog.Action.LOGIN_FAILED, request=request,
               username=str(credentials.get("username", ""))[:150], description="Başarısız giriş denemesi")


# --------------------------------------------------------------------------
# Cihaz değişiklikleri: durum geçmişi + müşteri / admin bildirimleri
# --------------------------------------------------------------------------
# Müşterinin görebildiği alanlar. İç notlar (teşhis notu, atanan teknisyen, parça/işçilik) bildirim üretmez.
TRACKED_FIELDS = ("status", "estimated_cost", "estimated_delivery", "is_paid",
                  "return_cargo_company", "return_cargo_tracking_code")

S = Device.Status
STATUS_TITLES = {
    S.PRE_REGISTERED: "Ön kaydınız alındı",
    S.AWAITING_CARGO: "Kargo bekleniyor",
    S.IN_TRANSIT: "Cihazınız kargoda",
    S.RECEIVED: "Cihazınız servise ulaştı",
    S.DIAGNOSING: "Cihazınız inceleniyor",
    S.WAITING_APPROVAL: "Onayınız bekleniyor",
    S.WAITING_PART: "Onarım için parça bekleniyor",
    S.REPAIRING: "Cihazınız tamirde",
    S.TESTING: "Cihazınız test aşamasında",
    S.READY: "Cihazınız teslime hazır",
    S.RETURN_SHIPPING: "Cihazınız geri kargoya verildi",
    S.DELIVERED: "Cihazınız teslim edildi",
    S.UNREPAIRABLE: "Cihazınız onarılamadı",
    S.CANCELLED: "Servis kaydınız iptal edildi",
}


def _skip_raw(func):
    """loaddata gibi ham (raw) yüklemelerde bildirim/geçmiş üretmesin (örn. SQLite -> PostgreSQL veri taşıma)."""
    @functools.wraps(func)
    def wrapper(sender, instance, *args, **kwargs):
        if kwargs.get("raw"):
            return None
        return func(sender, instance, *args, **kwargs)
    return wrapper


def _admin_url(device):
    return reverse("admin:core_device_change", args=[device.pk])


@receiver(pre_save, sender=Device)
@_skip_raw
def device_remember_old_values(sender, instance, **kwargs):
    instance._old_values = None
    if instance.pk:
        instance._old_values = Device.objects.filter(pk=instance.pk).values(*TRACKED_FIELDS).first()


@receiver(pre_save, sender=Device)
@_skip_raw
def device_remember_technician(sender, instance, **kwargs):
    """İş ataması değiştiyse atanma zamanını yazar; bildirim için eski teknisyeni hatırlar."""
    previous = Device.objects.filter(pk=instance.pk).values_list("technician_id", flat=True).first() if instance.pk else None
    instance._old_technician_id = previous
    if instance.technician_id != previous:
        instance.assigned_at = timezone.now() if instance.technician_id else None


@receiver(post_save, sender=Device)
@_skip_raw
def device_assignment_changed(sender, instance, created, **kwargs):
    """Admin bir cihazı teknisyene atadığında (ya da başka teknisyene aktardığında) teknisyene bildirim gider."""
    previous = getattr(instance, "_old_technician_id", None)
    if instance.technician_id == previous:
        return
    actor = getattr(instance, "_actor", None)
    actor = actor if actor and actor.pk else None
    label = f"{instance.tracking_id} · {instance.title}"
    url = reverse("core:technician_dashboard")
    if instance.technician_id:
        note = getattr(instance, "_assign_note", "")
        notify(instance.technician, "Size yeni bir iş atandı", f"{label} — Not: {note}" if note else label,
               url=url, device=instance, actor=actor)
        log_action(AuditLog.Action.UPDATE, user=actor, target=instance,
                   description=f"{label} cihazı {instance.technician.display_name} teknisyenine atandı"
                               + (f" (Not: {note})" if note else ""))
    if previous:
        old_tech = User.objects.filter(pk=previous).first()
        if old_tech:
            notify(old_tech, "Bir iş sizden alındı", label, url=url, device=instance, actor=actor)


@receiver(post_save, sender=Device)
@_skip_raw
def device_auto_assign(sender, instance, created, **kwargs):
    """Kayıt atanabilir hale geldiyse (servis kabulü / kargoya verildi / elden teslim) ve teknisyeni yoksa
    AI + iş yüküne göre otomatik atama planlanır (arka planda, kayıt işlendikten sonra)."""
    from . import auto_assign  # döngüsel içe aktarmayı önlemek için burada
    old = getattr(instance, "_old_values", None) or {}
    if auto_assign.atanabilir_hale_geldi(instance, created, old.get("status")):
        auto_assign.planla(instance.pk)


def _changes(instance, old):
    """(başlık, [ek satırlar]) döndürür; değişiklik yoksa (None, [])."""
    title, lines = None, []
    if old["status"] != instance.status:
        title = STATUS_TITLES.get(instance.status, "Servis durumunuz güncellendi")
    if old["estimated_cost"] != instance.estimated_cost:
        lines.append(f"Tahmini ücret: {number_format(instance.estimated_cost, decimal_pos=0, use_l10n=True)} ₺")
    if old["estimated_delivery"] != instance.estimated_delivery and instance.estimated_delivery:
        when = instance.estimated_delivery
        lines.append("Tahmini teslim: " + (when.strftime("%d.%m.%Y") if hasattr(when, "strftime") else str(when)))
    if not old["is_paid"] and instance.is_paid:
        lines.append("Ödemeniz alındı")
    if old["return_cargo_tracking_code"] != instance.return_cargo_tracking_code and instance.return_cargo_tracking_code:
        lines.append(f"Geri kargo takip kodu: {instance.return_cargo_tracking_code}")
    return title, lines


@receiver(post_save, sender=Device)
@_skip_raw
def device_saved(sender, instance, created, **kwargs):
    actor = getattr(instance, "_actor", None)
    by_customer = bool(actor and instance.customer_id and actor.pk == instance.customer_id)
    old = getattr(instance, "_old_values", None)
    label = f"{instance.tracking_id} · {instance.title}"

    if created:
        DeviceStatusHistory.objects.create(
            device=instance, old_status="", new_status=instance.status,
            note=getattr(instance, "_history_note", "") or "Kayıt oluşturuldu",
            changed_by=actor if actor and actor.pk else None)
        if not getattr(instance, "_skip_email", False):
            # Müşteriye sorgulama kodu + kayıt bilgilendirmesi (müşteri ya da personel kaydı fark etmez).
            # Kayıt veritabanına işlendikten sonra gönderilir; hata kaydı bozmaz.
            transaction.on_commit(lambda: send_device_registered_email(instance))
        if by_customer:
            how = "kargo ile gönderecek" if instance.is_cargo else "kendisi teslim edecek"
            # Ayrıntılar bildirimin içinde: müşteri, iletişim, cihaz, arıza ve teslim yöntemi (bildirim gövdesi 300 karakterle sınırlı,
            # bu yüzden en değerli bilgiler başta, uzun olabilen arıza metni en sonda ve kısaltılmış).
            issue = " ".join((instance.issue_description or "").split())
            issue = (issue[:100].rstrip() + "…") if len(issue) > 100 else issue
            details = [f"{instance.customer_name} · {label} ({instance.get_device_type_display()})",
                       f"Müşteri cihazı {how}."]
            if instance.customer_phone:
                details.append(f"Tel: {instance.customer_phone}")
            if issue:
                details.append(f"Arıza: {issue}")
            notify_admins("Yeni cihaz ön kaydı", " — ".join(details[:2]) + "".join(f" · {d}" for d in details[2:]),
                          url=_admin_url(instance), device=instance)
        elif instance.customer_id:
            notify(instance.customer, "Servis kaydınız oluşturuldu", label,
                   url=customer_device_url(instance), device=instance)
        return

    if not old:
        return
    title, lines = _changes(instance, old)
    if not old["is_paid"] and instance.is_paid and not getattr(instance, "_skip_email", False):
        # Ödeme alındı: ödeme dökümü müşteriye e-postayla gider (kayıt işlendikten sonra; hata kaydı bozmaz).
        transaction.on_commit(lambda: send_payment_receipt_email(instance))
    if not old["is_paid"] and instance.is_paid:
        # Ödeme alındı: yöneticilere ve sorumlu teknisyene haber ver (teslime hazır kargolu cihaz kargo kuyruğuna geçer).
        amount = instance.total_cost if instance.total_cost > 0 else instance.estimated_cost
        money = f"{number_format(amount, decimal_pos=0, use_l10n=True)} ₺ " if amount and amount > 0 else ""
        notify_device_staff(instance, "Ödeme alındı", f"{label} için {money}ödeme alındı.")
    status_changed = old["status"] != instance.status
    if status_changed:
        DeviceStatusHistory.objects.create(
            device=instance, old_status=old["status"], new_status=instance.status,
            note=getattr(instance, "_history_note", ""), changed_by=actor if actor and actor.pk else None)

    if not (status_changed or lines):
        return

    if by_customer:
        # Müşterinin kendi işlemi (örn. «kargoya verdim», «teslim aldım»): yöneticilere haber ver;
        # durum değiştiyse müşteriye de onay niteliğinde tarayıcı bildirimi + e-posta gider.
        if status_changed:
            _inform_customer(instance, old["status"], title, lines, customer_device_url(instance))
        if status_changed and instance.status == S.IN_TRANSIT:
            # Kargoya verildi: admin + (atanmışsa) teknisyene bildirim. Kod oluşturulduysa bildirimde kod görünür.
            extra = " · ".join(x for x in (f"Kargo kodu: {instance.cargo_code}" if instance.cargo_code else "",
                                           instance.cargo_company, instance.cargo_tracking_code) if x)
            notify_device_staff(instance, "Cihaz kargoya verildi",
                                f"{label} — müşteri kargoya verdi" + (f" ({extra})." if extra else "."))
        return

    if status_changed and instance.status == S.RETURN_SHIPPING and not getattr(instance, "_skip_email", False):
        # Geri kargoya verildi: müşteriye «Ürünü teslim aldım» bağlantılı e-posta (kayıt işlendikten sonra).
        transaction.on_commit(lambda: send_return_shipped_email(instance))

    if instance.customer_id:
        url = customer_cargo_url(instance) if (instance.is_cargo and (
            instance.awaiting_arrival or instance.status == S.RETURN_SHIPPING)) else customer_device_url(instance)
        if status_changed and instance.status == S.READY and instance.is_cargo and not instance.is_paid:
            # Onarım bitti, kargoyla gönderilecek: müşteri bildirimden doğrudan online ödeme ekranına gider.
            url = reverse("core:customer_payment", args=[instance.tracking_id])
            total = instance.total_cost
            if total and total > 0:
                lines.append(f"Onarım tutarı: {number_format(total, decimal_pos=2, use_l10n=True, force_grouping=True)} ₺")
            lines.append("Onarım tamamlandı. Kargoyla gönderim için online ödemenizi yapın")
        notify(instance.customer, title or "Servis kaydınızda güncelleme",
               " · ".join([label] + lines), url=url, device=instance)
    else:
        url = customer_device_url(instance)
    # Geri kargoda «Ürünü teslim aldım» bağlantılı özel e-posta zaten gidiyor; diğer tüm durumlarda bilgilendirme e-postası.
    if status_changed and instance.status != S.RETURN_SHIPPING:
        _email_customer(instance, old["status"], title, lines, url)


def _email_customer(instance, old_status, title, lines, url):
    """Durum değişikliği e-postası: kayıt veritabanına işlendikten sonra gönderilir; hata kaydı bozmaz."""
    if getattr(instance, "_skip_email", False):
        return
    title = title or "Servis durumunuz güncellendi"
    lines = list(lines)
    transaction.on_commit(lambda: send_status_update_email(instance, title, old_status, lines, url))


def _inform_customer(instance, old_status, title, lines, url):
    """Müşterinin kendi yaptığı durum değişikliği: tarayıcı/uygulama bildirimi + e-posta (onay)."""
    title = title or "Servis durumunuz güncellendi"
    if instance.customer_id:
        notify(instance.customer, title, " · ".join([f"{instance.tracking_id} · {instance.title}"] + lines),
               url=url, device=instance)
    _email_customer(instance, old_status, title, lines, url)


# --------------------------------------------------------------------------
# Admin bildirim paneli: parça talebi, izin talebi, teknisyenin stok kullanımı
# --------------------------------------------------------------------------
@receiver(post_save, sender=PartRequest)
@_skip_raw
def part_request_created(sender, instance, created, **kwargs):
    if not created or instance.status != PartRequest.Status.PENDING:
        return
    notify_admins(
        instance.technician.display_name,
        f"{instance.device.tracking_id} için “{instance.part.name}” parçası talep etti.",
        url=_admin_url(instance.device), device=instance.device, kind=Notification.Kind.PART_REQUEST,
        actor=instance.technician, part_request=instance)




@receiver(post_save, sender=TechnicianLeave)
@_skip_raw
def leave_created(sender, instance, created, **kwargs):
    if not created or instance.status != TechnicianLeave.Status.PENDING:
        return
    tech = instance.technician
    done = ("delivered", "cancelled", "unrepairable")
    open_jobs = Device.objects.filter(technician=tech).exclude(status__in=done).count()
    body = (f"{instance.start_date:%d.%m.%Y} – {instance.end_date:%d.%m.%Y} tarihleri için "
            f"{instance.get_leave_type_display().lower()} talep etti ({_business_days(instance.start_date, instance.end_date)} iş günü).")
    if open_jobs:
        body += f" Bu tarihlerde {open_jobs} açık işi var."
    notify_admins(tech.display_name, body, url=reverse("admin:core_technicianleave_changelist"),
                  kind=Notification.Kind.LEAVE, actor=tech, leave=instance)


@receiver(post_save, sender=StockMovement)
@_skip_raw
def stock_movement_created(sender, instance, created, **kwargs):
    """Teknisyen bir parçayı cihazda kullanınca adminlere «Stok» bildirimi gider (admin kendi işlemini görmez)."""
    if not created or instance.movement_type != StockMovement.MovementType.OUT or getattr(instance, "_skip_notify", False):
        return
    actor = instance.created_by
    if actor is None or actor.role == User.Role.ADMIN:
        return
    quantity = StockPart.objects.filter(pk=instance.part_id).values_list("quantity", flat=True).first()
    where = f" {instance.device.tracking_id} için" if instance.device_id else ""
    notify_admins(actor.display_name, f"“{instance.part.name}” parçasını{where} kullandı. Stok {quantity} adede düştü.",
                  url=reverse("core:stock_detail", args=[instance.part_id]), device=instance.device,
                  kind=Notification.Kind.STOCK, actor=actor)
