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


def _sohbet(sistem, mesajlar, resimler=None, sema=None, uzunluk=300, zaman_asimi=None):
    """Ollama'ya istek atar. Hata olursa None döner; site asla bozulmaz."""
    if not settings.AI_ENABLED:
        return None
    msgs = [{"role": "system", "content": sistem}] + list(mesajlar)
    if resimler:
        msgs[-1] = {**msgs[-1], "images": resimler}
    govde = {"model": settings.OLLAMA_MODEL, "messages": msgs, "stream": False,
             "options": {"temperature": 0.2, "num_predict": uzunluk}}
    if sema:
        govde["format"] = sema
    try:
        r = requests.post(f"{settings.OLLAMA_URL}/api/chat", json=govde, timeout=zaman_asimi or settings.OLLAMA_TIMEOUT,
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


# ---------------- TEKNİSYEN ÇÖZÜM ASİSTANI ----------------
SISTEM_TEKNISYEN = """Sen FIXLAB teknik servisinin kıdemli arıza tespit uzmanısın; karşındaki kişi servisin TEKNİSYENİ.
Kurallar:
- Türkçe yaz; teknik terim kullanabilirsin. Kısa ve uygulanabilir ol, gereksiz giriş cümlesi kurma.
- Cevabını şu başlıklarla ver (gereksiz olanı atla):
  **Olası nedenler** – olasılık sırasıyla en fazla 4 madde; her birinin yanında kısa gerekçe.
  **Kontrol adımları** – hızlı/ucuz testten başlayarak sıralı adımlar (ölçüm noktası, beklenen değer, kullanılacak araç).
  **Gerekebilecek parça** – yalnızca ihtimal yüksekse; depoda varsa adını aynen yaz.
  **Dikkat** – ESD, batarya, sıvı teması, yüksek gerilim gibi riskler varsa tek satır.
- Cihaz kaydındaki bilgileri (marka, model, arıza, önceki notlar, kullanılan parçalar) ZATEN biliyorsun; teknisyene tekrar sorma.
- Fotoğraf veya video kareleri varsa gördüğün hasarı (kırık, oksitlenme, şişme, yanık, eksik vida, kopuk flex) açıkça belirt.
  Video kareleri bir videodan sırayla alınmış görüntülerdir.
- Teknisyen bir adımın sonucunu yazarsa (ör. "voltaj geliyor", "harici klavye çalışıyor") olasılıkları buna göre güncelle.
- Emin olmadığın modele özgü bilgi (pin numarası, şema değeri) uydurma; "servis kılavuzundan doğrulayın" de.
- [KİŞİSEL], [TELEFON] gibi etiketler gizlenmiş bilgilerdir; tahmin etmeye çalışma.

CİHAZ KAYDI:
{baglam}"""


def teknisyen_baglam(device):
    """Cihaz kaydından yapay zekâya gidecek teknik bağlam (müşteri adı/telefonu gönderilmez)."""
    from .models import StockPart
    d = device
    # Takip no ve seri no bilerek gönderilmez: kişiyle/kayıtla eşleştirilebilen tanımlayıcılardır (KVKK).
    satir = [f"Cihaz: {d.get_device_type_display()} · {d.brand} {d.model}"]
    if d.accessories:
        satir.append(f"Teslim alınan aksesuarlar: {d.accessories}")
    satir.append(f"Durum: {d.get_status_display()} · Öncelik: {d.get_priority_display()}"
                 f" · Kabul: {d.received_at:%d.%m.%Y}")
    if d.fault_category:
        satir.append(f"Arıza türü: {d.get_fault_category_display()}")
    if d.issue_title:
        satir.append(f"Arıza başlığı: {d.issue_title}")
    satir.append(f"Müşterinin arıza açıklaması: {d.issue_description[:1500]}")
    if d.diagnosis_notes:
        satir.append(f"Teknisyen tespit notları: {d.diagnosis_notes[:800]}")
    notlar = list(d.notes.order_by("-created_at")[:8])
    if notlar:
        satir.append("Servis notları (yeniden eskiye):")
        satir += [f"- {n.created_at:%d.%m %H:%M}: {n.text[:300]}" for n in notlar]
    parcalar = list(d.part_usages.select_related("part"))
    if parcalar:
        satir.append("Bu cihazda kullanılan parçalar: " +
                     ", ".join(f"{u.part.name} x{u.quantity}" for u in parcalar))
    ekler = list(d.attachments.all())
    if ekler:
        foto = sum(a.kind == DeviceAttachment.Kind.PHOTO for a in ekler)
        satir.append(f"Müşterinin kayda eklediği medya: {foto} fotoğraf, {len(ekler) - foto} video.")
    # Aynı müşterinin bu servisteki önceki kayıtları (tekrarlayan arıza ipucu)
    onceki = Device.objects.exclude(pk=d.pk)
    onceki = onceki.filter(customer_id=d.customer_id) if d.customer_id else onceki.filter(
        customer_phone=d.customer_phone) if d.customer_phone else onceki.none()
    onceki = list(onceki.order_by("-received_at")[:5])
    if onceki:
        satir.append("Müşterinin önceki servis kayıtları:")
        satir += [f"- {o.received_at:%d.%m.%Y} · {o.brand} {o.model} · "
                  f"{(o.issue_title or o.issue_description)[:120]} · {o.get_status_display()}" for o in onceki]
    depo = list(StockPart.objects.filter(is_active=True, quantity__gt=0).order_by("name")
                .values_list("name", flat=True)[:40])
    if depo:
        satir.append("Depoda stokta olan parçalar: " + ", ".join(depo))
    return "\n".join(satir)


def teknisyen_cevap(device, gecmis, resimler=None, video_kare=0):
    """Teknisyen sohbeti: kayıt bağlamı + konuşma + (fotoğraf / video kareleri)."""
    bilinen = (kullanici_bilgileri(device.customer) +
               [device.customer_name, device.customer_phone, device.customer_email,
                device.return_recipient, device.return_phone, device.return_address])
    mesajlar = [{"role": m["role"], "content": anonimlestir(str(m.get("content", "")), bilinen)[:2000]}
                for m in gecmis[-12:] if m.get("role") in ("user", "assistant")]
    if not mesajlar or mesajlar[-1]["role"] != "user":
        return None
    if resimler:
        ek = f"\n\n[Ekte {len(resimler)} görüntü var"
        ek += f"; bunların son {video_kare} tanesi videodan alınmış karelerdir]" if video_kare else "]"
        mesajlar[-1]["content"] += ek
    sistem = SISTEM_TEKNISYEN.format(baglam=anonimlestir(teknisyen_baglam(device), bilinen))
    return _sohbet(sistem, mesajlar, resimler, uzunluk=900)


# ---------------- OTOMATİK ATAMA: arızaya uygun uzmanlık alanı ----------------
YOK = "Hiçbiri"
SISTEM_UZMANLIK = """Sen FIXLAB teknik servisinin iş dağıtım asistanısın.
Görevin: cihaz arızasını okuyup servisteki UZMANLIK ALANLARINDAN en uygun olanını seçmek ve SADECE JSON döndürmek.
- uzmanlik: aşağıdaki listeden AYNEN biri. Hiçbiri arızayla ilgili değilse "{yok}".
- Önce CİHAZ TÜRÜNE bak: cihaz türüyle uyumsuz uzmanlığı seçme (dizüstü/masaüstü için telefon veya yazıcı
  uzmanlığı; telefon için yazıcı uzmanlığı vb.).
- Sıvı teması, açılmama, güç/şarj alamama, kısa devre gibi kart düzeyindeki arızalar anakart/donanım uzmanlığına,
  görüntü ve panel sorunları ekran uzmanlığına, açılış hatası/mavi ekran/virüs yazılım uzmanlığına gider.
- ariza_turu: şu kodlardan biri: {kodlar}
- gerekce: en fazla 12 kelimelik Türkçe gerekçe.
Uzmanlık alanları:
{liste}"""


def uzmanlik_sec(aciklama, cihaz, uzmanliklar, bilinenler=(), zaman_asimi=None):
    """Arızaya en uygun uzmanlık alanını seçer. {'uzmanlik', 'ariza_turu', 'gerekce'} ya da None (AI yanıt vermezse).
    Yapay zekâya teknisyen adı GİTMEZ; yalnızca uzmanlık alanı metinleri gider."""
    secenekler = list(uzmanliklar) + [YOK]
    sema = {"type": "object",
            "properties": {"uzmanlik": {"type": "string", "enum": secenekler},
                           "ariza_turu": {"type": "string", "enum": list(ARIZA_TURLERI)},
                           "gerekce": {"type": "string"}},
            "required": ["uzmanlik", "ariza_turu", "gerekce"]}
    kodlar = ", ".join(f"{k} ({v})" for k, v in ARIZA_TURLERI.items())
    sistem = SISTEM_UZMANLIK.format(yok=YOK, kodlar=kodlar, liste="\n".join(f"- {u}" for u in uzmanliklar) or "-")
    soru = f"Cihaz: {cihaz}\nArıza: {anonimlestir(aciklama, bilinenler)[:1500]}"
    try:
        veri = json.loads(_sohbet(sistem, [{"role": "user", "content": soru}], sema=sema, uzunluk=150,
                                   zaman_asimi=zaman_asimi) or "")
    except ValueError:
        return None
    uz = veri.get("uzmanlik")
    return {"uzmanlik": uz if uz in uzmanliklar else "",
            "ariza_turu": veri.get("ariza_turu") if veri.get("ariza_turu") in ARIZA_TURLERI else "",
            "gerekce": str(veri.get("gerekce", ""))[:120]}


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
