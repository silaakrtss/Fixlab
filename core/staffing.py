"""Personel Yönetimi: teknisyen iş yükü, izin takvimi, izin verme / iptal etme, silme (gerekirse pasife alma)."""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from . import accounts
from .models import AuditLog, Device, StockMovement, TechnicianLeave, User
from .notifications import notify
from .utils import log_action

DONE_STATUSES = (Device.Status.DELIVERED, Device.Status.CANCELLED, Device.Status.UNREPAIRABLE)
OPEN_STATUSES = [s for s in Device.Status.values if s not in DONE_STATUSES]
# Cihaz servise gelmeden (müşteri kargoya vermeden / elden teslim edilmeden) teknisyen atanamaz.
HANDOVER_PENDING = (Device.Status.PRE_REGISTERED, Device.Status.AWAITING_CARGO)


def assign_block_reason(device):
    """Cihaza şu an teknisyen atanamıyorsa nedenini (kullanıcıya gösterilecek metin), atanabiliyorsa boş metin döndürür."""
    if device.status in HANDOVER_PENDING:
        if device.is_cargo:
            return "Müşteri cihazı kargoya verdiğinde teknisyen atayabilirsiniz."
        return "Cihaz elden teslim alındığında teknisyen atayabilirsiniz. Önce “Cihazı teslim aldım” bilgisini kaydedin."
    if device.status in DONE_STATUSES:
        return "Kapanmış (teslim edilmiş, iptal edilmiş ya da onarılamayan) kayıt yeniden atanamaz."
    return ""
# Takvimde görünen / iş atamasını engelleyen izinler yalnızca onaylı izinlerdir.
ACTIVE_LEAVE = TechnicianLeave.Status.APPROVED


def business_days(start, end):
    """İki tarih (dahil) arasındaki hafta içi gün sayısı."""
    days, day = 0, start
    while day <= end:
        days += day.weekday() < 5
        day += timedelta(days=1)
    return days


def format_span(start, end, full=False):
    """06.10 – 10.10.2025  (yıllar farklıysa ya da full=True ise iki tarih de yıllı)."""
    if full or start.year != end.year:
        return f"{start:%d.%m.%Y} – {end:%d.%m.%Y}"
    return f"{start:%d.%m} – {end:%d.%m.%Y}"


def is_on_leave(user, today=None):
    today = today or timezone.localdate()
    return TechnicianLeave.objects.filter(technician=user, status=ACTIVE_LEAVE,
                                          start_date__lte=today, end_date__gte=today).exists()


def technician_rows(today=None):
    """Personel Yönetimi tablosu: her teknisyen için açık iş, tamamlanan, durum ve izin bilgisi."""
    today = today or timezone.localdate()
    techs = list(User.objects.filter(role=User.Role.TECHNICIAN)
                 .annotate(open_jobs=Count("assigned_devices", filter=Q(assigned_devices__status__in=OPEN_STATUSES)),
                           done_jobs=Count("assigned_devices", filter=Q(assigned_devices__status=Device.Status.DELIVERED)))
                 .order_by("-is_active", "first_name", "last_name", "username"))
    leaves = {}
    for leave in (TechnicianLeave.objects.filter(technician__in=techs, status=ACTIVE_LEAVE, end_date__gte=today)
                  .order_by("start_date")):
        leaves.setdefault(leave.technician_id, []).append(leave)
    rows = []
    for tech in techs:
        mine = leaves.get(tech.pk, [])
        current = next((lv for lv in mine if lv.start_date <= today), None)
        upcoming = next((lv for lv in mine if lv.start_date > today), None)
        if current:
            leave_text = f"{current.get_leave_type_display()} · {format_span(current.start_date, current.end_date)}"
        elif upcoming:
            leave_text = f"Planlı · {format_span(upcoming.start_date, upcoming.end_date)}"
        else:
            leave_text = ""
        if not tech.is_active:
            state, label = "off", "Pasif"
        elif current:
            state, label = "leave", "İzinde"
        else:
            state, label = "active", "Aktif"
        rows.append({"tech": tech, "open_jobs": tech.open_jobs, "done_jobs": tech.done_jobs,
                     "state": state, "state_label": label, "leave_text": leave_text})
    return rows


def leave_calendar(today=None):
    """İzin takvimi: devam eden ve planlı (onaylı) izinler, başlangıç tarihine göre."""
    today = today or timezone.localdate()
    items = list(TechnicianLeave.objects.filter(status=ACTIVE_LEAVE, end_date__gte=today,
                                                technician__role=User.Role.TECHNICIAN)
                 .select_related("technician").order_by("start_date", "id"))
    for leave in items:
        leave.span = format_span(leave.start_date, leave.end_date, full=True)
        leave.workdays = business_days(leave.start_date, leave.end_date)
        leave.ongoing = leave.start_date <= today
    return items


def find_overlap(technician, start, end, exclude_pk=None):
    """Aynı teknisyenin bu tarihlerle çakışan onaylı ya da bekleyen izni (yoksa None)."""
    qs = TechnicianLeave.objects.filter(technician=technician, start_date__lte=end, end_date__gte=start,
                                        status__in=(TechnicianLeave.Status.APPROVED, TechnicianLeave.Status.PENDING))
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


def open_job_count(technician):
    return Device.objects.filter(technician=technician, status__in=OPEN_STATUSES).count()


def _span_label(leave):
    return f"{leave.start_date:%d.%m.%Y} – {leave.end_date:%d.%m.%Y}"


@transaction.atomic
def grant_leave(technician, admin, data, request=None):
    leave = TechnicianLeave.objects.create(
        technician=technician, leave_type=data["leave_type"], start_date=data["start_date"],
        end_date=data["end_date"], description=data.get("description", ""),
        status=TechnicianLeave.Status.APPROVED, reviewed_by=admin, reviewed_at=timezone.now())
    notify(technician, "Size izin tanımlandı",
           f"{leave.get_leave_type_display()} · {_span_label(leave)}", url=reverse("core:technician_dashboard"))
    log_action(AuditLog.Action.LEAVE, user=admin, request=request, target=leave,
               description=f"İzin verildi: {technician.display_name} ({_span_label(leave)})")
    return leave


@transaction.atomic
def update_leave(leave, admin, data, request=None):
    leave.leave_type = data["leave_type"]
    leave.start_date = data["start_date"]
    leave.end_date = data["end_date"]
    leave.description = data.get("description", "")
    leave.save(update_fields=["leave_type", "start_date", "end_date", "description"])
    notify(leave.technician, "İzniniz güncellendi",
           f"{leave.get_leave_type_display()} · {_span_label(leave)}", url=reverse("core:technician_dashboard"))
    log_action(AuditLog.Action.LEAVE, user=admin, request=request, target=leave,
               description=f"İzin güncellendi: {leave.technician.display_name} ({_span_label(leave)})")
    return leave


@transaction.atomic
def cancel_leave(leave, admin, request=None):
    leave.status = TechnicianLeave.Status.CANCELLED
    leave.reviewed_by = admin
    leave.reviewed_at = timezone.now()
    leave.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    notify(leave.technician, "İzniniz iptal edildi", _span_label(leave), url=reverse("core:technician_dashboard"))
    log_action(AuditLog.Action.LEAVE, user=admin, request=request, target=leave,
               description=f"İzin iptal edildi: {leave.technician.display_name} ({_span_label(leave)})")
    return leave


def has_history(person):
    """Teknisyenin silinirse kaybolacak / sahipsiz kalacak geçmiş kayıtları var mı?"""
    return (Device.objects.filter(technician=person).exists()
            or TechnicianLeave.objects.filter(technician=person).exists()
            or person.part_requests.exists()
            or StockMovement.objects.filter(created_by=person).exists())


@transaction.atomic
def delete_or_archive(person, admin, request=None):
    """Geçmişi olmayan teknisyen silinir; geçmişi olan silinmez, pasife alınır. 'deleted' | 'archived' | 'already'."""
    name, username = person.display_name, person.username
    if has_history(person):
        if not person.is_active:
            return "already"
        person.is_active = False
        person.save(update_fields=["is_active"])
        log_action(AuditLog.Action.UPDATE, user=admin, request=request, target=person,
                   description=f"Teknisyen geçmiş kaydı olduğu için silinmek yerine pasife alındı: {username}")
        return "archived"
    log_action(AuditLog.Action.DELETE, user=admin, request=request, target=person,
               description=f"Teknisyen silindi: {name} ({username})")
    accounts.delete_account(person)  # LDAP açıkken dizin kaydı da silinir (hata olursa tüm işlem geri alınır)
    person.delete()
    return "deleted"


@transaction.atomic
def assign_device(device, technician, admin, request=None, note=""):
    """Admin: cihazı teknisyene atar. İş teknisyenin paneline düşer ve teknisyene bildirim gider
    (bildirimi cihaz kaydındaki sinyal üretir). Pasif ya da şu an izinde olan teknisyene atanamaz.
    note: yöneticinin teknisyene yazdığı isteğe bağlı not; bildirimde ve işlem kaydında görünür."""
    if technician.role != User.Role.TECHNICIAN:
        raise ValidationError("Yalnızca teknisyen hesabına iş atanabilir.")
    reason = assign_block_reason(device)
    if reason:
        raise ValidationError(reason)
    device.technician = technician
    device.clean()
    device._actor = admin
    device._assign_note = (note or "").strip()
    device.save()
    return device


# Türkçe alfabe sırası (a b c ç d e f g ğ h ı i j k l m n o ö p r s ş t u ü v y z); q w x sona yakın yerleşir.
_TR_ALPHABET = "abcçdefgğhıijklmnoöpqrsştuüvwxyz"
_TR_ORDER = {ch: i for i, ch in enumerate(_TR_ALPHABET)}


def turkish_sort_key(text):
    """Metni Türkçe alfabe sırasına göre karşılaştırılabilir hale getirir (İ/I, ı/i ayrımı doğru)."""
    text = (text or "").replace("İ", "i").replace("I", "ı").lower()
    return [_TR_ORDER.get(ch, 100 + ord(ch)) for ch in text]


def assignable_technicians(today=None):
    """«Teknisyene ata» listesi: pasif olmayan teknisyenler, açık iş sayısına göre azdan çoğa.
    Eşit iş sayısında ada göre A→Z (Türkçe sıralı); izindeki teknisyenler (seçilemez) listenin sonundadır.

    Her satır: tech, open_jobs (açık iş sayısı), specialization, on_leave, available, is_default.
    Varsayılan seçim, listenin ilk uygun (izinde olmayan) teknisyenidir, yani en az açık işi olandır."""
    rows = [{"tech": r["tech"], "open_jobs": r["open_jobs"], "specialization": r["tech"].specialization,
             "on_leave": r["state"] == "leave", "available": r["state"] == "active", "is_default": False}
            for r in technician_rows(today) if r["state"] != "off"]
    rows.sort(key=lambda r: (not r["available"], r["open_jobs"], turkish_sort_key(r["tech"].first_name),
                             turkish_sort_key(r["tech"].last_name), turkish_sort_key(r["tech"].username)))
    if rows and rows[0]["available"]:
        rows[0]["is_default"] = True
    return rows
