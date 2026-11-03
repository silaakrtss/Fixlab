"""Müşteri portalı: cihazın servis / kargo sürecini adım adım gösteren zaman çizelgesi."""
from .models import Device

S = Device.Status

# Durum -> süreç aşaması (0..7). Aşamalar kargo ile gelen cihazın tam yolculuğudur.
PHASE = {
    S.PRE_REGISTERED: 1, S.AWAITING_CARGO: 1, S.IN_TRANSIT: 2, S.RECEIVED: 3,
    S.DIAGNOSING: 4, S.WAITING_APPROVAL: 4, S.WAITING_PART: 4, S.REPAIRING: 4, S.TESTING: 4,
    S.READY: 5, S.RETURN_SHIPPING: 6, S.DELIVERED: 7,
}
TERMINAL_FAIL = (S.UNREPAIRABLE, S.CANCELLED)


def phase_of(status):
    return PHASE.get(status)


def _current_phase(device, history):
    phase = phase_of(device.status)
    if phase is not None:
        return phase
    # İptal / onarılamaz: en son ulaşılan aşamada durur.
    reached = [phase_of(h.new_status) for h in history if phase_of(h.new_status) is not None]
    return max(reached) if reached else 1


def build_stages(device):
    """Cihazın durumuna göre [{key,title,desc,state,when,extra}] listesi ve özet bilgiler döndürür."""
    history = list(device.status_history.all())          # -created_at sırasında (yeniden eskiye)
    first_at = {}
    for h in reversed(history):                           # eskiden yeniye
        phase = phase_of(h.new_status)
        if phase is not None and phase not in first_at:
            first_at[phase] = h.created_at
    first_at[0] = device.received_at

    cargo = device.is_cargo
    current = _current_phase(device, history)
    failed = device.status in TERMINAL_FAIL
    all_done = device.status == S.DELIVERED

    if cargo:
        transit_extra = " · ".join(x for x in (
            f"Firma: {device.cargo_company}" if device.cargo_company else "",
            f"Takip kodu: {device.cargo_tracking_code}" if device.cargo_tracking_code else "") if x)
        return_extra = " · ".join(x for x in (
            f"Firma: {device.return_cargo_company}" if device.return_cargo_company else "",
            f"Takip kodu: {device.return_cargo_tracking_code}" if device.return_cargo_tracking_code else "") if x)
        defs = [
            (0, "Ön kayıt oluşturuldu", "Cihaz ve arıza bilgileriniz kaydedildi.", ""),
            (1, "Kargo bekleniyor", "Cihazınızı hazırlayıp kargoya verin, ardından kargo bilgisini bu sayfadan girin.", ""),
            (2, "Kargoda · Servise geliyor", "Cihazınız kargo firmasında, servise doğru yolda.", transit_extra),
            (3, "Serviste teslim alındı", "Cihaz servise ulaştı ve teslim alındı.", ""),
            (4, "Teknik inceleme ve onarım", "Teknisyenimiz cihazınızı inceliyor ve onarıyor.", ""),
            (5, "Onarım tamamlandı", "Cihazınız hazır, geri kargoya verilmesi bekleniyor.", ""),
            (6, "Geri kargoda", "Onarılan cihazınız adresinize gönderildi.", return_extra),
            (7, "Teslim edildi", "Cihazınız size ulaştı. Bizi tercih ettiğiniz için teşekkürler.", ""),
        ]
    else:
        defs = [
            (0, "Ön kayıt oluşturuldu", "Cihaz ve arıza bilgileriniz kaydedildi.", ""),
            (1, "Cihaz teslimi bekleniyor", "Cihazınızı takip kodunuzla birlikte servise getirin.", ""),
            (3, "Serviste teslim alındı", "Cihaz servise teslim edildi.", ""),
            (4, "Teknik inceleme ve onarım", "Teknisyenimiz cihazınızı inceliyor ve onarıyor.", ""),
            (5, "Teslime hazır", "Cihazınız hazır, servisten teslim alabilirsiniz.", ""),
            (7, "Teslim edildi", "Cihazınızı teslim aldınız.", ""),
        ]

    stages = []
    for phase, title, desc, extra in defs:
        if all_done or phase < current:
            state = "done"
        elif phase == current:
            state = "current"
        else:
            state = "todo"
        if failed and phase == current:
            state = "stopped"
        stages.append({"phase": phase, "title": title, "desc": desc, "extra": extra if state != "todo" else "",
                       "state": state, "when": first_at.get(phase) if state != "todo" else None})
    done_count = sum(1 for s in stages if s["state"] == "done")
    current_stage = next((s for s in stages if s["state"] in ("current", "stopped")), stages[-1])
    percent = 100 if all_done else round((done_count + (0.5 if not failed else 0)) / len(stages) * 100)
    return {"stages": stages, "current": current_stage, "percent": min(percent, 100), "failed": failed}


# Müşteriye gösterilen kısa durum etiketi + rozet rengi sınıfı
def status_tone(status):
    if status in (S.READY, S.DELIVERED):
        return "ok"
    if status in (S.UNREPAIRABLE, S.CANCELLED):
        return "bad"
    if status in (S.WAITING_APPROVAL, S.WAITING_PART, S.AWAITING_CARGO):
        return "warn"
    return "info"


def status_note(device):
    """Ana sayfa kartındaki «Son durum» cümlesi."""
    when = device.received_at
    notes = {
        S.PRE_REGISTERED: "Ön kaydınız alındı. Cihazınızı servise getirdiğinizde inceleme başlayacak.",
        S.AWAITING_CARGO: "Cihazınızı kargo şirketine vereceğiniz an «Kargo sürecini görüntüle» sayfasından kargo kodunuzu oluşturun.",
        S.IN_TRANSIT: "Cihaz kargoda ve servise ulaştığında inceleme başlayacak. Kargo adımlarını aşağıdaki ekrandan takip edebilirsiniz.",
        S.RECEIVED: "Cihazınız servise ulaştı ve teslim alındı. Teknik inceleme kısa süre içinde başlayacak.",
        S.DIAGNOSING: f"Teknisyenimiz cihazınızı kontrol ediyor. Bu kayıt {when:%d.%m.%Y} tarihinde oluşturulmuştur.",
        S.WAITING_APPROVAL: "Onarım için onayınız bekleniyor. Lütfen servisle iletişime geçin.",
        S.WAITING_PART: "Onarım için gereken parça bekleniyor.",
        S.REPAIRING: "Cihazınız onarım aşamasında.",
        S.TESTING: "Onarım tamamlandı, cihazınız son testlerden geçiyor.",
        S.READY: "Cihazınız hazır.",
        S.RETURN_SHIPPING: "Cihazınız size geri kargolandı.",
        S.DELIVERED: "Cihazınız teslim edildi.",
        S.UNREPAIRABLE: "Cihaz onarılamadı. Ayrıntı için servisle iletişime geçin.",
        S.CANCELLED: "Bu kayıt iptal edildi.",
    }
    return notes.get(device.status, "")
