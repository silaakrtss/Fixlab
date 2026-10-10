from .models import AuditLog
from .utils import generate_temp_password, generate_username, log_action, send_credentials_email


def provision_user(user, created_by=None, request=None):
    """Yeni kullanıcıya benzersiz kullanıcı adı + geçici şifre verir, kaydeder, e-posta yollar.

    Dönüş: e-posta gönderildi mi (bool). Kullanıcı e-posta başarısız olsa da kaydedilir;
    yönetici 'bilgileri yeniden gönder' ile yeni geçici şifre üretebilir.
    """
    user.username = generate_username(user.first_name, user.last_name)
    raw_password = generate_temp_password()
    user.set_password(raw_password)
    user.is_temporary_password = True
    user.save()
    log_action(AuditLog.Action.CREATE, user=created_by, request=request, target=user,
               description=f"{user.get_role_display()} hesabı oluşturuldu: {user.username}")
    sent = send_credentials_email(user, raw_password)
    log_action(AuditLog.Action.CREDENTIALS_SENT, user=created_by, request=request, target=user,
               description=f"{user.email} adresine giriş bilgisi {'gönderildi' if sent else 'GÖNDERİLEMEDİ'}")
    return sent


def reset_credentials(user, by=None, request=None):
    """Yeni geçici şifre üretir ve e-postayla gönderir; kullanıcı tekrar şifre değiştirmek zorunda kalır."""
    raw_password = generate_temp_password()
    user.set_password(raw_password)
    user.is_temporary_password = True
    user.save(update_fields=["password", "is_temporary_password"])
    sent = send_credentials_email(user, raw_password)
    log_action(AuditLog.Action.CREDENTIALS_SENT, user=by, request=request, target=user,
               description=f"Yeni geçici şifre {'gönderildi' if sent else 'GÖNDERİLEMEDİ'}: {user.username}")
    return sent
