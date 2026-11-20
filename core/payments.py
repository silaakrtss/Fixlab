"""Online ödeme (SİMÜLASYON). Gerçek tahsilat yapılmaz; bir ödeme sağlayıcısı bağlanana kadar akışın tamamını
(kart → 3D doğrulama kodu → sonuç → dekont + bildirimler) gerçeğe yakın şekilde işletir.

Güvenlik: kart numarası ve CVV hiçbir yerde saklanmaz; yalnızca son 4 hane ve kart türü kaydedilir.
Doğrulama kodu e-postayla gönderilir, kendisi değil yalnızca imzalı özeti (HMAC) tutulur.
Test kartı 4000 0000 0000 0002, doğru kod girilse bile «banka reddetti» sonucu verir.
"""
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from .models import AuditLog, Device, PaymentAttempt
from .utils import log_action, mask_email

import logging

logger = logging.getLogger(__name__)

OTP_MINUTES = 3
OTP_MAX_ATTEMPTS = 3
OTP_MAX_SENDS = 3
OTP_RESEND_COOLDOWN = 30          # saniye
DECLINE_TEST_CARD = "4000000000000002"
VAT_RATE = Decimal("0.20")


class PaymentError(Exception):
    """Kullanıcıya gösterilebilir ödeme hatası."""


# --- Tutar --------------------------------------------------------------------------------
def payable_amount(device):
    """Ödenecek tutar (KDV dahil): işçilik + parça; yoksa tahmini ücret. Belirlenmemişse 0."""
    if device.total_cost and device.total_cost > 0:
        return device.total_cost
    return device.estimated_cost or Decimal("0")


def amount_lines(device):
    """Ödeme ekranı / dekont kalemleri: kullanılan parçalar + işçilik (yoksa tek satır servis bedeli)."""
    lines = [{"label": u.part.name, "detail": f"{u.quantity} adet", "amount": u.line_total}
             for u in device.part_usages.select_related("part").order_by("added_at", "id")]
    if device.labor_cost:
        lines.append({"label": "Onarım ve test işçiliği", "detail": "", "amount": device.labor_cost})
    lines += [{"label": c.label, "detail": "", "amount": c.amount} for c in device.charges.all()]
    if not lines or not (device.total_cost and device.total_cost > 0):
        lines = [{"label": "Servis bedeli", "detail": "", "amount": payable_amount(device)}]
    return lines


def vat_share(amount):
    """KDV dahil tutardaki KDV payı (%20)."""
    return (amount - amount / (1 + VAT_RATE)).quantize(Decimal("0.01"))


def payment_block_reason(device):
    """Ödeme şu an yapılamıyorsa nedeni; yapılabiliyorsa boş metin."""
    if device.is_paid:
        return "Bu kaydın ödemesi zaten alındı."
    if not device.is_cargo:
        return "Elden teslim seçtiğiniz için ödemeyi cihazı teslim alırken serviste yapabilirsiniz."
    if device.status != Device.Status.READY:
        return "Online ödeme, onarım tamamlanıp cihaz teslime hazır olduğunda açılır."
    if payable_amount(device) <= 0:
        return "Servis bedeli henüz belirlenmedi; belirlendiğinde size bildirim gelir."
    return ""


# --- Kart -----------------------------------------------------------------------------------
def luhn_ok(number):
    total, alt = 0, False
    for ch in reversed(number):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def card_brand(number):
    if number.startswith("4"):
        return "Visa"
    if number[:2] in {"51", "52", "53", "54", "55"} or 2221 <= int(number[:4] or 0) <= 2720:
        return "Mastercard"
    if number[:2] in {"34", "37"}:
        return "American Express"
    if number.startswith("9792"):
        return "Troy"
    return "Kart"


# --- Doğrulama kodu ----------------------------------------------------------------------------
def _otp_hash(attempt, code):
    return salted_hmac("fixlab.payment-otp", f"{attempt.pk}:{code}").hexdigest()


def _send_code(attempt):
    code = f"{secrets.randbelow(10 ** 6):06d}"
    attempt.otp_hash = _otp_hash(attempt, code)
    attempt.otp_expires_at = timezone.now() + timedelta(minutes=OTP_MINUTES)
    attempt.otp_sent_at = timezone.now()
    attempt.otp_sends += 1
    attempt.otp_attempts = 0
    attempt.save(update_fields=["otp_hash", "otp_expires_at", "otp_sent_at", "otp_sends", "otp_attempts"])
    context = {"name": attempt.customer.display_name, "code": code, "minutes": OTP_MINUTES,
               "amount": attempt.amount, "tracking_id": attempt.device.tracking_id, "last4": attempt.card_last4}
    try:
        sent = send_mail("FIXLAB ödeme doğrulama kodunuz", render_to_string("core/email/payment_code.txt", context),
                         settings.DEFAULT_FROM_EMAIL, [attempt.customer.email],
                         html_message=render_to_string("core/email/payment_code.html", context), fail_silently=False)
        return sent > 0
    except Exception:
        logger.exception("Ödeme doğrulama kodu gönderilemedi (deneme %s)", attempt.pk)
        return False


@transaction.atomic
def start_payment(device, customer, data, request=None):
    """Kart + fatura bilgileriyle ödeme denemesi açar ve doğrulama kodunu e-postayla yollar."""
    reason = payment_block_reason(device)
    if reason:
        raise PaymentError(reason)
    # Önceki yarım kalmış denemeler kapatılır (aynı anda tek açık deneme).
    PaymentAttempt.objects.filter(device=device, status=PaymentAttempt.Status.PENDING).update(
        status=PaymentAttempt.Status.CANCELLED, completed_at=timezone.now())
    number = data["card_number"]
    attempt = PaymentAttempt.objects.create(
        device=device, customer=customer, amount=payable_amount(device),
        holder_name=data["holder_name"], card_last4=number[-4:], card_brand=card_brand(number),
        billing_name=data["billing_name"], billing_phone=data["billing_phone"], billing_address=data["billing_address"],
        simulate_decline=(number == DECLINE_TEST_CARD))
    sent = _send_code(attempt)
    log_action(AuditLog.Action.OTHER, user=customer, request=request, target=device,
               description=f"Online ödeme başlatıldı: {device.tracking_id} · {attempt.card_brand} **** {attempt.card_last4}")
    return attempt, sent


def resend_code(attempt):
    if attempt.status != PaymentAttempt.Status.PENDING:
        raise PaymentError("Bu ödeme işlemi artık açık değil.")
    if attempt.otp_sends >= OTP_MAX_SENDS:
        raise PaymentError("Yeniden kod gönderme hakkınız doldu. İşlemi iptal edip yeniden başlatın.")
    if attempt.otp_sent_at and (timezone.now() - attempt.otp_sent_at).total_seconds() < OTP_RESEND_COOLDOWN:
        raise PaymentError(f"Yeni kod istemek için {OTP_RESEND_COOLDOWN} saniye bekleyin.")
    return _send_code(attempt)


def cancel(attempt):
    if attempt.status == PaymentAttempt.Status.PENDING:
        attempt.status = PaymentAttempt.Status.CANCELLED
        attempt.completed_at = timezone.now()
        attempt.save(update_fields=["status", "completed_at"])


def _fail(attempt, reason):
    attempt.status = PaymentAttempt.Status.FAILED
    attempt.failure_reason = reason
    attempt.completed_at = timezone.now()
    attempt.save(update_fields=["status", "failure_reason", "completed_at"])
    return attempt


def verify(attempt, code, request=None):
    """Kodu doğrular. Dönüş: ('ok'|'wrong'|'failed', mesaj). Başarıda cihaz «ödendi» olur:
    dekont e-postası + yönetici/teknisyen bildirimi cihaz kaydı sinyalinden gider."""
    with transaction.atomic():
        attempt = PaymentAttempt.objects.select_for_update().select_related("device", "customer").get(pk=attempt.pk)
        if attempt.status != PaymentAttempt.Status.PENDING:
            raise PaymentError("Bu ödeme işlemi artık açık değil.")
        if timezone.now() > attempt.otp_expires_at:
            _fail(attempt, "Doğrulama kodunun süresi doldu.")
            return "failed", attempt.failure_reason
        code = (code or "").strip()
        if not (len(code) == 6 and code.isdigit() and constant_time_compare(attempt.otp_hash, _otp_hash(attempt, code))):
            attempt.otp_attempts += 1
            attempt.save(update_fields=["otp_attempts"])
            left = OTP_MAX_ATTEMPTS - attempt.otp_attempts
            if left <= 0:
                _fail(attempt, "Doğrulama kodu üst üste hatalı girildi.")
                return "failed", attempt.failure_reason
            return "wrong", f"Kod hatalı. {left} deneme hakkınız kaldı."
        if attempt.simulate_decline:
            _fail(attempt, "Banka ödemeyi onaylamadı (test kartı).")
            return "failed", attempt.failure_reason
        device = Device.objects.select_for_update().get(pk=attempt.device_id)
        if device.is_paid or payment_block_reason(device):
            _fail(attempt, payment_block_reason(device) or "Bu kaydın ödemesi zaten alındı.")
            return "failed", attempt.failure_reason
        attempt.status = PaymentAttempt.Status.SUCCEEDED
        attempt.completed_at = timezone.now()
        attempt.reference = f"FXP-{timezone.localtime():%y%m%d}-{attempt.pk:05d}"
        attempt.save(update_fields=["status", "completed_at", "reference"])
        device.is_paid = True
        device._actor = attempt.customer
        device._payment_attempt = attempt        # dekont e-postasına işlem ayrıntıları eklenir
        device.save()
    log_action(AuditLog.Action.UPDATE, user=attempt.customer, request=request, target=device,
               description=f"Online ödeme alındı (simülasyon): {device.tracking_id} · {attempt.reference}")
    return "ok", ""


def masked_email(user):
    return mask_email(user.email)
