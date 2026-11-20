"""Hesap servisi: kullanıcı kimlikleri (şifre dahil) LDAP'ta, uygulama verisi PostgreSQL'de.

LDAP_ENABLED=1 iken:
  * Kullanıcı adı / e-posta / ad soyad / telefon / ROL / ŞİFRE dizinde (LDAP) tutulur.
  * Django'daki ``User`` satırı yalnızca bir "gölge kayıttır" (cihaz, izin, bildirim ilişkileri için);
    şifre alanı kullanılamaz (unusable) durumdadır, giriş her zaman LDAP'a sorulur.
LDAP_ENABLED=0 iken her şey eskisi gibi Django'nun kendi şifre sistemiyle çalışır.

Uygulamanın her yerinde şifre/hesap işlemleri bu modül üzerinden yapılır.
"""
import logging

from django.conf import settings

from . import directory as dirmod
from .directory import (DirectoryConflict, DirectoryEntry, DirectoryError, DirectoryNotFound,  # noqa: F401
                        DirectoryUnavailable)
from .models import User

logger = logging.getLogger(__name__)

_directory = None  # önbellek; testler buraya MemoryDirectory koyabilir


def ldap_enabled():
    return bool(getattr(settings, "LDAP_ENABLED", False))


def get_directory():
    global _directory
    if _directory is None:
        _directory = dirmod.LdapDirectory(
            url=settings.LDAP_URL, base_dn=settings.LDAP_BASE_DN, bind_dn=settings.LDAP_BIND_DN,
            bind_password=settings.LDAP_BIND_PASSWORD, start_tls=settings.LDAP_START_TLS,
            timeout=settings.LDAP_TIMEOUT,
            ou_names={"admin": settings.LDAP_OU_ADMIN, "technician": settings.LDAP_OU_TECHNICIAN,
                      "customer": settings.LDAP_OU_CUSTOMER})
    return _directory


def set_directory(directory):
    """Testler ve araçlar için dizin nesnesini değiştirir (None: sıfırla)."""
    global _directory
    _directory = directory


def entry_from_user(user):
    return DirectoryEntry(uid=user.username, role=user.role, email=user.email or "",
                          first_name=user.first_name or "", last_name=user.last_name or "",
                          phone=user.phone or "")


# --- Hesap oluşturma / şifre ------------------------------------------------
def create_account(user, raw_password):
    """Yeni hesabı kaydeder. ``user.username`` dolu olmalıdır; kullanıcı kaydedilir ve döndürülür.

    LDAP açıkken önce dizinde kayıt açılır; Django kaydı başarısız olursa dizin kaydı geri alınır."""
    if not ldap_enabled():
        user.set_password(raw_password)
        user.save()
        return user
    directory = get_directory()
    directory.create(entry_from_user(user), raw_password)
    try:
        user.set_unusable_password()
        user.save()
    except Exception:
        try:
            directory.delete(user.username)
        except DirectoryError:  # pragma: no cover
            logger.exception("Geri alma sırasında dizin kaydı silinemedi: %s", user.username)
        raise
    return user


def apply_password(user, raw_password):
    """Kullanıcının şifresini değiştirir (kaydetmez; çağıran ``user.save(update_fields=[...])`` yapar).

    LDAP açıkken şifre dizine yazılır. Kullanıcının dizinde kaydı yoksa (eski hesap) kayıt açılır.
    Gölge kaydın şifre alanı her seferinde yeniden «kullanılamaz» yapılır; bu, kullanıcının diğer açık
    oturumlarını geçersiz kılar (Django oturum özeti şifre alanından türetilir)."""
    if not ldap_enabled():
        user.set_password(raw_password)
        return
    directory = get_directory()
    try:
        directory.set_password(user.username, raw_password)
    except DirectoryNotFound:
        directory.create(entry_from_user(user), raw_password)
    user.set_unusable_password()


def verify_password(user, raw_password):
    """Verilen şifre bu kullanıcının güncel şifresi mi?"""
    if not ldap_enabled():
        return user.check_password(raw_password)
    entry = get_directory().authenticate(user.username, raw_password)
    return bool(entry and entry.uid.lower() == user.username.lower())


def update_account(user):
    """Ad, e-posta, telefon ve rol değişikliğini dizine yansıtır (LDAP kapalıysa bir şey yapmaz)."""
    if not ldap_enabled():
        return
    directory = get_directory()
    if directory.find(user.username) is None:
        return  # henüz dizinde değil: ilk şifre atamasında oluşturulur
    directory.update(entry_from_user(user))


def delete_account(user):
    """Dizin kaydını siler (LDAP kapalıysa bir şey yapmaz). Dizinde kayıt yoksa sessizce geçer."""
    if ldap_enabled():
        get_directory().delete(user.username)


# --- Dizin -> Django gölge kaydı -----------------------------------------------
def sync_user(entry):
    """Dizin kaydına karşılık gelen Django kullanıcısını oluşturur/günceller ve döndürür.

    Dizin, kimlik bilgisinin (rol, e-posta, ad, telefon, şifre) tek kaynağıdır. ``is_active`` ise uygulamada
    kalır: yönetici bir hesabı pasife alırsa dizinde kaydı olsa bile giriş yapamaz."""
    user = User.objects.filter(username__iexact=entry.uid).first()
    if user is None and entry.email:
        user = User.objects.filter(email__iexact=entry.email).first()
    created = user is None
    if created:
        user = User(username=entry.uid, is_temporary_password=False)
    fields = {
        "role": entry.role or user.role,
        "first_name": entry.first_name[:150],
        "last_name": entry.last_name[:150],
        "phone": entry.phone[:20],
        "is_staff": entry.role == User.Role.ADMIN,
        "is_superuser": entry.role == User.Role.ADMIN,
    }
    for name, value in fields.items():
        setattr(user, name, value)
    if entry.email and not User.objects.filter(email__iexact=entry.email).exclude(pk=user.pk).exists():
        user.email = entry.email
    user.set_unusable_password()
    user.save()
    return user
