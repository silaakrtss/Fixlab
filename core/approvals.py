"""Admin bildirim panelindeki Onayla / Reddet işlemleri (parça ve izin talepleri)."""
from django.db import transaction
from django.utils import timezone

from .models import AuditLog, DevicePartUsage, PartRequest, TechnicianLeave
from .notifications import notify
from .stock import StockError, use_stock
from .utils import log_action
from django.urls import reverse


@transaction.atomic
def decide_part_request(part_request, admin, approve, request=None):
    pr = (PartRequest.objects.select_for_update().select_related("part", "device", "technician")
          .get(pk=part_request.pk))
    if pr.status != PartRequest.Status.PENDING:
        raise StockError("Bu talep daha önce sonuçlandırılmış.")
    label = f"{pr.device.tracking_id} · “{pr.part.name}” ({pr.quantity} adet)"
    if approve:
        # Talebi yapan teknisyen adına stoktan düşülür; cihazın parça maliyeti güncellenir.
        use_stock(pr.part, pr.quantity, pr.device, pr.technician, note="Parça talebi onaylandı",
                  request=request, notify_admins_flag=False)
        DevicePartUsage.objects.create(device=pr.device, part=pr.part, quantity=pr.quantity, added_by=pr.technician)
        pr.device.recalculate_parts_cost()
    pr.status = PartRequest.Status.APPROVED if approve else PartRequest.Status.REJECTED
    pr.reviewed_by = admin
    pr.reviewed_at = timezone.now()
    pr.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    notify(pr.technician, "Parça talebiniz onaylandı" if approve else "Parça talebiniz reddedildi", label,
           url=reverse("core:technician_dashboard"))
    log_action(AuditLog.Action.STOCK_CHANGE, user=admin, request=request, target=pr,
               description=f"Parça talebi {'onaylandı' if approve else 'reddedildi'}: {label}")
    return pr


@transaction.atomic
def decide_leave(leave, admin, approve, request=None):
    lv = TechnicianLeave.objects.select_for_update().select_related("technician").get(pk=leave.pk)
    if lv.status != TechnicianLeave.Status.PENDING:
        raise StockError("Bu talep daha önce sonuçlandırılmış.")
    lv.status = TechnicianLeave.Status.APPROVED if approve else TechnicianLeave.Status.REJECTED
    lv.reviewed_by = admin
    lv.reviewed_at = timezone.now()
    lv.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    span = f"{lv.start_date:%d.%m.%Y} – {lv.end_date:%d.%m.%Y}"
    notify(lv.technician, "İzin talebiniz onaylandı" if approve else "İzin talebiniz reddedildi", span,
           url=reverse("core:technician_dashboard"))
    log_action(AuditLog.Action.LEAVE, user=admin, request=request, target=lv,
               description=f"İzin talebi {'onaylandı' if approve else 'reddedildi'}: {lv.technician.display_name} ({span})")
    return lv
