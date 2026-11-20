"""FIXLAB yapay zekâ yardımcıları (Ollama). Buluta giden her metin önce anonimleştirilir."""
import base64, io, json, logging, threading

import requests
from django.conf import settings
from PIL import Image, ImageOps

from .anonymize import anonimlestir, kullanici_bilgileri  # noqa: F401  (views buradan kullanır)
from .models import Device, DeviceAttachment

logger = logging.getLogger(__name__)
ARIZA_TURLERI = dict(Device.FaultCategory.choices)

# ---------------- ÖN TALİMAT (SYSTEM INSTRUCTION) ----------------
SISTEM_GENEL = """Sen FIXLAB teknik servisinin yapay zekâ asistanısın.
Kurallar:
- Her zaman Türkçe, kısa, sade ve nazik yaz.
- En fazla 3 kısa madde yaz.
- Müşteriye yalnızca GÜVENLİ ve evde yapılabilecek basit adımlar öner (yeniden başlatma, kablo/şarj aleti kontrolü, güncelleme gibi).
- Cihazı açmayı, batarya sökmeyi, lehim yapmayı ASLA önerme.
- BIOS, sürücü veya sistem ayarlarıyla uğraşmayı önerme.
- Sıvı teması, şişmiş batarya, yanık kokusu veya duman varsa: cihazı kapatıp şarjdan çıkarmasını ve servise getirmesini söyle.
- Fiyat veya kesin süre sözü verme.
- [KİŞİSEL], [TELEFON] gibi etiketler gizlenmiş bilgilerdir; tahmin etmeye çalışma, cevabında kullanma.
- Emin değilsen bunu açıkça söyle."""

SISTEM_ANALIZ = SISTEM_GENEL + """
Görevin: müşterinin arıza açıklamasını (ve varsa fotoğrafları) incelemek ve SADECE JSON döndürmek.
- baslik: en fazla 8 kelimelik açıklayıcı arıza başlığı (örn. "Ekranda yatay çizgiler ve titreme").
- ariza_turu: şu kodlardan biri: {kodlar}
- oneriler: servise gelmeden önce denenebilecek en fazla 3 kısa, güvenli öneri.
- acil_uyari: tehlike varsa tek cümlelik uyarı, yoksa boş metin.
Fotoğraf varsa gördüğün hasarı (kırık ekran, sıvı izi, şişme) dikkate al."""

SISTEM_ASISTAN = SISTEM_GENEL + """
Bir sohbet asistanısın. Karşındaki kişi: {rol}.
- Müşteriyse: gerekirse tek bir netleştirici soru sor, basit adımlar ver, gerekiyorsa servise yönlendir.
- Teknisyen ya da yöneticiyse: teknik terim kullanabilirsin; olası nedenleri olasılık sırasına göre yaz ve her birinin nasıl test edileceğini söyle.
Cihaz bilgisi: {baglam}"""

ANALIZ_SEMASI = {
    "type": "object",
    "properties": {
        "baslik": {"type": "string"},
        "ariza_turu": {"type": "string", "enum": list(ARIZA_TURLERI)},
        "oneriler": {"type": "array", "items": {"type": "string"}},
        "acil_uyari": {"type": "string"},
    },
    "required": ["baslik", "ariza_turu", "oneriler", "acil_uyari"],
}


def resim_base64(dosya, en_fazla=1024):
    """Fotoğrafı küçültür, konum vb. EXIF bilgisini atar (yeniden kaydederken yazılmaz), base64 döndürür."""
    img = ImageOps.exif_transpose(Image.open(dosya)).convert("RGB")
    img.thumbnail((en_fazla, en_fazla))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _sohbet(sistem, mesajlar, resimler=None, sema=None):
    """Ollama'ya istek atar. Hata olursa None döner; site asla bozulmaz."""
    if not settings.AI_ENABLED:
        return None
    msgs = [{"role": "system", "content": sistem}] + list(mesajlar)
    if resimler:
        msgs[-1] = {**msgs[-1], "images": resimler}
    govde = {"model": settings.OLLAMA_MODEL, "messages": msgs, "stream": False,
             "options": {"temperature": 0.2, "num_predict": 300}}
    if sema:
        govde["format"] = sema
    try:
        r = requests.post(f"{settings.OLLAMA_URL}/api/chat", json=govde, timeout=settings.OLLAMA_TIMEOUT,
                          headers={"ngrok-skip-browser-warning": "1"})
        r.raise_for_status()
        return r.json()["message"]["content"]
    except Exception:
        logger.exception("Yapay zekâ isteği başarısız")
        return None


def ariza_analiz(aciklama, cihaz_turu="", marka_model="", resimler=None, bilinenler=()):
    temiz = anonimlestir(aciklama, bilinenler)[:2000]
    kodlar = ", ".join(f"{k} ({v})" for k, v in ARIZA_TURLERI.items())
    soru = f"Cihaz: {cihaz_turu} {marka_model}\nArıza açıklaması: {temiz}"
    cevap = _sohbet(SISTEM_ANALIZ.format(kodlar=kodlar), [{"role": "user", "content": soru}], resimler, ANALIZ_SEMASI)
    try:
        veri = json.loads(cevap or "")
    except ValueError:
        return None
    if veri.get("ariza_turu") not in ARIZA_TURLERI:
        veri["ariza_turu"] = "diger"
    veri["baslik"] = str(veri.get("baslik", ""))[:150]
    veri["oneriler"] = [str(o)[:200] for o in veri.get("oneriler", [])][:3]
    veri["acil_uyari"] = str(veri.get("acil_uyari", ""))[:200]
    return veri


def asistan_cevap(gecmis, rol, baglam="", bilinenler=(), resimler=None):
    """resimler: müşterinin yüklediği fotoğraflar (base64); son mesaja eklenip yapay zekâya gösterilir."""
    mesajlar = [{"role": m["role"], "content": anonimlestir(str(m.get("content", "")), bilinenler)[:2000]}
                for m in gecmis[-10:] if m.get("role") in ("user", "assistant")]
    if not mesajlar or mesajlar[-1]["role"] != "user":
        return None
    sistem = SISTEM_ASISTAN.format(rol=rol, baglam=anonimlestir(baglam, bilinenler) or "-")
    return _sohbet(sistem, mesajlar, resimler)


# ---- Kayıt oluşunca arka planda arıza türünü belirle (müşteri beklemesin) ----
def arka_planda_tur_belirle(device_id):
    threading.Thread(target=_tur_belirle, args=(device_id,), daemon=True).start()


def _tur_belirle(device_id):
    from django.db import close_old_connections
    try:
        d = Device.objects.get(pk=device_id)
        resimler = []
        for a in d.attachments.filter(kind=DeviceAttachment.Kind.PHOTO)[:3]:
            with a.file.open("rb") as fh:
                resimler.append(resim_base64(fh))
        bilinen = kullanici_bilgileri(d.customer) + [d.customer_name, d.customer_phone, d.customer_email]
        veri = ariza_analiz(d.issue_description, d.get_device_type_display(), f"{d.brand} {d.model}", resimler, bilinen)
        if veri:  # .update(): bildirim/sinyal tetiklemez; admin önceden değiştirdiyse dokunmaz
            Device.objects.filter(pk=d.pk, fault_category="").update(
                fault_category=veri["ariza_turu"], fault_category_source="ai")
    except Exception:
        logger.exception("Arıza türü belirlenemedi")
    finally:
        close_old_connections()
