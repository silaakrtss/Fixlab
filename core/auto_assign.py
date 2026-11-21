"""Otomatik teknisyen ataması (AI + iş yükü).

Kural:
1. Cihaz atanabilir olduğunda (yönetici servis kabulünde oluşturdu / müşteri kargoya verdi / elden teslim alındı)
   ve henüz teknisyeni yoksa arka planda çalışır.
2. Yapay zekâ arıza açıklamasına göre servisteki uzmanlık alanlarından en uygununu seçer.
3. O uzmanlık alanına sahip (aktif, izinde olmayan) teknisyenlerden AÇIK İŞİ EN AZ olana atanır.
4. Uygun uzman yoksa ya da yapay zekâ yanıt vermezse: tüm uygun teknisyenlerden açık işi en az olana atanır.
5. Yöneticiye bildirim gider; atamayı cihaz detayındaki «Teknisyeni değiştir» ile her zaman değiştirebilir.
"""
import logging
import threading

from django.conf import settings
from django.db import close_old_connections, transaction
from django.urls import reverse

from . import ai
from .models import Device
from .notifications import notify_admins
from .staffing import DONE_STATUSES, HANDOVER_PENDING, assign_block_reason, assign_device, assignable_technicians

logger = logging.getLogger(__name__)


def _norm(text):
    return " ".join((text or "").replace("İ", "i").replace("I", "ı").lower().split())


def atanabilir_hale_geldi(device, created, old_status):
    """post_save sinyalinden: kayıt bu kaydetmeyle mi atanabilir hale geldi?"""
    if device.technician_id or getattr(device, "_no_auto_assign", False):
        return False
    if device.status in HANDOVER_PENDING or device.status in DONE_STATUSES:
        return False
    return created or old_status in HANDOVER_PENDING


def planla(device_id):
    """Kayıt veritabanına yazıldıktan (ekler dahil) sonra arka planda ata; kullanıcı beklemesin."""
    if not settings.AUTO_ASSIGN:
        return
    transaction.on_commit(lambda: threading.Thread(target=_calistir, args=(device_id,), daemon=True).start())


def _calistir(device_id):
    try:
        otomatik_ata(device_id)
    except Exception:
        logger.exception("Otomatik atama başarısız: %s", device_id)
    finally:
        close_old_connections()


def teknisyen_sec(device, adaylar, use_ai=True):
    """Kaydedilmiş cihaz için seçim (bkz. sec)."""
    bilinen = (ai.kullanici_bilgileri(device.customer) +
               [device.customer_name, device.customer_phone, device.customer_email])
    cihaz = f"{device.get_device_type_display()} · {device.brand} {device.model}"
    aciklama = " – ".join(x for x in (device.issue_title, device.issue_description) if x)
    return sec(aciklama, cihaz, adaylar, bilinen, use_ai)


def sec(aciklama, cihaz, adaylar, bilinen=(), use_ai=True, zaman_asimi=None):
    """(seçilen satır, uzmanlık, gerekçe, ai_sonucu) — adaylar assignable_technicians() sırasında
    (açık işi az olan önce, eşitlikte ada göre) ve yalnızca uygun (izinde/pasif olmayan) satırlardır."""
    gruplar = {}
    for r in adaylar:
        if r["specialization"].strip():
            gruplar.setdefault(_norm(r["specialization"]), r["specialization"].strip())
    sonuc = None
    if use_ai and gruplar and aciklama.strip():
        sonuc = ai.uzmanlik_sec(aciklama, cihaz, sorted(gruplar.values()), bilinen, zaman_asimi)
    uzmanlik = (sonuc or {}).get("uzmanlik", "")
    if uzmanlik:
        uzmanlar = [r for r in adaylar if _norm(r["specialization"]) == _norm(uzmanlik)]
        if uzmanlar:  # aynı uzmanlıkta birden çok kişi varsa: iş yükü en az olan (liste zaten öyle sıralı)
            return uzmanlar[0], uzmanlik, sonuc.get("gerekce", ""), sonuc
    return adaylar[0], "", "", sonuc


def _atanabilir(device_id):
    device = Device.objects.select_related("customer").filter(pk=device_id).first()
    if device is None or device.technician_id or assign_block_reason(device):
        return None
    return device


def otomatik_ata(device_id, use_ai=True):
    device = _atanabilir(device_id)
    if device is None:
        return None
    url = reverse("core:admin_device_detail", args=[device.pk])
    label = f"{device.tracking_id} · {device.title}"
    adaylar = [r for r in assignable_technicians() if r["available"]]
    if not adaylar:
        notify_admins("Otomatik atama yapılamadı", f"{label} — uygun (aktif, izinde olmayan) teknisyen yok.",
                      url=url, device=device)
        return None

    # Yapay zekâ çağrısı uzun sürebilir: veritabanı kilidi tutulmadan, transaction DIŞINDA yapılır.
    sonuc_uzm = teknisyen_sec(device, adaylar, use_ai)
    sonuc = sonuc_uzm[3]

    with transaction.atomic():
        device = _atanabilir(device_id)  # bu arada yönetici elle atadıysa ya da kayıt değiştiyse dokunma
        if device is None:
            return None
        # İş yükleri AI beklenirken değişmiş olabilir: aynı uzmanlık için güncel sıralamayla yeniden seç
        adaylar = [r for r in assignable_technicians() if r["available"]]
        if not adaylar:
            return None
        uzmanlik, gerekce = sonuc_uzm[1], sonuc_uzm[2]
        uzmanlar = [r for r in adaylar if uzmanlik and _norm(r["specialization"]) == _norm(uzmanlik)]
        secilen = uzmanlar[0] if uzmanlar else adaylar[0]
        if not uzmanlar:
            uzmanlik = ""
        # Arıza türü henüz belirlenmediyse yapay zekânın bulduğunu yaz (yönetici sonradan değiştirebilir)
        # (assign_device kaydı bütün olarak kaydettiği için bellekteki nesneye yazılır)
        if sonuc and sonuc.get("ariza_turu") and not device.fault_category:
            device.fault_category, device.fault_category_source = sonuc["ariza_turu"], "ai"

        is_yuku = f"{secilen['open_jobs']} açık iş"
        if uzmanlik:
            neden = f"uzmanlık «{uzmanlik}», {is_yuku}" + (f" — {gerekce}" if gerekce else "")
        elif sonuc is None:
            neden = f"yapay zekâ kullanılamadı; iş yükü en az olan seçildi ({is_yuku})"
        else:
            neden = f"arızaya uygun uzman bulunamadı; iş yükü en az olan seçildi ({is_yuku})"
        note = f"Otomatik atama: {neden}"[:200]
        tech = secilen["tech"]
        assign_device(device, tech, None, note=note)
    notify_admins("Kayıt otomatik atandı", f"{label} → {tech.display_name} ({neden})"[:290],
                  url=url, device=device)
    return tech
