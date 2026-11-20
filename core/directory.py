"""Dizin (LDAP) erişim katmanı — FIXLAB kullanıcı kimlikleri burada tutulur.

Bu dosya Django'dan bağımsızdır (yalnızca standart kütüphane + isteğe bağlı ``ldap3``).
  * LdapDirectory   : gerçek LDAP sunucusu (OpenLDAP vb.) — ldap3 kütüphanesiyle
  * MemoryDirectory : bellekte çalışan sahte dizin (testler ve LDAP'sız geliştirme için)

Dizin yapısı (kök: LDAP_BASE_DN):
    ou=admins       -> rol "admin"
    ou=technicians  -> rol "technician"
    ou=customers    -> rol "customer"
Her kullanıcı bir ``inetOrgPerson`` kaydıdır: uid, cn, sn, givenName, mail, telephoneNumber, userPassword.
Kullanıcının rolü, bulunduğu OU'dan belirlenir.
"""
import base64
import hashlib
import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ROLES = ("admin", "technician", "customer")
ATTRIBUTES = ["uid", "cn", "sn", "givenName", "mail", "telephoneNumber"]


# --------------------------------------------------------------------------
# Hatalar
# --------------------------------------------------------------------------
class DirectoryError(Exception):
    """Dizin işlemi başarısız."""


class DirectoryUnavailable(DirectoryError):
    """Dizin sunucusuna ulaşılamıyor ya da hizmet hesabıyla bağlanılamıyor."""


class DirectoryConflict(DirectoryError):
    """Aynı kullanıcı adı ya da e-posta dizinde zaten var. ``field``: 'uid' | 'mail'."""

    def __init__(self, message, field="uid"):
        super().__init__(message)
        self.field = field


class DirectoryNotFound(DirectoryError):
    """Kayıt dizinde bulunamadı."""


# --------------------------------------------------------------------------
# Veri tipi ve yardımcılar
# --------------------------------------------------------------------------
@dataclass
class DirectoryEntry:
    uid: str
    role: str = ""
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    phone: str = ""
    dn: str = ""

    @property
    def common_name(self):
        return (f"{self.first_name} {self.last_name}").strip() or self.uid

    @property
    def surname(self):
        # inetOrgPerson için sn zorunludur
        return self.last_name or self.first_name or self.uid


def ssha(password, salt=None):
    """OpenLDAP'ın varsayılan olarak tanıdığı {SSHA} (tuzlu SHA-1) şifre özeti."""
    salt = salt if salt is not None else os.urandom(8)
    digest = hashlib.sha1(password.encode("utf-8") + salt).digest()
    return "{SSHA}" + base64.b64encode(digest + salt).decode("ascii")


def ssha_verify(password, hashed):
    if not hashed.startswith("{SSHA}"):
        return False
    raw = base64.b64decode(hashed[6:])
    digest, salt = raw[:20], raw[20:]
    return hashlib.sha1(password.encode("utf-8") + salt).digest() == digest


def escape_filter(value):
    """LDAP arama filtresi için özel karakterleri kaçırır (LDAP injection koruması, RFC 4515)."""
    out = []
    for ch in str(value):
        if ch in "\\*()\x00":
            out.append("\\%02x" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


def escape_rdn(value):
    """DN içindeki bir değeri kaçırır (RFC 4514)."""
    value = str(value).replace("\\", "\\\\")
    for ch in ',+"<>;=':
        value = value.replace(ch, "\\" + ch)
    if value.startswith("#") or value.startswith(" "):
        value = "\\" + value
    if value.endswith(" "):
        value = value[:-1] + "\\ "
    return value


def _ldap3():
    try:
        import ldap3
        from ldap3.core.exceptions import LDAPException
    except ImportError as exc:  # pragma: no cover
        raise DirectoryError("ldap3 paketi kurulu değil: pip install -r requirements.txt") from exc
    return ldap3, LDAPException


def _pick(attributes, name):
    """Öznitelik sözlüğünden (büyük/küçük harf duyarsız) ilk değeri metin olarak döndürür."""
    for key, value in (attributes or {}).items():
        if key.lower() == name.lower():
            if isinstance(value, (list, tuple)):
                value = value[0] if value else ""
            if isinstance(value, bytes):
                value = value.decode("utf-8", "replace")
            return str(value or "")
    return ""


# --------------------------------------------------------------------------
# Ortak arayüz
# --------------------------------------------------------------------------
class BaseDirectory:
    def authenticate(self, identifier, password):
        """Kullanıcı adı/e-posta + şifre doğruysa DirectoryEntry, değilse None."""
        raise NotImplementedError

    def find(self, identifier):
        """uid ya da e-posta ile kayıt bulur; yoksa (ya da belirsizse) None."""
        raise NotImplementedError

    def create(self, entry, password):
        raise NotImplementedError

    def set_password(self, uid, password):
        raise NotImplementedError

    def update(self, entry):
        """Ad, e-posta, telefon ve (değiştiyse) rol/OU bilgisini günceller."""
        raise NotImplementedError

    def delete(self, uid):
        raise NotImplementedError

    def list_users(self):
        raise NotImplementedError

    def ensure_structure(self):
        """Gerekli OU'ları oluşturur; yeni oluşturulanların adını döndürür."""
        raise NotImplementedError

    def ping(self):
        """Sunucuya ulaşılabiliyor mu? Değilse DirectoryUnavailable."""
        raise NotImplementedError

    @staticmethod
    def _check_new(entry, password, existing_uid, existing_mail):
        if entry.role not in ROLES:
            raise DirectoryError(f"Geçersiz rol: {entry.role!r}")
        if not entry.uid:
            raise DirectoryError("Kullanıcı adı boş olamaz.")
        if not password:
            raise DirectoryError("Şifre boş olamaz.")
        if existing_uid:
            raise DirectoryConflict(f"«{entry.uid}» kullanıcı adı dizinde zaten var.", "uid")
        if entry.email and existing_mail:
            raise DirectoryConflict(f"{entry.email} e-postası dizinde zaten kayıtlı.", "mail")


# --------------------------------------------------------------------------
# Bellek içi dizin (test / geliştirme)
# --------------------------------------------------------------------------
class MemoryDirectory(BaseDirectory):
    def __init__(self, base_dn="dc=fixlab,dc=local"):
        self.base_dn = base_dn
        self._users = {}  # uid(küçük harf) -> [DirectoryEntry, şifre özeti]

    def _dn(self, entry):
        return f"uid={escape_rdn(entry.uid)},ou={entry.role}s,{self.base_dn}"

    def _lookup(self, identifier):
        key = (identifier or "").strip().lower()
        if not key:
            return None
        if key in self._users:
            return self._users[key]
        matches = [v for v in self._users.values() if v[0].email.lower() == key]
        return matches[0] if len(matches) == 1 else None

    def find(self, identifier):
        item = self._lookup(identifier)
        return item[0] if item else None

    def authenticate(self, identifier, password):
        if not identifier or not password:
            return None
        item = self._lookup(identifier)
        if item and ssha_verify(password, item[1]):
            return item[0]
        return None

    def create(self, entry, password):
        existing_uid = self._users.get(entry.uid.lower())
        existing_mail = any(v[0].email.lower() == entry.email.lower() for v in self._users.values()) if entry.email else False
        self._check_new(entry, password, existing_uid, existing_mail)
        stored = DirectoryEntry(entry.uid, entry.role, entry.email, entry.first_name, entry.last_name, entry.phone)
        stored.dn = self._dn(stored)
        self._users[entry.uid.lower()] = [stored, ssha(password)]
        return stored

    def set_password(self, uid, password):
        item = self._users.get((uid or "").lower())
        if not item:
            raise DirectoryNotFound(f"«{uid}» dizinde yok.")
        if not password:
            raise DirectoryError("Şifre boş olamaz.")
        item[1] = ssha(password)

    def update(self, entry):
        item = self._users.get(entry.uid.lower())
        if not item:
            raise DirectoryNotFound(f"«{entry.uid}» dizinde yok.")
        stored = item[0]
        stored.email, stored.first_name, stored.last_name, stored.phone = (
            entry.email, entry.first_name, entry.last_name, entry.phone)
        if entry.role in ROLES:
            stored.role = entry.role
        stored.dn = self._dn(stored)

    def delete(self, uid):
        return self._users.pop((uid or "").lower(), None) is not None

    def list_users(self):
        return [v[0] for v in self._users.values()]

    def ensure_structure(self):
        return []

    def ping(self):
        return None


# --------------------------------------------------------------------------
# Gerçek LDAP sunucusu (ldap3)
# --------------------------------------------------------------------------
class LdapDirectory(BaseDirectory):
    def __init__(self, url, base_dn, bind_dn, bind_password, ou_names=None, start_tls=False, timeout=5):
        self.url = url
        self.base_dn = base_dn
        self.bind_dn = bind_dn
        self.bind_password = bind_password
        self.ou_names = ou_names or {"admin": "admins", "technician": "technicians", "customer": "customers"}
        self.start_tls = start_tls
        self.timeout = timeout

    # --- bağlantı ---------------------------------------------------------
    def _connection(self, user=None, password=None):
        """Bağlı Connection döndürür; kimlik bilgisi yanlışsa None; sunucuya ulaşılamazsa DirectoryUnavailable."""
        ldap3, LDAPException = _ldap3()
        try:
            server = ldap3.Server(self.url, get_info=ldap3.NONE, connect_timeout=self.timeout)
            conn = ldap3.Connection(server, user=user, password=password, receive_timeout=self.timeout,
                                    raise_exceptions=False)
            if self.start_tls:
                conn.open()
                conn.start_tls()
            if not conn.bind():
                code = (conn.result or {}).get("result")
                conn.unbind()
                if code == 49:  # invalidCredentials
                    return None
                raise DirectoryUnavailable(f"LDAP bağlantısı kurulamadı ({(conn.result or {}).get('description', '?')}).")
            return conn
        except LDAPException as exc:
            raise DirectoryUnavailable(f"LDAP sunucusuna ulaşılamıyor: {exc}") from exc

    def _service(self):
        conn = self._connection(self.bind_dn, self.bind_password)
        if conn is None:
            raise DirectoryUnavailable("LDAP hizmet hesabı (LDAP_BIND_DN / LDAP_BIND_PASSWORD) reddedildi.")
        return conn

    def _run(self, action):
        """Hizmet hesabıyla bir işlem çalıştırır, bağlantıyı her durumda kapatır."""
        ldap3, LDAPException = _ldap3()
        conn = self._service()
        try:
            return action(conn, ldap3)
        except LDAPException as exc:
            raise DirectoryError(f"LDAP işlemi başarısız: {exc}") from exc
        finally:
            try:
                conn.unbind()
            except Exception:  # pragma: no cover
                pass

    # --- DN / çözümleme ---------------------------------------------------
    def ou_dn(self, role):
        return f"ou={self.ou_names[role]},{self.base_dn}"

    def dn_for(self, uid, role):
        return f"uid={escape_rdn(uid)},{self.ou_dn(role)}"

    def _role_from_dn(self, dn):
        suffix = ("," + self.base_dn).lower()
        low = dn.lower()
        if not low.endswith(suffix):
            return ""
        match = re.search(r",ou=([^,]+)$", low[:-len(suffix)])
        if not match:
            return ""
        for role, name in self.ou_names.items():
            if name.lower() == match.group(1):
                return role
        return ""

    def _to_entry(self, item):
        attrs = item.get("attributes") or {}
        dn = item.get("dn", "")
        return DirectoryEntry(
            uid=_pick(attrs, "uid"), role=self._role_from_dn(dn), email=_pick(attrs, "mail"),
            first_name=_pick(attrs, "givenName"), last_name=_pick(attrs, "sn"),
            phone=_pick(attrs, "telephoneNumber"), dn=dn)

    def _search(self, conn, ldap3, search_filter, base=None, scope=None):
        conn.search(search_base=base or self.base_dn, search_filter=search_filter,
                    search_scope=scope or ldap3.SUBTREE, attributes=ATTRIBUTES)
        code = (conn.result or {}).get("result", 0)
        if code == 32:  # noSuchObject: taban yok -> sonuç da yok
            return []
        if code != 0:
            raise DirectoryError(f"LDAP araması başarısız: {(conn.result or {}).get('description', code)}")
        return [self._to_entry(i) for i in (conn.response or []) if i.get("type") == "searchResEntry"]

    def _find_with(self, conn, ldap3, identifier):
        value = escape_filter((identifier or "").strip())
        if not value:
            return None
        found = self._search(conn, ldap3, f"(&(objectClass=inetOrgPerson)(|(uid={value})(mail={value})))")
        found = [e for e in found if e.role]  # tanımlı OU dışındaki kayıtlar yok sayılır
        return found[0] if len(found) == 1 else None

    # --- BaseDirectory ----------------------------------------------------
    def find(self, identifier):
        return self._run(lambda conn, ldap3: self._find_with(conn, ldap3, identifier))

    def authenticate(self, identifier, password):
        if not identifier or not password:  # boş şifre = anonim bağlantı; asla kabul edilmez
            return None
        entry = self.find(identifier)
        if entry is None:
            return None
        conn = self._connection(entry.dn, password)
        if conn is None:
            return None
        conn.unbind()
        return entry

    def create(self, entry, password):
        def action(conn, ldap3):
            existing_uid = self._find_with(conn, ldap3, entry.uid)
            existing_mail = self._find_with(conn, ldap3, entry.email) if entry.email else None
            self._check_new(entry, password, existing_uid, existing_mail)
            dn = self.dn_for(entry.uid, entry.role)
            attrs = {"uid": entry.uid, "cn": entry.common_name, "sn": entry.surname, "userPassword": ssha(password)}
            if entry.email:
                attrs["mail"] = entry.email
            if entry.first_name:
                attrs["givenName"] = entry.first_name
            if entry.phone:
                attrs["telephoneNumber"] = entry.phone
            if not conn.add(dn, ["inetOrgPerson"], attrs):
                description = (conn.result or {}).get("description", "")
                if description == "entryAlreadyExists":
                    raise DirectoryConflict(f"«{entry.uid}» kullanıcı adı dizinde zaten var.", "uid")
                raise DirectoryError(f"Dizin kaydı oluşturulamadı: {description}")
            return DirectoryEntry(entry.uid, entry.role, entry.email, entry.first_name, entry.last_name, entry.phone, dn)
        return self._run(action)

    def set_password(self, uid, password):
        if not password:
            raise DirectoryError("Şifre boş olamaz.")

        def action(conn, ldap3):
            entry = self._find_with(conn, ldap3, uid)
            if entry is None:
                raise DirectoryNotFound(f"«{uid}» dizinde yok.")
            if not conn.modify(entry.dn, {"userPassword": [(ldap3.MODIFY_REPLACE, [ssha(password)])]}):
                raise DirectoryError(f"Şifre güncellenemedi: {(conn.result or {}).get('description', '?')}")
        return self._run(action)

    def update(self, entry):
        def action(conn, ldap3):
            current = self._find_with(conn, ldap3, entry.uid)
            if current is None:
                raise DirectoryNotFound(f"«{entry.uid}» dizinde yok.")
            dn = current.dn
            if entry.role in ROLES and entry.role != current.role:  # rol değişti: kaydı yeni OU'ya taşı
                if not conn.modify_dn(dn, f"uid={escape_rdn(current.uid)}", new_superior=self.ou_dn(entry.role)):
                    raise DirectoryError(f"Kayıt taşınamadı: {(conn.result or {}).get('description', '?')}")
                dn = self.dn_for(current.uid, entry.role)
            changes = {"cn": [(ldap3.MODIFY_REPLACE, [entry.common_name])],
                       "sn": [(ldap3.MODIFY_REPLACE, [entry.surname])]}
            for attr, value in (("mail", entry.email), ("givenName", entry.first_name),
                                ("telephoneNumber", entry.phone)):
                changes[attr] = [(ldap3.MODIFY_REPLACE, [value] if value else [])]  # boş değer özniteliği siler
            if not conn.modify(dn, changes):
                raise DirectoryError(f"Kayıt güncellenemedi: {(conn.result or {}).get('description', '?')}")
        return self._run(action)

    def delete(self, uid):
        def action(conn, ldap3):
            entry = self._find_with(conn, ldap3, uid)
            if entry is None:
                return False
            if not conn.delete(entry.dn):
                raise DirectoryError(f"Kayıt silinemedi: {(conn.result or {}).get('description', '?')}")
            return True
        return self._run(action)

    def list_users(self):
        def action(conn, ldap3):
            return [e for e in self._search(conn, ldap3, "(objectClass=inetOrgPerson)") if e.role]
        return self._run(action)

    def ensure_structure(self):
        def action(conn, ldap3):
            created = []
            # Kök kayıt (dc=...) yoksa oluştur: LDAP sunucusu imajına bağlı kalmayalım.
            self._search(conn, ldap3, "(objectClass=*)", base=self.base_dn, scope=ldap3.BASE)
            first_rdn = self.base_dn.split(",")[0]
            if (conn.result or {}).get("result") == 32 and first_rdn.lower().startswith("dc="):
                value = first_rdn.split("=", 1)[1]
                if not conn.add(self.base_dn, ["top", "dcObject", "organization"], {"dc": value, "o": value}):
                    raise DirectoryError(f"{self.base_dn} oluşturulamadı: {(conn.result or {}).get('description', '?')}")
                created.append(self.base_dn)
            for role in ROLES:
                dn = self.ou_dn(role)
                self._search(conn, ldap3, "(objectClass=*)", base=dn, scope=ldap3.BASE)
                code = (conn.result or {}).get("result", 0)
                if code == 32:
                    if not conn.add(dn, ["organizationalUnit"], {"ou": self.ou_names[role]}):
                        raise DirectoryError(f"{dn} oluşturulamadı: {(conn.result or {}).get('description', '?')}")
                    created.append(self.ou_names[role])
            return created
        return self._run(action)

    def ping(self):
        conn = self._service()
        conn.unbind()
