"""LDAP erişim katmanı testleri (Django gerektirmez, gerçek LDAP sunucusu gerektirmez).

LdapDirectory, ldap3'ün bellek içi küçük bir sahtesiyle sınanır; sahte, ldap3'ün kullandığımız arayüzünü
(Server/Connection/search/add/modify/modify_dn/delete, result/response) taklit eder.
Çalıştırma:  python -m unittest core.tests_directory      (ya da manage.py test core)
"""
import re
import sys
import types
import unittest

from . import directory as d

BASE = "dc=fixlab,dc=local"
ADMIN_DN, ADMIN_PW = f"cn=admin,{BASE}", "adminpw"


# --------------------------------------------------------------------------
# ldap3 sahtesi
# --------------------------------------------------------------------------
class FakeLDAPException(Exception):
    pass


class FakeStore:
    def __init__(self):
        self.entries = {BASE.lower(): {"dn": BASE, "attributes": {"objectClass": ["dcObject"]}}}
        self.down = False


def _parse_filter(text):
    """(&(a=b)(|(c=d)(e=f))) -> iç içe demet; yalnızca eşitlik, &, |, ! desteklenir."""
    pos = 0

    def parse():
        nonlocal pos
        assert text[pos] == "(", text
        pos += 1
        if text[pos] in "&|!":
            op = text[pos]
            pos += 1
            kids = []
            while text[pos] == "(":
                kids.append(parse())
            pos += 1
            return (op, kids)
        end = text.index(")", pos)
        attr, _, raw = text[pos:end].partition("=")
        pos = end + 1
        wildcard = raw == "*"  # kaçırılmamış tek "*": varlık sorgusu
        value = re.sub(r"\\([0-9a-f]{2})", lambda m: chr(int(m.group(1), 16)), raw)
        return ("=", attr, value, wildcard)

    return parse()


def _match(node, attrs):
    if node[0] == "&":
        return all(_match(k, attrs) for k in node[1])
    if node[0] == "|":
        return any(_match(k, attrs) for k in node[1])
    if node[0] == "!":
        return not _match(node[1][0], attrs)
    _, attr, value, wildcard = node
    for key, vals in attrs.items():
        if key.lower() == attr.lower():
            vals = vals if isinstance(vals, list) else [vals]
            return wildcard or any(str(v).lower() == value.lower() for v in vals)
    return False


def make_fake_ldap3(store):
    mod = types.ModuleType("ldap3")
    mod.NONE, mod.SUBTREE, mod.BASE, mod.MODIFY_REPLACE = "NONE", "SUBTREE", "BASE", "MODIFY_REPLACE"

    class Server:
        def __init__(self, url, get_info=None, connect_timeout=None):
            self.url = url

    class Connection:
        def __init__(self, server, user=None, password=None, receive_timeout=None, raise_exceptions=False):
            self.user, self.password = user, password
            self.result, self.response, self.bound = {}, [], False

        def open(self):
            if store.down:
                raise FakeLDAPException("sunucu kapalı")

        def start_tls(self):
            return True

        def bind(self):
            if store.down:
                raise FakeLDAPException("sunucu kapalı")
            if self.user == ADMIN_DN:
                ok = self.password == ADMIN_PW
            else:
                entry = store.entries.get((self.user or "").lower())
                # anonim bağlantıya (kullanıcı/şifre yok) izin verilir: sahte, gerçek sunucu gibi davranır
                ok = (not self.user and not self.password) or bool(
                    entry and self.password and d.ssha_verify(self.password, entry["attributes"].get("userPassword", [""])[0]))
            self.result = {"result": 0 if ok else 49, "description": "success" if ok else "invalidCredentials"}
            self.bound = ok
            return ok

        def unbind(self):
            self.bound = False

        def _done(self, code=0, description="success"):
            self.result = {"result": code, "description": description}
            return code == 0

        def search(self, search_base, search_filter, search_scope="SUBTREE", attributes=None):
            self.response = []
            base = search_base.lower()
            if base not in store.entries:
                return self._done(32, "noSuchObject")
            node = _parse_filter(search_filter) if search_filter != "(objectClass=*)" else None
            for key, entry in store.entries.items():
                inside = key == base or key.endswith("," + base)
                if search_scope == "BASE":
                    inside = key == base
                if inside and (node is None or _match(node, entry["attributes"])):
                    attrs = {k: v for k, v in entry["attributes"].items() if k != "userPassword"}
                    self.response.append({"type": "searchResEntry", "dn": entry["dn"], "attributes": attrs})
            return bool(self.response) and self._done()

        def add(self, dn, object_class, attributes):
            if dn.lower() in store.entries:
                return self._done(68, "entryAlreadyExists")
            parent = re.split(r"(?<!\\),", dn, maxsplit=1)[1].lower()  # kaçırılmış virgülde bölünmez
            if dn.lower() != BASE.lower() and parent not in store.entries:
                return self._done(32, "noSuchObject")
            attrs = {k: (v if isinstance(v, list) else [v]) for k, v in attributes.items()}
            attrs["objectClass"] = list(object_class)
            store.entries[dn.lower()] = {"dn": dn, "attributes": attrs}
            return self._done()

        def modify(self, dn, changes):
            entry = store.entries.get(dn.lower())
            if not entry:
                return self._done(32, "noSuchObject")
            for attr, ops in changes.items():
                for op, values in ops:
                    assert op == "MODIFY_REPLACE"
                    if values:
                        entry["attributes"][attr] = list(values)
                    else:
                        entry["attributes"].pop(attr, None)
            return self._done()

        def modify_dn(self, dn, relative_dn, new_superior=None):
            entry = store.entries.pop(dn.lower(), None)
            if not entry:
                return self._done(32, "noSuchObject")
            new_dn = f"{relative_dn},{new_superior}"
            entry["dn"] = new_dn
            store.entries[new_dn.lower()] = entry
            return self._done()

        def delete(self, dn):
            if store.entries.pop(dn.lower(), None) is None:
                return self._done(32, "noSuchObject")
            return self._done()

    mod.Server, mod.Connection = Server, Connection
    core_mod, exc_mod = types.ModuleType("ldap3.core"), types.ModuleType("ldap3.core.exceptions")
    exc_mod.LDAPException = FakeLDAPException
    core_mod.exceptions = exc_mod
    mod.core = core_mod
    return {"ldap3": mod, "ldap3.core": core_mod, "ldap3.core.exceptions": exc_mod}


# --------------------------------------------------------------------------
# Testler
# --------------------------------------------------------------------------
class HelperTests(unittest.TestCase):
    def test_ssha_roundtrip_and_is_salted(self):
        a, b = d.ssha("Sifre#123456"), d.ssha("Sifre#123456")
        self.assertTrue(a.startswith("{SSHA}"))
        self.assertNotEqual(a, b)
        self.assertTrue(d.ssha_verify("Sifre#123456", a))
        self.assertFalse(d.ssha_verify("yanlis", a))

    def test_filter_escape_blocks_injection(self):
        self.assertEqual(d.escape_filter("a*)(uid=*"), "a\\2a\\29\\28uid=\\2a")
        self.assertEqual(d.escape_filter("a\\b"), "a\\5cb")

    def test_rdn_escape(self):
        self.assertEqual(d.escape_rdn("a,b+c"), "a\\,b\\+c")
        self.assertEqual(d.escape_rdn("#x"), "\\#x")
        self.assertEqual(d.escape_rdn("ayse@example.com"), "ayse@example.com")

    def test_entry_names(self):
        e = d.DirectoryEntry("u1", "customer")
        self.assertEqual((e.common_name, e.surname), ("u1", "u1"))
        e = d.DirectoryEntry("u1", "customer", first_name="Ayşe")
        self.assertEqual((e.common_name, e.surname), ("Ayşe", "Ayşe"))


class _DirectoryContract:
    """Hem MemoryDirectory hem LdapDirectory aynı davranışı göstermelidir."""

    def entry(self, uid="ayse.demir", role="technician", email="ayse@example.com"):
        return d.DirectoryEntry(uid, role, email, "Ayşe", "Demir", "0532 111 22 33")

    def test_create_find_authenticate(self):
        self.dir.create(self.entry(), "Gizli#Sifre1")
        found = self.dir.find("ayse.demir")
        self.assertEqual((found.uid, found.role, found.email, found.last_name), ("ayse.demir", "technician", "ayse@example.com", "Demir"))
        self.assertEqual(self.dir.find("AYSE@example.com").uid, "ayse.demir")  # e-posta ile de bulunur
        self.assertEqual(self.dir.authenticate("ayse.demir", "Gizli#Sifre1").uid, "ayse.demir")
        self.assertEqual(self.dir.authenticate("ayse@example.com", "Gizli#Sifre1").uid, "ayse.demir")
        self.assertIsNone(self.dir.authenticate("ayse.demir", "yanlis"))
        self.assertIsNone(self.dir.authenticate("yok.kisi", "Gizli#Sifre1"))

    def test_empty_password_and_identifier_never_authenticate(self):
        self.dir.create(self.entry(), "Gizli#Sifre1")
        for identifier, password in (("ayse.demir", ""), ("ayse.demir", None), ("", "Gizli#Sifre1"), (None, None)):
            self.assertIsNone(self.dir.authenticate(identifier, password))

    def test_wildcards_do_not_match_everyone(self):
        self.dir.create(self.entry(), "Gizli#Sifre1")
        self.assertIsNone(self.dir.find("*"))
        self.assertIsNone(self.dir.authenticate("*", "Gizli#Sifre1"))
        self.assertIsNone(self.dir.find("ayse.demir)(uid=*"))

    def test_roles_follow_ou(self):
        for uid, role, mail in (("a", "admin", "a@x.com"), ("t", "technician", "t@x.com"), ("c@x.com", "customer", "c@x.com")):
            self.dir.create(self.entry(uid, role, mail), "Gizli#Sifre1")
        self.assertEqual({e.uid: e.role for e in self.dir.list_users()},
                         {"a": "admin", "t": "technician", "c@x.com": "customer"})
        self.assertEqual(self.dir.find("c@x.com").role, "customer")

    def test_duplicates_rejected(self):
        self.dir.create(self.entry(), "Gizli#Sifre1")
        with self.assertRaises(d.DirectoryConflict) as ctx:
            self.dir.create(self.entry(email="baska@example.com"), "Gizli#Sifre1")
        self.assertEqual(ctx.exception.field, "uid")
        with self.assertRaises(d.DirectoryConflict) as ctx:
            self.dir.create(self.entry(uid="baska"), "Gizli#Sifre1")
        self.assertEqual(ctx.exception.field, "mail")

    def test_invalid_input_rejected(self):
        with self.assertRaises(d.DirectoryError):
            self.dir.create(self.entry(role="hacker"), "Gizli#Sifre1")
        with self.assertRaises(d.DirectoryError):
            self.dir.create(self.entry(), "")

    def test_set_password(self):
        self.dir.create(self.entry(), "Eski#Sifre123")
        self.dir.set_password("ayse.demir", "Yeni#Sifre456")
        self.assertIsNone(self.dir.authenticate("ayse.demir", "Eski#Sifre123"))
        self.assertIsNotNone(self.dir.authenticate("ayse.demir", "Yeni#Sifre456"))
        with self.assertRaises(d.DirectoryNotFound):
            self.dir.set_password("yok", "Yeni#Sifre456")
        with self.assertRaises(d.DirectoryError):
            self.dir.set_password("ayse.demir", "")

    def test_update_and_role_move(self):
        self.dir.create(self.entry(), "Gizli#Sifre1")
        changed = self.entry(role="admin", email="yeni@example.com")
        changed.phone = ""
        changed.last_name = "Yılmaz"
        self.dir.update(changed)
        found = self.dir.find("ayse.demir")
        self.assertEqual((found.role, found.email, found.last_name, found.phone), ("admin", "yeni@example.com", "Yılmaz", ""))
        self.assertIsNotNone(self.dir.authenticate("ayse.demir", "Gizli#Sifre1"))  # şifre taşınınca korunur
        with self.assertRaises(d.DirectoryNotFound):
            self.dir.update(self.entry(uid="yok"))

    def test_delete(self):
        self.dir.create(self.entry(), "Gizli#Sifre1")
        self.assertTrue(self.dir.delete("ayse.demir"))
        self.assertFalse(self.dir.delete("ayse.demir"))
        self.assertIsNone(self.dir.find("ayse.demir"))
        self.assertIsNone(self.dir.authenticate("ayse.demir", "Gizli#Sifre1"))

    def test_special_characters_in_uid(self):
        self.dir.create(self.entry(uid="o'brien, jr+x", email="ob@example.com"), "Gizli#Sifre1")
        self.assertIsNotNone(self.dir.authenticate("o'brien, jr+x", "Gizli#Sifre1"))


class MemoryDirectoryTests(_DirectoryContract, unittest.TestCase):
    def setUp(self):
        self.dir = d.MemoryDirectory()


class LdapDirectoryTests(_DirectoryContract, unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self._saved = {k: sys.modules.get(k) for k in ("ldap3", "ldap3.core", "ldap3.core.exceptions")}
        sys.modules.update(make_fake_ldap3(self.store))
        self.dir = d.LdapDirectory("ldap://fake:389", BASE, ADMIN_DN, ADMIN_PW)
        self.assertEqual(self.dir.ensure_structure(), ["admins", "technicians", "customers"])

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value

    def test_ensure_structure_creates_missing_base_entry(self):
        fresh = FakeStore()
        fresh.entries.clear()  # kök kayıt bile yok (bazı LDAP imajları oluşturmaz)
        sys.modules.update(make_fake_ldap3(fresh))
        directory = d.LdapDirectory("ldap://fake:389", BASE, ADMIN_DN, ADMIN_PW)
        self.assertEqual(directory.ensure_structure(), [BASE, "admins", "technicians", "customers"])
        directory.create(self.entry(), "Gizli#Sifre1")
        self.assertIsNotNone(directory.authenticate("ayse.demir", "Gizli#Sifre1"))
        self.assertEqual(directory.ensure_structure(), [])

    def test_ensure_structure_is_idempotent(self):
        self.assertEqual(self.dir.ensure_structure(), [])

    def test_server_down_raises_unavailable(self):
        self.store.down = True
        with self.assertRaises(d.DirectoryUnavailable):
            self.dir.find("x")
        with self.assertRaises(d.DirectoryUnavailable):
            self.dir.ping()

    def test_wrong_service_account_raises_unavailable(self):
        bad = d.LdapDirectory("ldap://fake:389", BASE, ADMIN_DN, "yanlis")
        with self.assertRaises(d.DirectoryUnavailable):
            bad.ping()

    def test_ping_ok(self):
        self.dir.ping()

    def test_password_stored_hashed_not_plain(self):
        self.dir.create(self.entry(), "Gizli#Sifre1")
        stored = self.store.entries[f"uid=ayse.demir,ou=technicians,{BASE}".lower()]["attributes"]["userPassword"][0]
        self.assertTrue(stored.startswith("{SSHA}"))
        self.assertNotIn("Gizli", stored)

    def test_entries_outside_known_ous_are_ignored(self):
        self.store.entries[f"uid=x,ou=baska,{BASE}".lower()] = {
            "dn": f"uid=x,ou=baska,{BASE}", "attributes": {"uid": ["x"], "mail": ["x@x.com"], "objectClass": ["inetOrgPerson"]}}
        self.assertIsNone(self.dir.find("x"))

    def test_start_tls_path(self):
        tls = d.LdapDirectory("ldap://fake:389", BASE, ADMIN_DN, ADMIN_PW, start_tls=True)
        tls.ping()


if __name__ == "__main__":
    unittest.main()
