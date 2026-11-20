"""Stok işlemleri: giriş, düzeltme, çıkış ve silme. Adet değişimi her zaman bir StockMovement kaydı bırakır."""
from django.db import transaction
from django.db.models import F

from .models import AuditLog, StockMovement, StockPart
from .utils import log_action


class StockError(Exception):
    """Kullanıcıya gösterilebilir stok hatası."""


def _refresh(part):
    part.refresh_from_db(fields=["quantity", "updated_at"])


@transaction.atomic
def add_stock(part, quantity, user, note="", request=None, movement_type=StockMovement.MovementType.IN):
    StockPart.objects.filter(pk=part.pk).update(quantity=F("quantity") + quantity)
    movement = StockMovement.objects.create(part=part, movement_type=movement_type, quantity=quantity,
                                            note=(note or "")[:255], created_by=user)
    _refresh(part)
    log_action(AuditLog.Action.STOCK_CHANGE, user=user, request=request, target=part,
               description=f"Stok girişi: {part.code} +{quantity} (yeni stok {part.quantity})")
    return movement


@transaction.atomic
def adjust_stock(part, new_quantity, user, note="", request=None):
    """Adedi doğrudan değiştirir; farkı «Sayım düzeltmesi» hareketi olarak kaydeder."""
    current = StockPart.objects.select_for_update().get(pk=part.pk).quantity
    diff = new_quantity - current
    if diff == 0:
        return None
    StockPart.objects.filter(pk=part.pk).update(quantity=new_quantity)
    movement = StockMovement.objects.create(part=part, movement_type=StockMovement.MovementType.ADJUST, quantity=diff,
                                            note=(note or "Parça düzenlenirken adet güncellendi")[:255], created_by=user)
    _refresh(part)
    log_action(AuditLog.Action.STOCK_CHANGE, user=user, request=request, target=part,
               description=f"Stok düzeltmesi: {part.code} {diff:+d} (yeni stok {part.quantity})")
    return movement


@transaction.atomic
def use_stock(part, quantity, device, used_by, note="", request=None, notify_admins_flag=True):
    """Parçayı cihaz için kullanır (stoktan düşer). Yetersizse StockError."""
    updated = StockPart.objects.filter(pk=part.pk, quantity__gte=quantity).update(quantity=F("quantity") - quantity)
    if not updated:
        raise StockError(f"«{part.name}» için stokta yeterli adet yok.")
    movement = StockMovement(part=part, movement_type=StockMovement.MovementType.OUT, quantity=quantity,
                             device=device, note=(note or "")[:255], created_by=used_by)
    movement._skip_notify = not notify_admins_flag
    movement.save()
    _refresh(part)
    log_action(AuditLog.Action.STOCK_CHANGE, user=used_by, request=request, target=part,
               description=f"Stok çıkışı: {part.code} -{quantity} ({device.tracking_id if device else '—'})")
    return movement


def delete_or_archive(part, user, request=None):
    """Hiç kullanılmamış parça silinir; cihaz/talep geçmişi olan parça arşivlenir (listeden kalkar). 'deleted'/'archived' döner."""
    if part.usages.exists() or part.requests.exists():
        part.is_active = False
        part.save(update_fields=["is_active", "updated_at"])
        log_action(AuditLog.Action.DELETE, user=user, request=request, target=part,
                   description=f"Stok parçası arşivlendi: {part.code}")
        return "archived"
    code = part.code
    log_action(AuditLog.Action.DELETE, user=user, request=request, target=part,
               description=f"Stok parçası silindi: {code}")
    part.delete()
    return "deleted"
