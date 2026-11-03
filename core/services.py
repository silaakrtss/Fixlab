import secrets
from datetime import timedelta

from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from . import accounts
from .models import AuditLog, EmailVerificationCode, PasswordResetCode
from .utils import (generate_temp_password, generate_username, log_action, send_credentials_email,
                    send_reset_code_email, send_verification_code_email)

RESET_CODE_MINUTES = 10       # kodun geçerlilik süresi
RESET_MAX_ATTEMPTS = 5        # bir kod için en fazla hatalı deneme
RESET_MAX_PER_HOUR = 5        # bir hesap için saatte en fazla kod
RESET_COOLDOWN_SECONDS = 60   # iki kod isteği arası bekleme


def provision_user(user, created_by=None, request=None):
    """Yeni kullanıcıya benzersiz kullanıcı adı + geçici şifre verir, kaydeder, e-posta yollar.

    LDAP açıkken hesap dizinde de açılır (şifre yalnızca orada tutulur).
    Dönüş: e-posta gönderildi mi (bool). Kullanıcı e-posta başarısız olsa da kaydedilir;
    yönetici 'bilgileri yeniden gönder' ile yeni geçici şifre üretebilir.
    Dizin hatası (sunucu kapalı, e-posta zaten kayıtlı vb.) accounts.DirectoryError olarak yükselir; hiçbir kayıt kalmaz.
    """
    raw_password = generate_temp_password()
    user.is_temporary_password = True
    username = generate_username(user.first_name, user.last_name)
    for attempt in range(6):
        user.username = username
        try:
            accounts.create_account(user, raw_password)
            break
        except accounts.DirectoryConflict as exc:
            # Kullanıcı adı dizinde başka bir kayıtla çakıştıysa sayı ekleyip yeniden dene; e-posta çakışması gerçek hatadır.
            if exc.field != "uid" or attempt == 5:
                raise
            username = f"{generate_username(user.first_name, user.last_name)}{secrets.randbelow(900) + 100}"
    log_action(AuditLog.Action.CREATE, user=created_by, request=request, target=user,
               description=f"{user.get_role_display()} hesabı oluşturuldu: {user.username}")
    sent = send_credentials_email(user, raw_password)
    log_action(AuditLog.Action.CREDENTIALS_SENT, user=created_by, request=request, target=user,
               description=f"{user.email} adresine giriş bilgisi {'gönderildi' if sent else 'GÖNDERİLEMEDİ'}")
    return sent


def reset_credentials(user, by=None, request=None):
    """Yeni geçici şifre üretir ve e-postayla gönderir; kullanıcı tekrar şifre değiştirmek zorunda kalır."""
    raw_password = generate_temp_password()
    accounts.apply_password(user, raw_password)
    user.is_temporary_password = True
    user.save(update_fields=["password", "is_temporary_password"])
    sent = send_credentials_email(user, raw_password)
    log_action(AuditLog.Action.CREDENTIALS_SENT, user=by, request=request, target=user,
               description=f"Yeni geçici şifre {'gönderildi' if sent else 'GÖNDERİLEMEDİ'}: {user.username}")
    return sent


# --- Müşteri şifre sıfırlama (6 haneli e-posta kodu) ---------------------------
def _code_hash(user, code):
    return salted_hmac("fixlab.password-reset", f"{user.pk}:{code}").hexdigest()


def issue_reset_code(user, request=None):
    """Yeni 6 haneli kod üretir, eskileri geçersiz kılar, e-postayla yollar.
    Dönüş: 'sent' | 'failed' | 'limit'."""
    now = timezone.now()
    if user.reset_codes.filter(created_at__gte=now - timedelta(hours=1)).count() >= RESET_MAX_PER_HOUR:
        return "limit"
    user.reset_codes.filter(used_at__isnull=True).update(used_at=now)
    code = f"{secrets.randbelow(10 ** 6):06d}"
    PasswordResetCode.objects.create(user=user, code_hash=_code_hash(user, code),
                                     expires_at=now + timedelta(minutes=RESET_CODE_MINUTES))
    sent = send_reset_code_email(user, code, RESET_CODE_MINUTES)
    log_action(AuditLog.Action.CREDENTIALS_SENT, user=user, request=request, target=user,
               description=f"Şifre sıfırlama kodu {'gönderildi' if sent else 'GÖNDERİLEMEDİ'}: {user.email}")
    return "sent" if sent else "failed"


def check_reset_code(user, code):
    """Kod doğruysa PasswordResetCode kaydını döndürür, değilse None (hatalı denemeyi sayar)."""
    record = (user.reset_codes.filter(used_at__isnull=True, expires_at__gt=timezone.now())
              .order_by("-created_at").first())
    if record is None or record.attempts >= RESET_MAX_ATTEMPTS:
        return None
    if constant_time_compare(record.code_hash, _code_hash(user, code)):
        return record
    record.attempts += 1
    record.save(update_fields=["attempts"])
    return None


def consume_reset_codes(user):
    user.reset_codes.filter(used_at__isnull=True).update(used_at=timezone.now())


# --- Üyelik e-posta doğrulaması (6 haneli kod) -----------------------------------
def _verify_hash(user, code):
    return salted_hmac("fixlab.email-verify", f"{user.pk}:{code}").hexdigest()


def issue_verification_code(user, request=None):
    """Yeni 6 haneli üyelik doğrulama kodu üretir, eskileri geçersiz kılar, e-postayla yollar.
    Dönüş: 'sent' | 'failed' | 'limit'."""
    now = timezone.now()
    if user.verification_codes.filter(created_at__gte=now - timedelta(hours=1)).count() >= RESET_MAX_PER_HOUR:
        return "limit"
    user.verification_codes.filter(used_at__isnull=True).update(used_at=now)
    code = f"{secrets.randbelow(10 ** 6):06d}"
    EmailVerificationCode.objects.create(user=user, code_hash=_verify_hash(user, code),
                                         expires_at=now + timedelta(minutes=RESET_CODE_MINUTES))
    sent = send_verification_code_email(user, code, RESET_CODE_MINUTES)
    log_action(AuditLog.Action.CREDENTIALS_SENT, user=user, request=request, target=user,
               description=f"Üyelik doğrulama kodu {'gönderildi' if sent else 'GÖNDERİLEMEDİ'}: {user.email}")
    return "sent" if sent else "failed"


def check_verification_code(user, code):
    """Kod doğruysa kaydı döndürür, değilse None (hatalı denemeyi sayar)."""
    record = (user.verification_codes.filter(used_at__isnull=True, expires_at__gt=timezone.now())
              .order_by("-created_at").first())
    if record is None or record.attempts >= RESET_MAX_ATTEMPTS:
        return None
    if constant_time_compare(record.code_hash, _verify_hash(user, code)):
        return record
    record.attempts += 1
    record.save(update_fields=["attempts"])
    return None


def consume_verification_codes(user):
    user.verification_codes.filter(used_at__isnull=True).update(used_at=timezone.now())
