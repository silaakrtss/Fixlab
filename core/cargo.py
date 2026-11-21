"""Kargo akışı: müşteri → servis ve servis → müşteri gönderiminde kod oluşturma, teslim ve bildirimler."""
import secrets
from decimal import Decimal, InvalidOperation

from django.core import signing
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import AuditLog, Device, User
from .notifications import notify, notify_device_staff, technician_device_url
from .utils import log_action

S = Device.Status

# Karışabilecek karakterler (0/O, 1/I/L) kodda kullanılmaz.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
RECEIPT_SALT = "fixlab.receipt-confirm"
RECEIPT_MAX_AGE = 60 * 24 * 3600  # teslim onay bağlantısı 60 gün geçerli
APPROVAL_SALT = "fixlab.repair-approval"
APPROVAL_MAX_AGE = 30 * 24 * 3600  # e-postadaki onarım onayı bağlantısı 30 gün geçerli
APPROVAL_MIN = 10  # müşteriye iletilecek açıklama en az bu kadar karakter

# Teknisyenin cihaz sayfasından seçebildiği onarım aşamaları (kargo adımları ayrı düğmelerle yönetilir).
PIPELINE = (S.DIAGNOSING, S.WAITING_APPROVAL, S.WAITING_PART, S.REPAIRING, S.TESTING, S.READY)
PIPELINE_TARGETS = PIPELINE + (S.UNREPAIRABLE,)


class CargoError(Exception):
    """Kullanıcıya gösterilebilir, işlem yapılamadığını anlatan hata."""


def new_code(prefix="KRG"):
    """KRG-7H4K9QXP gibi kargo kodu üretir; başka kayıtta kullanılmıyorsa döndürür."""
    for _ in range(20):
        code = f"{prefix}-" + "".join(secrets.choice(_ALPHABET) for _ in range(8))
        if not (Device.objects.filter(cargo_code=code).exists() or Device.objects.filter(return_cargo_code=code).exists()):
            return code
    raise CargoError("Kod üretilemedi, lütfen tekrar deneyin.")


def _label(device):
    return f"{device.brand} {device.model}".strip() or device.tracking_id


def _lock(device):
    return Device.objects.select_for_update().get(pk=device.pk)   # ilişkiler gerektiğinde ayrıca okunur


# --- Müşteri → servis ---------------------------------------------------------------
def create_customer_cargo_code(device, user, request=None):
    """Müşteri «Kod oluştur»a bastı: kod üretilir, kayıt «Kargoda» olur, admin + teknisyene «kargoya verildi» gider."""
    with transaction.atomic():
        device = _lock(device)
        if not device.is_cargo:
            raise CargoError("Bu kayıt için kargo süreci yok.")
        if device.status != S.AWAITING_CARGO:
            raise CargoError("Bu kayıt için kargo kodu artık oluşturulamaz.")
        device.cargo_code = new_code("KRG")
        device.cargo_code_created_at = timezone.now()
        device.cargo_sent_at = device.cargo_code_created_at
        device.status = S.IN_TRANSIT
        device._actor = user
        device._history_note = f"Müşteri kargo kodu oluşturdu: {device.cargo_code}"
        device.save()   # sinyal: admin + teknisyene «Cihaz kargoya verildi» bildirimi
    log_action(AuditLog.Action.UPDATE, user=user, request=request, target=device,
               description=f"Kargo kodu oluşturuldu: {device.tracking_id} ({device.cargo_code})")
    return device


def report_handover_failed(device, user, request=None):
    """«Kod oluştu, teslim sağlanmadı»: kod yanlışlıkla oluşturulduysa kayıt «Kargo bekleniyor»a döner,
    kod geçersiz olur ve admin + teknisyene bildirim gider. Müşteri gerekirse yeni kod oluşturabilir."""
    with transaction.atomic():
        device = _lock(device)
        if device.status != S.IN_TRANSIT or not device.cargo_code:
            raise CargoError("Bu kayıt için bildirilecek bir kargo kodu yok.")
        old_code = device.cargo_code
        device.cargo_code = ""
        device.cargo_code_created_at = None
        device.cargo_sent_at = None
        device.cargo_company = ""
        device.cargo_tracking_code = ""
        device.status = S.AWAITING_CARGO
        device._actor = user
        device._history_note = f"Müşteri: kod oluşturuldu ({old_code}) ancak kargo şirketine teslim sağlanmadı"
        device.save()
        notify_device_staff(
            device, "Kargo teslimi gerçekleşmedi",
            f"{_label(device)} — müşteri kargo kodu oluşturdu ({old_code}) ancak ürün kargo şirketine teslim edilmedi. "
            f"Kayıt «Kargo bekleniyor» durumuna döndü.")
    log_action(AuditLog.Action.UPDATE, user=user, request=request, target=device,
               description=f"Kargo kodu iptal: kod oluşturuldu, teslim sağlanmadı ({device.tracking_id}, {old_code})")
    return device


# --- Elden teslim: admin teslim aldığını girer -------------------------------------------------------
def admin_receive_in_person(device, admin, delivered_by="", note="", request=None):
    """Müşteri cihazı servise kendisi getirdi: admin «Teslim alındı» bilgisini girer. Kayıt «Teslim alındı» olur,
    müşteriye bildirim gider (sinyal); teknisyen atanmışsa teknisyene de haber verilir."""
    delivered_by = " ".join((delivered_by or "").split())[:80]
    note = " ".join((note or "").split())[:140]
    with transaction.atomic():
        device = _lock(device)
        if device.is_cargo:
            raise CargoError("Bu kayıt kargo ile gönderiliyor; teslim alma işlemi kargo adımlarıyla yapılır.")
        if device.status != S.PRE_REGISTERED:
            raise CargoError("Bu kayıt için elden teslim alma işlemi artık yapılamaz.")
        parts = ["Elden teslim alındı"]
        if delivered_by:
            parts.append(f"teslim eden: {delivered_by}")
        if note:
            parts.append(f"not: {note}")
        device.status = S.RECEIVED
        device._actor = admin
        device._history_note = " · ".join(parts)[:255]
        device.save()   # sinyal: müşteriye «Cihazınız servise ulaştı» bildirimi
        tech = device.technician
        if tech is not None and tech.is_active and tech.role == User.Role.TECHNICIAN:
            notify(tech, "Cihaz teslim alındı",
                   f"{_label(device)} — müşteri cihazı elden teslim etti" + (f" ({note})." if note else "."),
                   url=technician_device_url(device), device=device)
    log_action(AuditLog.Action.UPDATE, user=admin, request=request, target=device,
               description=f"Elden teslim alındı: {device.tracking_id}" + (f" — {note}" if note else ""))
    return device


# --- Teknisyen ---------------------------------------------------------------------
def technician_receive_cargo(device, user, request=None):
    """Kargo servise ulaştı: «Kargo teslim alındı». Bundan sonra işleme başlanabilir."""
    with transaction.atomic():
        device = _lock(device)
        if device.status != S.IN_TRANSIT:
            raise CargoError("Yalnızca «Kargoda» olan kayıt teslim alınabilir.")
        device.status = S.RECEIVED
        device._actor = user
        device._history_note = "Kargo teslim alındı"
        device.save()   # sinyal: müşteriye «Cihazınız servise ulaştı» bildirimi
    log_action(AuditLog.Action.UPDATE, user=user, request=request, target=device,
               description=f"Kargo teslim alındı: {device.tracking_id}")
    return device


def technician_start_work(device, user, request=None):
    with transaction.atomic():
        device = _lock(device)
        if device.status != S.RECEIVED:
            raise CargoError("İşleme başlamak için önce cihazın teslim alınmış olması gerekir.")
        device.status = S.DIAGNOSING
        device._actor = user
        device._history_note = "İşleme başlandı"
        device.save()
    log_action(AuditLog.Action.UPDATE, user=user, request=request, target=device,
               description=f"İşleme başlandı: {device.tracking_id}")
    return device


def technician_set_status(device, user, new_status, request=None):
    if new_status not in PIPELINE_TARGETS:
        raise CargoError("Geçersiz durum.")
    with transaction.atomic():
        device = _lock(device)
        if device.status not in PIPELINE:
            raise CargoError("Bu aşamada durum bu ekrandan değiştirilemez.")
        if device.status == new_status:
            raise CargoError("Kayıt zaten bu durumda.")
        if new_status == S.WAITING_APPROVAL:
            raise CargoError("Müşteri onayı, müşteriye iletilecek açıklamayla birlikte istenir.")
        # Müşteri karar vermeden onay aşamasından çıkıldıysa bekleyen talep geçersiz olur (müşteri artık yanıtlayamaz)
        device.approvals.filter(status="pending").update(status="cancelled", decided_at=timezone.now(),
                                                         decided_via="teknisyen durumu değiştirdi")
        device.status = new_status
        device._actor = user
        device._history_note = "Teknisyen güncelledi"
        device.save()
    log_action(AuditLog.Action.STATUS_CHANGE, user=user, request=request, target=device,
               description=f"Durum güncellendi: {device.tracking_id} → {device.get_status_display()}")
    return device


NOTE_MAX = 1000


def add_technician_note(device, user, text, customer_visible=False, request=None):
    """Teknisyen işlem notu: kaydedilir, yöneticilere «Not» bildirimi gider; müşteriye açıksa müşteriye de bildirilir."""
    from .models import DeviceNote, Notification
    from .notifications import customer_device_url, notify_admins
    text = " ".join((text or "").split())[:NOTE_MAX]
    if not text:
        raise CargoError("İşlem notu boş olamaz.")
    note = DeviceNote.objects.create(device=device, author=user, text=text, customer_visible=customer_visible)
    label = f"{device.tracking_id} · {device.title}"
    short = text if len(text) <= 160 else text[:159].rstrip() + "…"
    notify_admins(user.display_name, f"{label} — {'Müşteriye açık not' if customer_visible else 'İç not'}: {short}",
                  url=reverse("core:admin_device_detail", args=[device.pk]), device=device,
                  kind=Notification.Kind.NOTE, actor=user)
    if customer_visible and device.customer_id:
        notify(device.customer, "Servis notu", f"{label} · {short}", url=customer_device_url(device), device=device)
    # OTHER: notun kendisi işlem geçmişinde ayrıca gösterilir; UPDATE olsaydı geçmişte iki kez görünürdü.
    log_action(AuditLog.Action.OTHER, user=user, request=request, target=device,
               description=f"İşlem notu eklendi ({'müşteriye açık' if customer_visible else 'iç not'}): {device.tracking_id}")
    return note


def technician_use_part(device, user, part, quantity, request=None):
    """Teknisyen depodan parça kullanır: stoktan düşer, cihaza parça olarak eklenir, parça maliyeti güncellenir.
    Yöneticilere «Stok» bildirimi stok hareketi sinyalinden gider."""
    from .models import DevicePartUsage
    from .stock import StockError, use_stock
    if device.status not in PIPELINE:
        raise CargoError("Parça kullanımı yalnızca cihaz onarım aşamasındayken kaydedilebilir.")
    if not part.is_active:
        raise CargoError("Bu parça artık depoda kullanılmıyor.")
    if quantity < 1 or quantity > 999:
        raise CargoError("Kullanılacak adet 1 ile 999 arasında olmalıdır.")
    with transaction.atomic():
        try:
            use_stock(part, quantity, device, user, note=f"{device.tracking_id} için kullanıldı", request=request)
        except StockError as exc:
            raise CargoError(str(exc))
        usage = DevicePartUsage.objects.create(device=device, part=part, quantity=quantity, added_by=user)
        device.recalculate_parts_cost()
    return usage


FEE_MAX = Decimal("1000000")
FEE_MAX_ITEMS = 10


def parse_money(raw, field="Tutar"):
    """«1.250,50» / «1250.50» / «1250» → Decimal. Boş → 0. Geçersiz ya da negatifse CargoError."""
    text = (raw or "").strip().replace(" ", "").replace("₺", "")
    if not text:
        return Decimal("0.00")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        value = Decimal(text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise CargoError(f"{field} geçerli bir tutar olmalıdır.")
    if value < 0 or value >= FEE_MAX:
        raise CargoError(f"{field} 0 ile {FEE_MAX:,.0f} ₺ arasında olmalıdır.".replace(",", "."))
    return value


def set_repair_charges(device, user, labor, items, request=None):
    """Teknisyen onarım ücretini girer: işçilik + ek kalemler (eski ek kalemlerin yerine geçer).
    Ödemesi alınmış kayıtta ücret değiştirilemez; toplam (parçalar dahil) sıfır olamaz."""
    from .models import DeviceCharge
    if device.is_paid:
        raise CargoError("Ödemesi alınmış kaydın ücreti değiştirilemez.")
    if len(items) > FEE_MAX_ITEMS:
        raise CargoError(f"En fazla {FEE_MAX_ITEMS} ek kalem girebilirsiniz.")
    with transaction.atomic():
        device = _lock(device)
        device.labor_cost = labor
        device.save(update_fields=["labor_cost", "updated_at"])
        device.charges.all().delete()
        for label, amount in items:
            DeviceCharge.objects.create(device=device, label=label, amount=amount, created_by=user)
        total = device.total_cost
        if total <= 0:
            raise CargoError("Onarım tutarı sıfır olamaz. İşçilik ya da ek kalem tutarı girin.")
    log_action(AuditLog.Action.UPDATE, user=user, request=request, target=device,
               description=f"Onarım ücreti girildi: {device.tracking_id} · {total} ₺")
    return device


def create_return_cargo_code(device, user, company="", tracking="", request=None):
    """Teknisyen «Geri kargo kodu oluştur»: kayıt «Geri kargoda» olur, müşteriye bildirim ve e-posta gider."""
    with transaction.atomic():
        device = _lock(device)
        if not device.is_cargo:
            raise CargoError("Bu kayıt kargo ile gönderilmiyor; cihaz müşteriye elden teslim edilir.")
        if device.status != S.READY:
            raise CargoError("Geri kargo kodu yalnızca «Teslime hazır» kayıtlar için oluşturulur.")
        now = timezone.now()
        device.return_cargo_code = new_code("GKR")
        device.return_cargo_code_created_at = now
        if company:
            device.return_cargo_company = company
        if tracking:
            device.return_cargo_tracking_code = tracking
        device.status = S.RETURN_SHIPPING
        device._actor = user
        device._history_note = f"Geri kargo kodu oluşturuldu: {device.return_cargo_code}"
        device.save()   # sinyal: müşteriye bildirim + e-posta («Ürünü teslim aldım» bağlantılı)
    log_action(AuditLog.Action.UPDATE, user=user, request=request, target=device,
               description=f"Geri kargo kodu oluşturuldu: {device.tracking_id} ({device.return_cargo_code})")
    return device


# --- Müşteri onarım onayı ---------------------------------------------------------------
def request_customer_approval(device, user, message, request=None):
    """Teknisyen «Müşteri onayı bekliyor»: açıklama müşteriye iletilir (uygulama bildirimi + e-posta, e-postada
    hesap gerektirmeyen onay bağlantısı). Kayıt «Müşteri onayı bekliyor» olur; karar teknisyene bildirilir.
    Müşteri önceki talebi reddettiyse aynı aşamada yeni bir talep (ör. yeni teklif) gönderilebilir."""
    from .models import DeviceApproval, DeviceNote
    from .notifications import notify_admins
    text = " ".join((message or "").split())[:NOTE_MAX]
    if len(text) < APPROVAL_MIN:
        raise CargoError("Müşteri onayı istemek için «İşlem notu» alanına müşteriye iletilecek açıklamayı yazın "
                         "(yapılacak işlem, tahmini ücret ve süre gibi).")
    with transaction.atomic():
        device = _lock(device)
        if device.status not in PIPELINE:
            raise CargoError("Bu aşamada müşteri onayı istenemez.")
        if device.approvals.filter(status=DeviceApproval.Status.PENDING).exists():
            raise CargoError("Bu kayıt için müşteriden zaten onay bekleniyor.")
        old_status = device.status
        approval = DeviceApproval.objects.create(device=device, message=text, requested_by=user)
        DeviceNote.objects.create(device=device, author=user, text=f"Onay talebi: {text}", customer_visible=True)
        if old_status != S.WAITING_APPROVAL:
            device.status = S.WAITING_APPROVAL
            device._actor = user
            device._history_note = "Müşteri onayı istendi"
            device._approval = approval  # sinyal genel durum bildirimini atlar; müşteriye aşağıdaki özel bildirim gider
            device.save()
        transaction.on_commit(lambda: _inform_customer_of_approval(device, approval, old_status))
    notify_admins("Müşteri onayı istendi", f"{device.tracking_id} · {_label(device)} — {_short(text)}",
                  url=reverse("core:admin_device_detail", args=[device.pk]), device=device, actor=user)
    log_action(AuditLog.Action.STATUS_CHANGE, user=user, request=request, target=device,
               description=f"Müşteri onayı istendi: {device.tracking_id}")
    return approval


def _short(text, n=160):
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def approval_url(approval):
    """E-postadaki onay sayfası (oturum gerektirmez; imzalı, süreli)."""
    return reverse("core:approval_decide", args=[signing.dumps({"a": approval.pk}, salt=APPROVAL_SALT)])


def approval_from_token(token):
    from .models import DeviceApproval
    try:
        data = signing.loads(token, salt=APPROVAL_SALT, max_age=APPROVAL_MAX_AGE)
        return DeviceApproval.objects.select_related("device", "device__customer", "device__technician").get(pk=data["a"])
    except (signing.BadSignature, DeviceApproval.DoesNotExist, KeyError, TypeError):
        return None


def _inform_customer_of_approval(device, approval, old_status):
    from .notifications import customer_device_url
    from .utils import send_status_update_email
    title = "Onarım için onayınız bekleniyor"
    if device.customer_id:
        notify(device.customer, title, f"{device.tracking_id} · {_label(device)} · {_short(approval.message)}",
               url=customer_device_url(device) + "#onay", device=device)
    send_status_update_email(device, title, old_status if old_status != S.WAITING_APPROVAL else "",
                             [f"Servisin açıklaması: {approval.message}",
                              "Onarıma devam edilmesi için aşağıdaki bağlantıdan onay verin ya da reddedin."],
                             approval_url(approval), action_label="Onayla / Reddet")


def decide_customer_approval(approval, approve, note="", actor=None, via="uygulama", request=None):
    """Müşteri onay talebini onaylar ya da reddeder; teknisyene (ve yöneticilere) bildirim gider.
    Kayıt «Müşteri onayı bekliyor» durumunda kalır; teknisyen karara göre bir sonraki aşamayı seçer."""
    from .models import DeviceApproval
    note = " ".join((note or "").split())[:500]
    with transaction.atomic():
        approval = DeviceApproval.objects.select_for_update().get(pk=approval.pk)
        device = Device.objects.select_related("technician").get(pk=approval.device_id)
        if approval.status != DeviceApproval.Status.PENDING:
            raise CargoError("Bu onay talebi için daha önce karar verilmiş ya da talep artık geçerli değil.")
        if device.status != S.WAITING_APPROVAL:
            raise CargoError("Bu kayıt şu an onay beklemiyor.")
        approval.status = DeviceApproval.Status.APPROVED if approve else DeviceApproval.Status.REJECTED
        approval.customer_note = note
        approval.decided_at = timezone.now()
        approval.decided_via = via
        approval.save(update_fields=["status", "customer_note", "decided_at", "decided_via"])
        title = "Müşteri onarımı onayladı" if approve else "Müşteri onarımı onaylamadı"
        body = f"{device.tracking_id} · {_label(device)}" + (f" — Not: {_short(note)}" if note else "")
        notify_device_staff(device, title, body, actor=actor if actor is not None and actor.pk else None)
    log_action(AuditLog.Action.STATUS_CHANGE, user=actor, request=request, target=device,
               description=f"Müşteri onay talebini {'onayladı' if approve else 'reddetti'} ({via}): {device.tracking_id}")
    return approval


# --- Müşteri teslim onayı --------------------------------------------------------------
def receipt_token(device):
    return signing.dumps({"d": device.pk, "c": device.return_cargo_code}, salt=RECEIPT_SALT)


def device_from_receipt_token(token):
    """Geçerli bağlantıdan cihazı döndürür; bozuk / süresi geçmiş / başka kodlu bağlantıda None."""
    try:
        data = signing.loads(token, salt=RECEIPT_SALT, max_age=RECEIPT_MAX_AGE)
        device = Device.objects.select_related("customer", "technician").get(pk=data["d"])
    except (signing.BadSignature, Device.DoesNotExist, KeyError, TypeError):
        return None
    if device.return_cargo_code != data.get("c"):
        return None
    return device


def confirm_delivery(device, actor=None, request=None, via="uygulama"):
    """Müşteri «Ürünü teslim aldım»: kayıt «Teslim edildi» olur, admin + teknisyene bildirim gider."""
    with transaction.atomic():
        device = _lock(device)
        if device.status != S.RETURN_SHIPPING:
            raise CargoError("Bu kayıt için teslim onayı beklenmiyor.")
        device.status = S.DELIVERED
        device.return_received_at = timezone.now()
        device._actor = actor if actor is not None else device.customer
        device._history_note = f"Müşteri ürünü teslim aldığını onayladı ({via})"
        device.save()
        notify_device_staff(device, "Ürün müşteriye teslim edildi",
                            f"{_label(device)} — müşteri ürünü teslim aldığını onayladı.")
    log_action(AuditLog.Action.STATUS_CHANGE, user=actor, request=request, target=device,
               description=f"Müşteri teslim aldığını onayladı ({via}): {device.tracking_id}")
    return device
