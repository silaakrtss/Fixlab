"""Django kimlik doğrulama backend'i: şifreyi LDAP sunucusunda doğrular."""
import logging

from django.contrib.auth.backends import ModelBackend

from . import accounts
from .directory import DirectoryError, DirectoryUnavailable

logger = logging.getLogger(__name__)


class LdapBackend(ModelBackend):
    """LDAP_ENABLED=1 iken kullanıcı adı (ya da e-posta) + şifreyi dizinde doğrular.

    Başarılı girişte Django'daki gölge kullanıcı eşitlenir. İzinler (has_perm) ModelBackend'den gelir."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not accounts.ldap_enabled() or not username or not password:
            return None
        try:
            entry = accounts.get_directory().authenticate(username.strip(), password)
        except DirectoryUnavailable:
            logger.exception("LDAP sunucusuna ulaşılamadı")
            if request is not None:
                request.directory_unavailable = True  # giriş formu «ulaşılamıyor» mesajı gösterir
            return None
        except DirectoryError:
            logger.exception("LDAP doğrulama hatası")
            return None
        if entry is None:
            return None
        user = accounts.sync_user(entry)
        return user if self.user_can_authenticate(user) else None
