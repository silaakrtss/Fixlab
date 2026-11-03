import datetime
import re

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import AuditLog, Device, EmailVerificationCode, User


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class OnboardingFlowTests(TestCase):
    def setUp(self):
        self.admin_password = "FixLab#Admin2025"

    def _create_technician(self):
        self.client.login(username="admin", password=self.admin_password)
        response = self.client.post(reverse("core:staff_create"), {
            "role": "technician", "first_name": "Şükrü", "last_name": "Çelik", "email": "sukru@example.com",
            "phone": "0532 123 45 67", "specialization": "Anakart",
        })
        self.assertRedirects(response, reverse("core:staff_list"))
        self.client.logout()

    def test_default_admin_created_by_migration(self):
        admin = User.objects.get(username="admin")
        self.assertEqual(admin.role, User.Role.ADMIN)
        self.assertTrue(admin.is_superuser)
        self.assertFalse(admin.is_temporary_password)
        self.assertTrue(admin.check_password(self.admin_password))

    def test_admin_login_goes_to_dashboard(self):
        response = self.client.post(reverse("core:login"), {"username": "admin", "password": self.admin_password})
        self.assertRedirects(response, reverse("core:home"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("core:admin_dashboard")).status_code, 200)

    def test_technician_creation_sends_email_with_credentials(self):
        self._create_technician()
        tech = User.objects.get(email="sukru@example.com")
        self.assertEqual(tech.username, "sukru.celik")
        self.assertTrue(tech.is_temporary_password)
        self.assertEqual(tech.role, User.Role.TECHNICIAN)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("sukru@example.com", body)       # mailde kullanıcı adı değil e-posta yazar
        self.assertNotIn("sukru.celik", body)
        self.assertNotIn("Kullanıcı adı", body)
        password = re.search(r"Geçici şifre: (\S+)", body).group(1)
        self.assertTrue(tech.check_password(password))
        self.assertGreaterEqual(len(password), 12)

    def test_staff_logs_in_with_email_only_except_first_admin(self):
        self._create_technician()
        password = re.search(r"Geçici şifre: (\S+)", mail.outbox[0].body).group(1)
        for identifier in ("sukru@example.com", "SUKRU@Example.com"):
            self.client.logout()
            response = self.client.post(reverse("core:login"), {"username": identifier, "password": password})
            self.assertEqual(response.status_code, 302, identifier)
        # Kullanıcı adı artık kabul edilmez (şifre doğru olsa bile)
        self.client.logout()
        by_username = self.client.post(reverse("core:login"), {"username": "sukru.celik", "password": password})
        self.assertEqual(by_username.status_code, 200)
        self.assertContains(by_username, "E-posta veya şifre hatalı.")
        self.assertNotIn("_auth_user_id", self.client.session)
        # İlk (varsayılan) admin kullanıcı adıyla girmeye devam eder
        first_admin = self.client.post(reverse("core:login"), {"username": "admin", "password": self.admin_password})
        self.assertEqual(first_admin.status_code, 302)
        self.client.logout()
        bad = self.client.post(reverse("core:login"), {"username": "sukru@example.com", "password": "yanlis-sifre"})
        self.assertEqual(bad.status_code, 200)
        self.assertContains(bad, "E-posta veya şifre hatalı.")

    def test_admin_created_by_admin_gets_email_in_mail(self):
        self.client.login(username="admin", password=self.admin_password)
        self.client.post(reverse("core:staff_create"), {
            "role": "admin", "first_name": "Sıla", "last_name": "Kurtaran", "email": "sila@example.com"})
        self.assertIn("sila@example.com", mail.outbox[-1].body)
        self.assertNotIn("sila.kurtaran", mail.outbox[-1].body)
        password = re.search(r"Geçici şifre: (\S+)", mail.outbox[-1].body).group(1)
        self.client.logout()
        response = self.client.post(reverse("core:login"), {"username": "sila@example.com", "password": password})
        self.assertEqual(response.status_code, 302)

    def test_duplicate_username_and_email_handled(self):
        self._create_technician()
        self.client.login(username="admin", password=self.admin_password)
        dup = self.client.post(reverse("core:staff_create"), {
            "role": "technician", "first_name": "Sukru", "last_name": "Celik", "email": "SUKRU@example.com"})
        self.assertEqual(dup.status_code, 200)  # form hatası: e-posta zaten var
        ok = self.client.post(reverse("core:staff_create"), {
            "role": "technician", "first_name": "Sukru", "last_name": "Celik", "email": "other@example.com"})
        self.assertRedirects(ok, reverse("core:staff_list"))
        self.assertTrue(User.objects.filter(username="sukru.celik2").exists())

    def test_forced_password_change_flow(self):
        self._create_technician()
        password = re.search(r"Geçici şifre: (\S+)", mail.outbox[0].body).group(1)

        response = self.client.post(reverse("core:login"), {"username": "sukru@example.com", "password": password})
        self.assertEqual(response.status_code, 302)

        # Hiçbir sayfaya erişemez, hepsi şifre değiştirmeye yönlenir
        for name in ("core:home", "core:technician_dashboard", "core:admin_dashboard"):
            self.assertRedirects(self.client.get(reverse(name)), reverse("core:force_password_change"),
                                 fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("core:force_password_change")).status_code, 200)

        # Zayıf/aynı şifre reddedilir
        bad = self.client.post(reverse("core:force_password_change"), {
            "old_password": password, "new_password1": password, "new_password2": password})
        self.assertEqual(bad.status_code, 200)
        self.assertTrue(User.objects.get(username="sukru.celik").is_temporary_password)
        weak = self.client.post(reverse("core:force_password_change"), {
            "old_password": password, "new_password1": "12345678", "new_password2": "12345678"})
        self.assertEqual(weak.status_code, 200)

        # Başarılı değişim
        good = self.client.post(reverse("core:force_password_change"), {
            "old_password": password, "new_password1": "Yeni-Sifre#2025", "new_password2": "Yeni-Sifre#2025"})
        self.assertRedirects(good, reverse("core:technician_dashboard"))
        tech = User.objects.get(username="sukru.celik")
        self.assertFalse(tech.is_temporary_password)
        self.assertTrue(tech.check_password("Yeni-Sifre#2025"))
        self.assertEqual(self.client.get(reverse("core:technician_dashboard")).status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.PASSWORD_CHANGE).exists())

    def test_technician_cannot_open_admin_pages(self):
        tech = User.objects.create_user("t1", "t1@x.com", "Pass#12345", role=User.Role.TECHNICIAN,
                                        is_temporary_password=False)
        self.client.force_login(tech)
        self.assertEqual(self.client.get(reverse("core:staff_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:staff_create")).status_code, 403)

    def test_resend_generates_new_password(self):
        self._create_technician()
        tech = User.objects.get(username="sukru.celik")
        tech.is_temporary_password = False
        tech.save()
        self.client.login(username="admin", password=self.admin_password)
        self.client.post(reverse("core:staff_resend", args=[tech.pk]))
        tech.refresh_from_db()
        self.assertTrue(tech.is_temporary_password)
        self.assertEqual(len(mail.outbox), 2)

    def test_tracking_id_generation(self):
        d1 = Device.objects.create(customer_name="A", customer_phone="05321234567", brand="HP", model="X", issue_description="y")
        d2 = Device.objects.create(customer_name="B", customer_phone="05321234567", brand="HP", model="Z", issue_description="y")
        self.assertRegex(d1.tracking_id, r"^TS-\d{4}-1001$")
        self.assertRegex(d2.tracking_id, r"^TS-\d{4}-1002$")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AdminCreationAndUiTests(TestCase):
    password = "FixLab#Admin2025"

    def _create(self, **extra):
        self.client.login(username="admin", password=self.password)
        data = {"role": "admin", "first_name": "Elif", "last_name": "Yıldız", "email": "elif@example.com"}
        data.update(extra)
        response = self.client.post(reverse("core:staff_create"), data)
        self.client.logout()
        return response

    def test_admin_can_create_admin_full_flow(self):
        response = self._create()
        self.assertRedirects(response, reverse("core:staff_list"), fetch_redirect_response=False)
        new_admin = User.objects.get(email="elif@example.com")
        self.assertEqual(new_admin.role, User.Role.ADMIN)
        self.assertTrue(new_admin.is_staff and new_admin.is_superuser)
        self.assertTrue(new_admin.is_temporary_password)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("admin hesabınız", mail.outbox[0].body)
        password = re.search(r"Geçici şifre: (\S+)", mail.outbox[0].body).group(1)

        # İlk giriş -> zorunlu şifre değiştirme, admin paneline erişemez
        self.client.post(reverse("core:login"), {"username": "elif@example.com", "password": password})
        self.assertRedirects(self.client.get(reverse("core:admin_dashboard")), reverse("core:force_password_change"),
                             fetch_redirect_response=False)
        done = self.client.post(reverse("core:force_password_change"), {
            "old_password": password, "new_password1": "Yeni-Admin#2026", "new_password2": "Yeni-Admin#2026"})
        self.assertRedirects(done, reverse("core:admin_dashboard"))
        self.assertFalse(User.objects.get(pk=new_admin.pk).is_temporary_password)
        self.assertEqual(self.client.get(reverse("core:staff_list")).status_code, 200)

    def test_technician_cannot_create_admin(self):
        tech = User.objects.create_user("t2", "t2@x.com", "Pass#12345", role=User.Role.TECHNICIAN,
                                        is_temporary_password=False)
        self.client.force_login(tech)
        r = self.client.post(reverse("core:staff_create"), {
            "role": "admin", "first_name": "A", "last_name": "B", "email": "a@b.com"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(User.objects.filter(email="a@b.com").exists())

    def test_invalid_role_rejected(self):
        self.client.login(username="admin", password=self.password)
        r = self.client.post(reverse("core:staff_create"), {
            "role": "customer", "first_name": "A", "last_name": "B", "email": "c@b.com"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(email="c@b.com").exists())

    def test_cannot_deactivate_or_reset_self(self):
        self.client.login(username="admin", password=self.password)
        me = User.objects.get(username="admin")
        self.client.post(reverse("core:staff_toggle", args=[me.pk]))
        self.client.post(reverse("core:staff_resend", args=[me.pk]))
        me.refresh_from_db()
        self.assertTrue(me.is_active)
        self.assertFalse(me.is_temporary_password)
        self.assertEqual(len(mail.outbox), 0)

    def test_legal_pages_public_and_linked_on_login(self):
        login = self.client.get(reverse("core:login"))
        self.assertContains(login, reverse("core:privacy"))
        self.assertContains(login, reverse("core:terms"))
        self.assertContains(self.client.get(reverse("core:privacy")), "KVKK")
        self.assertContains(self.client.get(reverse("core:terms")), "Güvenlik")

    def test_legal_pages_readable_with_temporary_password(self):
        self._create(role="technician", email="geci@example.com")
        tech = User.objects.get(email="geci@example.com")
        self.client.force_login(tech)
        self.assertEqual(self.client.get(reverse("core:privacy")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:terms")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:technician_dashboard")).status_code, 302)

    def test_user_card_and_logout(self):
        tech = User.objects.create_user("t3", "t3@x.com", "Pass#12345", role=User.Role.TECHNICIAN,
                                        first_name="ismail", last_name="Kaya", is_temporary_password=False)
        for user in (User.objects.get(username="admin"), tech):
            self.client.force_login(user)
            page = self.client.get(reverse("core:home"), follow=True)
            self.assertContains(page, "Çıkış Yap")
            self.assertContains(page, "Profil")
            self.assertContains(page, user.email)
            self.assertContains(page, reverse("core:privacy"))
        self.assertEqual(tech.avatar_letter, "İ")
        # Çıkış: POST ile oturum kapanır
        out = self.client.post(reverse("core:logout"))
        self.assertRedirects(out, reverse("core:login"))
        self.assertEqual(self.client.get(reverse("core:technician_dashboard")).status_code, 302)


class AdminDashboardTests(TestCase):
    """Dashboard tasarımındaki kartlar, son kayıtlar tablosu ve kritik stok."""

    def setUp(self):
        from .models import StockPart
        self.admin = User.objects.get(username="admin")
        self.client.force_login(self.admin)
        tech = User.objects.create_user("ayse.demir", "ayse@x.com", "Pass#12345", role=User.Role.TECHNICIAN,
                                        first_name="Ayşe", last_name="Demir", is_temporary_password=False)
        for name, status in [("Mehmet Kaya", "repairing"), ("Zeynep Aydın", "diagnosing"), ("Can Öztürk", "ready")]:
            Device.objects.create(customer_name=name, customer_phone="05321234567", brand="Dell", model="L5420",
                                  issue_description="x", status=status, technician=tech)
        StockPart.objects.create(name="USB-C şarj soketi", code="USBC", quantity=0, critical_level=5)

    def test_dashboard_shows_cards_table_and_stock(self):
        page = self.client.get(reverse("core:admin_dashboard"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["month_device_count"], 3)
        self.assertEqual(page.context["repairing_count"], 2)   # tamirde + inceleniyor
        self.assertEqual(page.context["ready_count"], 1)
        self.assertEqual(page.context["critical_count"], 1)
        for text in ("Dashboard", "Son kayıtlar", "Mehmet Kaya", "Ayşe Demir", "USB-C şarj soketi", "Son eylemler"):
            self.assertContains(page, text)

    def test_dashboard_links_still_work(self):
        page = self.client.get(reverse("core:admin_dashboard"))
        self.assertContains(page, reverse("core:staff_list"))
        self.assertContains(page, reverse("core:staff_create"))
        self.assertContains(page, "Çıkış Yap")

    def test_dashboard_only_for_admin(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("core:admin_dashboard")).status_code, 302)


class CustomerPortalTests(TestCase):
    VALID = {
        "first_name": "Ayşe", "last_name": "Demir", "email": "Ayse@Example.com", "phone": "0532 123 45 67",
        "password1": "GucluSifre#2026", "password2": "GucluSifre#2026", "accept_terms": "on",
    }

    def test_staff_login_has_customer_button_and_customer_pages_open(self):
        self.assertContains(self.client.get(reverse("core:login")), reverse("core:customer_login"))
        login = self.client.get(reverse("core:customer_login"))
        for name in ("customer_register", "customer_privacy", "customer_terms", "login"):
            self.assertContains(login, reverse(f"core:{name}"))
        for name in ("customer_register", "customer_privacy", "customer_terms"):
            self.assertEqual(self.client.get(reverse(f"core:{name}")).status_code, 200)
        self.assertContains(self.client.get(reverse("core:customer_privacy")), "Müşteri Girişine Dön")

    def test_register_requires_terms_checkbox(self):
        data = {**self.VALID}
        data.pop("accept_terms")
        response = self.client.post(reverse("core:customer_register"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="ayse@example.com").exists())

    @staticmethod
    def _code():
        return re.search(r"Doğrulama kodunuz: (\d{6})", mail.outbox[-1].body).group(1)

    def _register_verified(self):
        self.client.post(reverse("core:customer_register"), self.VALID)
        self.client.post(reverse("core:customer_verify"), {"code": self._code()})

    def test_register_then_verify_then_login_with_email(self):
        response = self.client.post(reverse("core:customer_register"), self.VALID)
        self.assertRedirects(response, reverse("core:customer_verify"))
        user = User.objects.get(email="ayse@example.com")
        self.assertEqual(user.role, User.Role.CUSTOMER)
        self.assertFalse(user.is_temporary_password)
        self.assertFalse(user.email_verified)                       # kod girilene kadar üyelik tamamlanmaz
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ayse@example.com"])
        self.assertIn("Doğrulama kodunuz:", mail.outbox[0].body)
        # kod girilmeden giriş yapılamaz
        blocked = self.client.post(reverse("core:customer_login"),
                                   {"username": "ayse@example.com", "password": "GucluSifre#2026"})
        self.assertRedirects(blocked, reverse("core:customer_verify"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        # kod doğrulanınca üyelik tamamlanır
        verify = self.client.post(reverse("core:customer_verify"), {"code": self._code()})
        self.assertRedirects(verify, reverse("core:customer_login"))
        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        login = self.client.post(reverse("core:customer_login"),
                                 {"username": "AYSE@example.com", "password": "GucluSifre#2026"})
        self.assertRedirects(login, reverse("core:home"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("core:customer_home")).status_code, 200)

    def test_wrong_code_does_not_verify(self):
        self.client.post(reverse("core:customer_register"), self.VALID)
        wrong = "000000" if self._code() != "000000" else "111111"
        response = self.client.post(reverse("core:customer_verify"), {"code": wrong})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kod hatalı veya süresi dolmuş")
        self.assertFalse(User.objects.get(email="ayse@example.com").email_verified)
        bad_format = self.client.post(reverse("core:customer_verify"), {"code": "12ab"})
        self.assertEqual(bad_format.status_code, 200)

    def test_code_locked_after_too_many_attempts_and_expires(self):
        from datetime import timedelta
        from django.utils import timezone
        self.client.post(reverse("core:customer_register"), self.VALID)
        real = self._code()
        wrong = "000000" if real != "000000" else "111111"
        for _ in range(5):
            self.client.post(reverse("core:customer_verify"), {"code": wrong})
        self.client.post(reverse("core:customer_verify"), {"code": real})          # 5 hatadan sonra doğru kod da kabul edilmez
        self.assertFalse(User.objects.get(email="ayse@example.com").email_verified)
        # yeni kod + süresi dolmuş kod
        self.client.post(reverse("core:customer_register"), self.VALID)
        fresh = self._code()
        EmailVerificationCode.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.client.post(reverse("core:customer_verify"), {"code": fresh})
        self.assertFalse(User.objects.get(email="ayse@example.com").email_verified)

    def test_verify_page_requires_pending_signup_and_resend_has_cooldown(self):
        self.assertRedirects(self.client.get(reverse("core:customer_verify")), reverse("core:customer_register"),
                             fetch_redirect_response=False)
        self.client.post(reverse("core:customer_register"), self.VALID)
        self.assertEqual(self.client.get(reverse("core:customer_verify")).status_code, 200)
        self.client.post(reverse("core:customer_verify_resend"))               # kayıttan hemen sonra: bekleme süresi
        self.assertEqual(len(mail.outbox), 1)
        s = self.client.session
        s["signup_sent_at"] = 0
        s.save()
        self.client.post(reverse("core:customer_verify_resend"))
        self.assertEqual(len(mail.outbox), 2)
        self.assertNotEqual(mail.outbox[0].body, mail.outbox[1].body)

    def test_unverified_login_from_new_session_sends_code_and_opens_verify_page(self):
        self.client.post(reverse("core:customer_register"), self.VALID)
        self.client.logout()
        fresh = self.client_class()
        response = fresh.post(reverse("core:customer_login"), {"username": "ayse@example.com", "password": "GucluSifre#2026"})
        self.assertRedirects(response, reverse("core:customer_verify"), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 2)
        done = fresh.post(reverse("core:customer_verify"), {"code": self._code()})
        self.assertRedirects(done, reverse("core:customer_login"))
        self.assertTrue(User.objects.get(email="ayse@example.com").email_verified)

    def test_reregistering_unverified_email_replaces_old_attempt(self):
        self.client.post(reverse("core:customer_register"), self.VALID)
        again = self.client.post(reverse("core:customer_register"), {**self.VALID, "first_name": "Ayşenur",
                                                                    "password1": "BaskaSifre#2026", "password2": "BaskaSifre#2026"})
        self.assertRedirects(again, reverse("core:customer_verify"))
        users = User.objects.filter(email="ayse@example.com")
        self.assertEqual(users.count(), 1)
        self.assertEqual(users.first().first_name, "Ayşenur")
        self.client.post(reverse("core:customer_verify"), {"code": self._code()})
        self.assertTrue(self.client.post(reverse("core:customer_login"),
                                         {"username": "ayse@example.com", "password": "BaskaSifre#2026"}).status_code == 302)

    def test_register_validations(self):
        for bad in ({"password1": "kisa", "password2": "kisa"},
                    {"password2": "BaskaSifre#2026"}):
            response = self.client.post(reverse("core:customer_register"), {**self.VALID, **bad})
            self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="ayse@example.com").exists())
        self._register_verified()
        again = self.client.post(reverse("core:customer_register"), self.VALID)
        self.assertEqual(again.status_code, 200)            # doğrulanmış hesapla aynı e-posta: reddedilir
        self.assertEqual(User.objects.filter(email="ayse@example.com").count(), 1)

    def test_staff_cannot_use_customer_login(self):
        response = self.client.post(reverse("core:customer_login"),
                                    {"username": "admin", "password": "FixLab#Admin2025"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_customer_logout_returns_to_customer_login(self):
        self._register_verified()
        self.client.post(reverse("core:customer_login"), {"username": "ayse@example.com", "password": "GucluSifre#2026"})
        response = self.client.post(reverse("core:logout"))
        self.assertRedirects(response, reverse("core:customer_login"), fetch_redirect_response=False)


class _ResetFlowMixin:
    """Şifre sıfırlama: e-posta -> kod doğrulama -> yeni şifre (müşteri ve personel için aynı kurallar)."""
    OLD = "EskiSifre#2026x"
    NEW = "YeniSifre#2026y"
    forgot = code = password = resend = login = sent_key = ""
    email = "ayse@example.com"

    def _request_code(self, email=None):
        response = self.client.post(reverse(self.forgot), {"email": email or self.email})
        self.assertRedirects(response, reverse(self.code))

    def _code(self):
        return re.search(r"Doğrulama kodunuz: (\d{6})", mail.outbox[-1].body).group(1)

    def _verify(self, code):
        return self.client.post(reverse(self.code), {"code": code})

    def _set_password(self, password=None, password2=None):
        password = password or self.NEW
        return self.client.post(reverse(self.password), {"password1": password, "password2": password2 or password})

    def _wrong(self, good):
        return "000000" if good != "000000" else "111111"

    def _can_login(self, password):
        self.client.logout()
        self.client.post(reverse(self.login), {"username": self.login_name, "password": password})
        return "_auth_user_id" in self.client.session

    def test_pages_open_and_steps_are_guarded(self):
        self.assertContains(self.client.get(reverse(self.forgot)), "Kod Gönder")
        # e-posta girilmeden kod ve yeni şifre sayfaları açılmaz
        self.assertRedirects(self.client.get(reverse(self.code)), reverse(self.forgot))
        self.assertRedirects(self.client.get(reverse(self.password)), reverse(self.forgot))

    def test_steps_are_separate_pages(self):
        self._request_code()
        code_page = self.client.get(reverse(self.code))
        self.assertContains(code_page, "Kodu Doğrula")
        self.assertContains(code_page, 'id="code-boxes"')
        self.assertNotContains(code_page, 'name="password1"')
        self.assertContains(code_page, "a•••••@•••••.com")
        self.assertNotContains(code_page, self.email)
        # kod doğrulanmadan yeni şifre sayfası açılmaz
        self.assertRedirects(self.client.get(reverse(self.password)), reverse(self.code))
        self.assertRedirects(self._verify(self._code()), reverse(self.password))
        password_page = self.client.get(reverse(self.password))
        self.assertContains(password_page, 'name="password1"')
        self.assertContains(password_page, 'name="password2"')
        self.assertNotContains(password_page, 'id="code-boxes"')

    def test_full_reset_flow(self):
        self._request_code()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.email])
        self.assertRedirects(self._verify(self._code()), reverse(self.password))
        self.assertRedirects(self._set_password(), reverse(self.login), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.NEW))
        self.assertFalse(self.user.is_temporary_password)
        self.assertTrue(self._can_login(self.NEW))

    def test_password_cannot_be_set_before_verification(self):
        self._request_code()
        self.assertRedirects(self._set_password(), reverse(self.code), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.OLD))

    def test_wrong_code_rejected_and_locks_after_attempts(self):
        self._request_code()
        good = self._code()
        for _ in range(5):
            self.assertEqual(self._verify(self._wrong(good)).status_code, 200)
        self.assertRedirects(self.client.get(reverse(self.password)), reverse(self.code))
        self.assertEqual(self._verify(good).status_code, 200)  # doğru kod bile artık geçersiz
        self.assertRedirects(self._set_password(), reverse(self.code), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.OLD))

    def test_code_must_be_six_digits(self):
        self._request_code()
        for bad in ("", "12345", "abcdef", "1234567"):
            self.assertEqual(self._verify(bad).status_code, 200)
        self.assertRedirects(self.client.get(reverse(self.password)), reverse(self.code))

    def test_expired_code_rejected(self):
        from datetime import timedelta
        from django.utils import timezone
        self._request_code()
        code = self._code()
        self.user.reset_codes.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self._verify(code).status_code, 200)
        self.assertRedirects(self.client.get(reverse(self.password)), reverse(self.code))

    def test_verification_expires_before_password_step(self):
        from datetime import timedelta
        from django.utils import timezone
        self._request_code()
        self._verify(self._code())
        self.user.reset_codes.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertRedirects(self._set_password(), reverse(self.code), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.OLD))

    def test_password_rules(self):
        self._request_code()
        self._verify(self._code())
        self.assertEqual(self._set_password("kisa", "kisa").status_code, 200)
        self.assertEqual(self._set_password(self.NEW, "Farkli#Sifre2026").status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.OLD))
        # hatalı şifre denemeleri doğrulamayı bozmaz
        self.assertRedirects(self._set_password(), reverse(self.login), fetch_redirect_response=False)

    def test_code_is_single_use(self):
        self._request_code()
        code = self._code()
        self._verify(code)
        self._set_password()
        self._request_code()  # oturum temizlendi, yeniden başlatılır
        self.assertEqual(self._verify(code).status_code, 200)  # eski kod artık geçersiz
        self.assertRedirects(self.client.get(reverse(self.password)), reverse(self.code))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.NEW))

    def test_unknown_email_gets_same_response_without_mail(self):
        self._request_code("yok@example.com")
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self._verify("123456").status_code, 200)
        self.assertRedirects(self.client.get(reverse(self.password)), reverse(self.code))

    def test_other_account_type_cannot_reset_here(self):
        self._request_code(self.other_email)
        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_account_gets_no_code(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self._request_code()
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_has_cooldown(self):
        self._request_code()
        code = self._code()
        self.client.post(reverse(self.resend))
        self.assertEqual(len(mail.outbox), 1)  # 60 sn bekleme: ikinci kod gitmez, ilk kod geçerli kalır
        self.assertRedirects(self._verify(code), reverse(self.password))

    def test_resend_requires_post(self):
        self._request_code()
        self.assertEqual(self.client.get(reverse(self.resend)).status_code, 405)

    def test_new_code_invalidates_old_code_and_verification(self):
        from django.utils import timezone
        self._request_code()
        old = self._code()
        self.assertRedirects(self._verify(old), reverse(self.password))
        session = self.client.session
        session[self.sent_key] = {self.email: timezone.now().timestamp() - 120}
        session.save()
        self.client.post(reverse(self.resend))
        self.assertEqual(len(mail.outbox), 2)
        # yeni kod istendi: önceki doğrulama geçersiz, yeni şifre sayfası kodu yeniden ister
        self.assertRedirects(self.client.get(reverse(self.password)), reverse(self.code))
        new = self._code()
        if new != old:
            self.assertEqual(self._verify(old).status_code, 200)
        self.assertRedirects(self._verify(new), reverse(self.password))
        self.assertRedirects(self._set_password(), reverse(self.login), fetch_redirect_response=False)

    def test_logged_in_user_is_redirected_away(self):
        self.client.force_login(self.user)
        for name in (self.forgot, self.code, self.password):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CustomerPasswordResetTests(_ResetFlowMixin, TestCase):
    forgot, code, password = "core:customer_forgot", "core:customer_reset_code", "core:customer_reset_password"
    resend, login = "core:customer_resend_code", "core:customer_login"
    sent_key = "reset_sent_at"
    login_name = "ayse@example.com"

    def setUp(self):
        self.other_email = User.objects.get(username="admin").email  # personel e-postası müşteri akışında çalışmaz
        self.user = User.objects.create_user(
            "ayse@example.com", "ayse@example.com", self.OLD, role=User.Role.CUSTOMER,
            first_name="Ayşe", last_name="Demir", is_temporary_password=False)

    def test_login_links_to_forgot_page(self):
        self.assertContains(self.client.get(reverse("core:customer_login")), reverse("core:customer_forgot"))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class StaffPasswordResetTests(_ResetFlowMixin, TestCase):
    forgot, code, password = "core:staff_forgot", "core:staff_reset_code", "core:staff_reset_password"
    resend, login = "core:staff_resend_code", "core:login"
    sent_key = "staff_reset_sent_at"
    login_name = "ayse.demir"

    def setUp(self):
        self.other_email = "musteri@example.com"  # müşteri e-postası personel akışında çalışmaz
        User.objects.create_user("musteri@example.com", "musteri@example.com", "Musteri#2026x", role=User.Role.CUSTOMER,
                                 is_temporary_password=False)
        self.user = User.objects.create_user(
            "ayse.demir", "ayse@example.com", self.OLD, role=User.Role.TECHNICIAN,
            first_name="Ayşe", last_name="Demir", is_temporary_password=False)

    def test_login_page_links_to_staff_forgot(self):
        self.assertContains(self.client.get(reverse("core:login")), reverse("core:staff_forgot"))

    def test_staff_pages_link_to_customer_login(self):
        for name in (self.forgot,):
            self.assertContains(self.client.get(reverse(name)), reverse("core:customer_login"))

    def test_admin_can_reset_too(self):
        admin = User.objects.get(username="admin")
        self._request_code(admin.email)
        self.assertEqual(len(mail.outbox), 1)
        self._verify(self._code())
        self._set_password()
        admin.refresh_from_db()
        self.assertTrue(admin.check_password(self.NEW))

    def test_temporary_password_user_gets_permanent_password(self):
        self.user.is_temporary_password = True
        self.user.save(update_fields=["is_temporary_password"])
        self._request_code()
        self._verify(self._code())
        self._set_password()
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_temporary_password)
        self.assertTrue(self._can_login(self.NEW))
        self.assertRedirects(self.client.get(reverse("core:home")), reverse("core:technician_dashboard"),
                             fetch_redirect_response=False)

    def test_customer_and_staff_flows_do_not_share_session(self):
        self._request_code()
        self.assertRedirects(self.client.get(reverse("core:customer_reset_code")), reverse("core:customer_forgot"))


# --------------------------------------------------------------------------
# Müşteri portalı: cihaz kaydı, sorgulama, kargo ve bildirimler
# --------------------------------------------------------------------------
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile

from .models import DeviceAttachment, DeviceStatusHistory, Notification

_TMP_MEDIA = tempfile.mkdtemp(prefix="fixlab-test-media-")


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class CustomerDeviceFlowTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.customer = User.objects.create_user(
            username="ayse@example.com", email="ayse@example.com", password="GucluSifre#2026",
            first_name="Ayşe", last_name="Yılmaz", role=User.Role.CUSTOMER, is_temporary_password=False)
        self.other = User.objects.create_user(
            username="mehmet@example.com", email="mehmet@example.com", password="GucluSifre#2026",
            first_name="Mehmet", last_name="Kaya", role=User.Role.CUSTOMER, is_temporary_password=False)
        self.admin = User.objects.get(username="admin")
        self.client.force_login(self.customer)

    def _payload(self, **extra):
        data = {
            "device_type": "laptop", "brand": "ASUS", "model": "Vivobook 15", "serial_number": "",
            "issue_title": "Ekranda aralıklı titreme", "issue_description": "Ekran açısı değişince görüntü gidip geliyor.",
            "delivery_method": "self", "contact_phone": "",
        }
        data.update(extra)
        return data

    def _cargo_payload(self, **extra):
        return self._payload(delivery_method="cargo", return_recipient="Ayşe Yılmaz", return_phone="0542 555 12 34",
                             return_city="İstanbul", return_district="Kadıköy", return_address="Caferağa Mah. No: 12", **extra)

    def _device(self, owner=None, **extra):
        values = dict(customer=owner or self.customer, customer_name="Ayşe Yılmaz", customer_phone="05321234567",
                      brand="Lenovo", model="ThinkPad E14", issue_description="Açılmıyor")
        values.update(extra)
        return Device.objects.create(**values)

    # --- personel girişine müşteri hesabıyla girilemez ---
    def test_customer_cannot_log_in_from_staff_login(self):
        self.client.logout()
        response = self.client.post(reverse("core:login"), {"username": "ayse@example.com", "password": "GucluSifre#2026"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bu giriş personel içindir. Müşteri girişini kullanın.")
        self.assertNotIn("_auth_user_id", self.client.session)
        # müşteri girişinden ise normal giriş yapar
        ok = self.client.post(reverse("core:customer_login"), {"username": "ayse@example.com", "password": "GucluSifre#2026"})
        self.assertEqual(ok.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    # --- kayıt bilgilendirme e-postası ---
    def test_customer_registration_sends_tracking_code_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("core:customer_device_create"), self._payload())
        self.assertEqual(response.status_code, 302)
        device = Device.objects.get(customer=self.customer)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["ayse@example.com"])
        self.assertIn(device.tracking_id, message.subject)
        self.assertIn(f"Sorgulama kodunuz: {device.tracking_id}", message.body)
        self.assertIn("Vivobook 15", message.body)
        self.assertIn("elden teslim", message.body)
        self.assertIn(device.tracking_id, message.alternatives[0][0])

    def test_cargo_registration_email_mentions_cargo_step(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("core:customer_device_create"), self._cargo_payload())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("kargo", mail.outbox[0].body.lower())
        self.assertNotIn("elden teslim", mail.outbox[0].body)

    def test_staff_created_record_sends_email_to_customer(self):
        with self.captureOnCommitCallbacks(execute=True):
            device = self._device()          # personel / admin panelinden açılan kayıt (customer_email boş olsa da hesap e-postası kullanılır)
        self.assertEqual([m.to for m in mail.outbox], [["ayse@example.com"]])
        self.assertIn(device.tracking_id, mail.outbox[0].body)

    def test_walk_in_record_uses_record_email_and_skips_when_none(self):
        with self.captureOnCommitCallbacks(execute=True):
            Device.objects.create(customer_name="Ziyaretçi", customer_phone="0532", customer_email="walkin@example.com",
                                  brand="HP", model="Pavilion", issue_description="x")
            Device.objects.create(customer_name="Mailsiz", customer_phone="0533", brand="HP", model="X", issue_description="x")
        self.assertEqual([m.to for m in mail.outbox], [["walkin@example.com"]])

    def test_email_failure_does_not_break_registration(self):
        with mock.patch("core.utils.send_mail", side_effect=OSError("smtp down")):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse("core:customer_device_create"), self._payload())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Device.objects.filter(customer=self.customer).exists())

    def test_status_change_and_cancel_do_not_send_registration_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            device = self._device(status=Device.Status.PRE_REGISTERED)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self._cancel_url(device))
            device.refresh_from_db()
            device.status = Device.Status.RECEIVED
            device.save()
        self.assertEqual(len(mail.outbox), 0)

    # --- kaydı iptal et ---
    def _cancel_url(self, device):
        return reverse("core:customer_device_cancel", args=[device.tracking_id])

    def test_cancel_button_only_before_cargo_or_delivery_to_service(self):
        S = Device.Status
        visible = {S.PRE_REGISTERED: True, S.AWAITING_CARGO: True, S.IN_TRANSIT: False, S.RECEIVED: False,
                   S.DIAGNOSING: False, S.READY: False, S.DELIVERED: False, S.CANCELLED: False}
        for status, expected in visible.items():
            device = self._device(status=status)
            for page in (self.client.get(reverse("core:customer_home")),
                         self.client.get(reverse("core:customer_device_lookup"), {"kod": device.tracking_id})):
                self.assertEqual(self._cancel_url(device) in page.content.decode(), expected, (status, expected))
            self.assertEqual(device.customer_can_cancel, expected)
            device.delete()

    def test_customer_cancels_self_delivery_record(self):
        device = self._device(status=Device.Status.PRE_REGISTERED)
        response = self.client.post(self._cancel_url(device))
        self.assertRedirects(response, f"{reverse('core:customer_device_lookup')}?kod={device.tracking_id}")
        device.refresh_from_db()
        self.assertEqual(device.status, Device.Status.CANCELLED)
        self.assertTrue(device.status_history.filter(new_status=Device.Status.CANCELLED,
                                                      note="Müşteri kaydı iptal etti").exists())
        page = self.client.get(reverse("core:customer_device_lookup"), {"kod": device.tracking_id})
        self.assertContains(page, "İptal edildi")
        self.assertNotContains(page, self._cancel_url(device))

    def test_cancelled_record_leaves_admin_new_records(self):
        device = self._device(status=Device.Status.AWAITING_CARGO, delivery_method="cargo")
        self.client.post(self._cancel_url(device))
        self.client.force_login(self.admin)
        page = self.client.get(reverse("core:admin_new_devices"))
        self.assertNotContains(page, device.tracking_id)
        self.assertEqual(page.context["total_new"], 0)
        self.assertEqual(page.context["nav_new_devices"], 0)
        self.assertEqual(self.client.get(reverse("core:admin_new_device_detail", args=[device.pk])).status_code, 404)

    def test_cannot_cancel_after_cargo_or_after_arrival(self):
        for status in (Device.Status.IN_TRANSIT, Device.Status.RECEIVED, Device.Status.DIAGNOSING):
            device = self._device(status=status)
            self.client.post(self._cancel_url(device))
            device.refresh_from_db()
            self.assertEqual(device.status, status)

    def test_cancel_requires_post_and_ownership(self):
        device = self._device(status=Device.Status.PRE_REGISTERED)
        self.assertEqual(self.client.get(self._cancel_url(device)).status_code, 405)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self._cancel_url(device)).status_code, 404)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self._cancel_url(device)).status_code, 403)
        device.refresh_from_db()
        self.assertEqual(device.status, Device.Status.PRE_REGISTERED)

    # --- sayfalar ---
    def test_portal_pages_open_for_customer(self):
        for name in ("customer_home", "customer_device_create", "customer_device_lookup", "notifications_list",
                     "customer_devices", "customer_help", "customer_profile"):
            self.assertEqual(self.client.get(reverse(f"core:{name}")).status_code, 200, name)
        home = self.client.get(reverse("core:customer_home"))
        self.assertContains(home, reverse("core:customer_device_create"))
        self.assertContains(home, reverse("core:customer_device_lookup"))

    def test_portal_pages_closed_to_staff(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("core:customer_device_create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:customer_home")).status_code, 403)

    def test_home_lists_only_own_devices(self):
        mine = self._device()
        theirs = self._device(owner=self.other, model="Gizli Model")
        home = self.client.get(reverse("core:customer_home"))
        self.assertContains(home, mine.tracking_id)
        self.assertNotContains(home, theirs.tracking_id)
        self.assertEqual(home.context["total_count"], 1)

    # --- kayıt oluşturma ---
    def test_self_delivery_registration(self):
        response = self.client.post(reverse("core:customer_device_create"), self._payload())
        device = Device.objects.get(customer=self.customer)
        self.assertRedirects(response, reverse("core:customer_device_created", args=[device.tracking_id]))
        self.assertEqual(device.status, Device.Status.PRE_REGISTERED)
        self.assertEqual(device.delivery_method, Device.DeliveryMethod.SELF)
        self.assertRegex(device.tracking_id, r"^TS-\d{4}-\d{4}$")
        page = self.client.get(response["Location"])
        self.assertContains(page, "Ön kayıt alındı")
        self.assertContains(page, device.tracking_id)
        self.assertContains(page, "Kendim teslim edeceğim")
        self.assertNotContains(page, "Kargo adımlarını görüntüle")

    def test_cargo_registration_with_attachments(self):
        files = [SimpleUploadedFile("ekran.jpg", b"\xff\xd8\xff fake", content_type="image/jpeg"),
                 SimpleUploadedFile("titreme.mp4", b"fake-video", content_type="video/mp4")]
        response = self.client.post(reverse("core:customer_device_create"),
                                    {**self._cargo_payload(), "attachments": files})
        device = Device.objects.get(customer=self.customer)
        self.assertRedirects(response, reverse("core:customer_device_created", args=[device.tracking_id]))
        self.assertEqual(device.status, Device.Status.AWAITING_CARGO)
        self.assertEqual(device.return_city, "İstanbul")
        self.assertEqual(device.attachments.count(), 2)
        self.assertEqual(set(device.attachments.values_list("kind", flat=True)), {"photo", "video"})
        page = self.client.get(response["Location"])
        self.assertContains(page, "Kargo bekleniyor")
        self.assertContains(page, "Kargo adımlarını görüntüle")
        self.assertContains(page, reverse("core:customer_cargo", args=[device.tracking_id]))

    def test_cargo_requires_return_address(self):
        data = self._cargo_payload()
        data["return_address"] = ""
        response = self.client.post(reverse("core:customer_device_create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Device.objects.exists())

    def test_required_fields_and_bad_files_rejected(self):
        self.assertEqual(self.client.post(reverse("core:customer_device_create"),
                                          self._payload(model="")).status_code, 200)
        bad = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/octet-stream")
        response = self.client.post(reverse("core:customer_device_create"), {**self._payload(), "attachments": bad})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Device.objects.exists())

    def test_created_page_not_visible_to_other_customer(self):
        device = self._device()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("core:customer_device_created", args=[device.tracking_id])).status_code, 404)
        self.assertEqual(self.client.get(reverse("core:customer_cargo", args=[device.tracking_id])).status_code, 404)

    # --- sorgulama ---
    def test_lookup_shows_own_device_only(self):
        mine = self._device(status=Device.Status.DIAGNOSING)
        theirs = self._device(owner=self.other, model="Başkası")
        url = reverse("core:customer_device_lookup")
        found = self.client.get(url, {"kod": mine.tracking_id.lower()})
        self.assertContains(found, "ThinkPad E14")
        self.assertContains(found, "İnceleniyor")
        missing = self.client.get(url, {"kod": theirs.tracking_id})
        self.assertContains(missing, "bulunamadı")
        self.assertNotContains(missing, "Başkası")

    # --- kargo süreci ---
    def test_cargo_page_and_customer_marks_sent(self):
        self.client.post(reverse("core:customer_device_create"), self._cargo_payload())
        device = Device.objects.get(customer=self.customer)
        Notification.objects.all().delete()
        url = reverse("core:customer_cargo", args=[device.tracking_id])
        self.assertContains(self.client.get(url), "Kargo bekleniyor")

        bad = self.client.post(url, {"cargo_company": "Yurtiçi Kargo", "cargo_tracking_code": ""})
        self.assertEqual(bad.status_code, 200)
        device.refresh_from_db()
        self.assertEqual(device.status, Device.Status.AWAITING_CARGO)

        self.client.post(url, {"cargo_company": "PTT Kargo", "cargo_tracking_code": "PTT-2048-5512"})
        device.refresh_from_db()
        self.assertEqual(device.status, Device.Status.IN_TRANSIT)
        self.assertEqual(device.cargo_tracking_code, "PTT-2048-5512")
        self.assertTrue(Notification.objects.filter(recipient=self.admin, title="Cihaz kargoya verildi").exists())
        self.assertFalse(Notification.objects.filter(recipient=self.customer).exists())  # kendi işlemi için bildirim yok
        self.assertContains(self.client.get(url), "PTT-2048-5512")

    def test_self_delivery_has_no_cargo_page(self):
        device = self._device(status=Device.Status.PRE_REGISTERED)
        response = self.client.get(reverse("core:customer_cargo", args=[device.tracking_id]))
        self.assertEqual(response.status_code, 302)

    # --- bildirimler ---
    def test_admin_notified_on_new_registration(self):
        self.client.post(reverse("core:customer_device_create"), self._payload())
        note = Notification.objects.get(recipient=self.admin)
        self.assertEqual(note.title, "Yeni cihaz ön kaydı")
        self.assertIn("TS-", note.body)
        self.assertFalse(Notification.objects.filter(recipient=self.customer).exists())

    def test_customer_notified_on_every_service_change(self):
        device = self._device(status=Device.Status.RECEIVED)
        Notification.objects.all().delete()

        device = Device.objects.get(pk=device.pk)
        device.status = Device.Status.DIAGNOSING
        device._actor = self.admin
        device.save()
        device = Device.objects.get(pk=device.pk)
        device.estimated_cost = 1850
        device.save()
        device = Device.objects.get(pk=device.pk)
        device.estimated_delivery = datetime.date(2026, 10, 25)
        device.save()
        device = Device.objects.get(pk=device.pk)
        device.diagnosis_notes = "İç not: müşteriye gösterilmez"
        device.save()  # iç not -> bildirim yok

        titles = list(Notification.objects.filter(recipient=self.customer).order_by("id").values_list("title", flat=True))
        self.assertEqual(titles, ["Cihazınız inceleniyor", "Servis kaydınızda güncelleme", "Servis kaydınızda güncelleme"])
        self.assertFalse(Notification.objects.filter(recipient=self.admin).exists())
        self.assertTrue(DeviceStatusHistory.objects.filter(device=device, new_status="diagnosing", changed_by=self.admin).exists())

    def test_notification_feed_open_and_mark_read(self):
        device = self._device(status=Device.Status.RECEIVED)
        Notification.objects.all().delete()
        device = Device.objects.get(pk=device.pk)
        device.status = Device.Status.READY
        device.save()
        feed = self.client.get(reverse("core:notifications_feed"), {"after": 0}).json()
        self.assertEqual(feed["unread"], 1)
        self.assertEqual(feed["new"][0]["title"], "Cihazınız teslime hazır")
        self.assertEqual(self.client.get(reverse("core:notifications_feed"), {"after": feed["latest"]}).json()["new"], [])

        opened = self.client.get(feed["new"][0]["url"])
        self.assertEqual(opened.status_code, 302)
        self.assertTrue(opened["Location"].startswith("/musteri/"))
        self.assertEqual(self.client.get(reverse("core:notifications_feed")).json()["unread"], 0)

        self.client.force_login(self.other)  # başkasının bildirimi açılamaz
        self.assertEqual(self.client.get(feed["new"][0]["url"]).status_code, 404)

    def test_mark_all_read_and_feed_requires_login(self):
        self._device(status=Device.Status.RECEIVED)
        d = Device.objects.get(customer=self.customer)
        d.status = Device.Status.REPAIRING
        d.save()
        self.client.post(reverse("core:notifications_mark_read"), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(self.client.get(reverse("core:notifications_feed")).json()["unread"], 0)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("core:notifications_feed")).status_code, 302)

    def test_staff_sees_notification_bell_and_list(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("core:admin_dashboard")), 'id="nt-btn"')
        self.assertContains(self.client.get(reverse("core:staff_list")), 'id="nt-btn"')
        self.assertEqual(self.client.get(reverse("core:notifications_list")).status_code, 200)

    # --- dosya erişimi ---
    def test_attachment_access_control(self):
        self.client.post(reverse("core:customer_device_create"), {
            **self._payload(), "attachments": SimpleUploadedFile("a.png", b"\x89PNG fake", content_type="image/png")})
        attachment = DeviceAttachment.objects.get()
        url = reverse("core:attachment", args=[attachment.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)


# ---------------------------------------------------------------------------
# Admin › Stok Depo + Bildirimlerim paneli
# ---------------------------------------------------------------------------
from decimal import Decimal as _D  # noqa: E402

from .models import DevicePartUsage, NotificationPreference, PartRequest, StockMovement, StockPart, TechnicianLeave  # noqa: E402


class StockDepotTests(TestCase):
    def setUp(self):
        self.admin = User.objects.get(username="admin")
        self.client.force_login(self.admin)
        self.tech = User.objects.create_user("ayse.demir", "ayse@x.com", "Pass#12345", role=User.Role.TECHNICIAN,
                                             first_name="Ayşe", last_name="Demir", is_temporary_password=False)
        self.lcd = StockPart.objects.create(code="LCD-14-FHD", name="14\" FHD laptop paneli", quantity=2,
                                            critical_level=3, unit_price=_D("1450"))
        self.rlr = StockPart.objects.create(code="RLR-PICK", name="Kağıt besleme silindiri", quantity=14,
                                            critical_level=5, unit_price=_D("95"))

    def test_list_shows_parts_cards_and_links(self):
        page = self.client.get(reverse("core:stock_list"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["part_count"], 2)
        self.assertEqual(page.context["critical_count"], 1)
        for text in ("Stok Depo", "LCD-14-FHD", "Kritik", "Yeterli", "1.450 ₺", "Parça detay ve stok giriş", "Yeni parça"):
            self.assertContains(page, text)
        self.assertContains(page, reverse("core:stock_detail", args=[self.lcd.pk]))
        self.assertContains(page, reverse("core:stock_create"))

    def test_only_admin_can_open(self):
        self.client.force_login(self.tech)
        for url in (reverse("core:stock_list"), reverse("core:stock_create"), reverse("core:stock_detail", args=[self.lcd.pk])):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("core:stock_list")).status_code, 302)

    def test_new_part_appears_in_list_with_initial_movement(self):
        url = reverse("core:stock_create")
        self.assertEqual(self.client.get(url).status_code, 200)
        out = self.client.post(url, {"code": "usb-c-skt", "name": "USB-C şarj soketi", "quantity": "5",
                                     "critical_level": "2", "unit_price": "180,50 ₺"})
        self.assertRedirects(out, reverse("core:stock_list"))
        part = StockPart.objects.get(code="USB-C-SKT")
        self.assertEqual((part.quantity, part.critical_level, part.unit_price), (5, 2, _D("180.50")))
        self.assertEqual(part.movements.get().movement_type, "in")
        self.assertContains(self.client.get(reverse("core:stock_list")), "USB-C şarj soketi")

    def test_new_part_defaults_and_validation(self):
        url = reverse("core:stock_create")
        self.client.post(url, {"code": "X-1", "name": "Deneme", "quantity": "", "critical_level": "", "unit_price": ""})
        part = StockPart.objects.get(code="X-1")
        self.assertEqual((part.quantity, part.critical_level, part.unit_price), (0, 1, _D("0.00")))
        self.assertFalse(part.movements.exists())
        dup = self.client.post(url, {"code": "lcd-14-fhd", "name": "Başka", "quantity": "1"})
        self.assertEqual(dup.status_code, 200)
        self.assertContains(dup, "zaten var")
        bad = self.client.post(url, {"code": "", "name": "", "unit_price": "abc"})
        self.assertContains(bad, "Stok kodu zorunlu")
        self.assertContains(bad, "Geçerli bir fiyat girin")
        self.assertEqual(StockPart.objects.count(), 3)

    def test_detail_shows_movements_and_stock_entry(self):
        device = Device.objects.create(customer_name="Mehmet", brand="Dell", model="L5420", issue_description="x")
        StockMovement.objects.create(part=self.lcd, movement_type="out", quantity=1, device=device, created_by=self.tech)
        url = reverse("core:stock_detail", args=[self.lcd.pk])
        page = self.client.get(url)
        for text in ("Stok hareketleri", "Çıkış", "-1", "Ayşe Demir", device.tracking_id, "Stok girişi",
                     "Parçayı düzenle", "Stok Depoya Dön"):
            self.assertContains(page, text)
        out = self.client.post(url, {"quantity": "10", "note": "Tedarikçi siparişi"})
        self.assertRedirects(out, url)
        self.lcd.refresh_from_db()
        self.assertEqual(self.lcd.quantity, 12)
        move = self.lcd.movements.first()
        self.assertEqual((move.movement_type, move.quantity, move.created_by, move.note),
                         ("in", 10, self.admin, "Tedarikçi siparişi"))
        self.assertContains(self.client.get(url), "+10")
        bad = self.client.post(url, {"quantity": "0"})
        self.assertContains(bad, "en az 1")
        self.lcd.refresh_from_db()
        self.assertEqual(self.lcd.quantity, 12)

    def test_edit_prefilled_and_quantity_change_logged(self):
        url = reverse("core:stock_edit", args=[self.lcd.pk])
        page = self.client.get(url)
        self.assertContains(page, 'value="LCD-14-FHD"')
        self.assertContains(page, "Mevcut adet")
        self.assertContains(page, 'value="1450,00"')
        out = self.client.post(url, {"code": "LCD-14-FHD", "name": "14\" FHD panel v2", "quantity": "7",
                                     "critical_level": "4", "unit_price": "1.500,00"})
        self.assertRedirects(out, reverse("core:stock_detail", args=[self.lcd.pk]))
        self.lcd.refresh_from_db()
        self.assertEqual((self.lcd.name, self.lcd.quantity, self.lcd.critical_level, self.lcd.unit_price),
                         ("14\" FHD panel v2", 7, 4, _D("1500.00")))
        adj = self.lcd.movements.get()
        self.assertEqual((adj.movement_type, adj.quantity), ("adjust", 5))

    def test_delete_unused_and_archive_used(self):
        self.client.post(reverse("core:stock_delete", args=[self.rlr.pk]))
        self.assertFalse(StockPart.objects.filter(pk=self.rlr.pk).exists())
        device = Device.objects.create(customer_name="Mehmet", brand="Dell", model="L5420", issue_description="x")
        DevicePartUsage.objects.create(device=device, part=self.lcd, quantity=1)
        self.client.post(reverse("core:stock_delete", args=[self.lcd.pk]))
        self.lcd.refresh_from_db()
        self.assertFalse(self.lcd.is_active)
        self.assertNotContains(self.client.get(reverse("core:stock_list")), "LCD-14-FHD")
        self.assertEqual(self.client.get(reverse("core:stock_delete", args=[self.lcd.pk])).status_code, 405)

    def test_sidebar_links_stock_and_bell_present(self):
        page = self.client.get(reverse("core:stock_list"))
        self.assertContains(page, 'id="nt-btn"')
        self.assertContains(self.client.get(reverse("core:admin_dashboard")), reverse("core:stock_list"))


class AdminNotificationPanelTests(TestCase):
    def setUp(self):
        self.admin = User.objects.get(username="admin")
        self.client.force_login(self.admin)
        self.tech = User.objects.create_user("ayse.demir", "ayse@x.com", "Pass#12345", role=User.Role.TECHNICIAN,
                                             first_name="Ayşe", last_name="Demir", is_temporary_password=False)
        self.device = Device.objects.create(customer_name="Mehmet", brand="Dell", model="L5420",
                                            issue_description="x", technician=self.tech)
        self.part = StockPart.objects.create(code="LCD-14-FHD", name="LCD 14\" FHD panel", quantity=2, critical_level=3)
        Notification.objects.all().delete()

    def _request(self, qty=1):
        PartRequest.objects.create(technician=self.tech, device=self.device, part=self.part, quantity=qty)
        return Notification.objects.get(recipient=self.admin, kind="part_request")

    def test_bell_links_to_panel_and_page_renders_like_design(self):
        self._request()
        TechnicianLeave.objects.create(technician=self.tech, start_date=datetime.date(2025, 10, 13), end_date=datetime.date(2025, 10, 17))
        page = self.client.get(reverse("core:notifications_list"))
        self.assertEqual(page.status_code, 200)
        for text in ("Bildirimlerim", "Parça talebi", "İzin talebi", "Onayla", "Reddet", "Onay bekleyen talepler",
                     "1 parça talebi, 1 izin talebi", "Bildirim tercihleri", "Tümünü okundu işaretle",
                     "Ayşe Demir", self.device.tracking_id, "Stokta 2 adet var", "5 iş günü", "Bugün"):
            self.assertContains(page, text)
        self.assertContains(page, f'href="{reverse("core:notifications_list")}"')  # zil bu sayfaya gider
        self.assertEqual(page.context["pending_total"], 2)

    def test_approve_part_request_takes_stock_and_notifies_technician(self):
        note = self._request(qty=2)
        out = self.client.post(reverse("core:notification_approve", args=[note.pk]))
        self.assertRedirects(out, reverse("core:notifications_list"))
        self.part.refresh_from_db()
        self.assertEqual(self.part.quantity, 0)
        pr = PartRequest.objects.get()
        self.assertEqual((pr.status, pr.reviewed_by), ("approved", self.admin))
        move = StockMovement.objects.get(part=self.part)
        self.assertEqual((move.movement_type, move.quantity, move.device, move.created_by), ("out", 2, self.device, self.tech))
        self.assertTrue(DevicePartUsage.objects.filter(device=self.device, part=self.part, quantity=2).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.tech, title="Parça talebiniz onaylandı").exists())
        self.assertFalse(Notification.objects.filter(recipient=self.admin, kind="stock").exists())  # kendi onayı için «Stok» bildirimi yok
        again = self.client.post(reverse("core:notification_approve", args=[note.pk]), follow=True)
        self.assertContains(again, "daha önce sonuçlandırılmış")
        self.part.refresh_from_db()
        self.assertEqual(self.part.quantity, 0)

    def test_approve_fails_without_enough_stock(self):
        note = self._request(qty=5)
        self.client.post(reverse("core:notification_approve", args=[note.pk]))
        self.part.refresh_from_db()
        self.assertEqual(self.part.quantity, 2)
        self.assertEqual(PartRequest.objects.get().status, "pending")

    def test_reject_part_and_leave_requests(self):
        note = self._request()
        self.client.post(reverse("core:notification_reject", args=[note.pk]))
        self.assertEqual(PartRequest.objects.get().status, "rejected")
        self.part.refresh_from_db()
        self.assertEqual(self.part.quantity, 2)
        leave = TechnicianLeave.objects.create(technician=self.tech, start_date=datetime.date(2025, 10, 13), end_date=datetime.date(2025, 10, 17))
        lnote = Notification.objects.get(recipient=self.admin, kind="leave")
        self.client.post(reverse("core:notification_approve", args=[lnote.pk]))
        leave.refresh_from_db()
        self.assertEqual((leave.status, leave.reviewed_by), ("approved", self.admin))
        self.assertTrue(Notification.objects.filter(recipient=self.tech, title="İzin talebiniz onaylandı").exists())

    def test_decisions_are_admin_only_and_post_only(self):
        note = self._request()
        url = reverse("core:notification_approve", args=[note.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.force_login(self.tech)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(PartRequest.objects.get().status, "pending")

    def test_technician_stock_use_notifies_admin(self):
        StockMovement.objects.create(part=self.part, movement_type="out", quantity=1, device=self.device, created_by=self.tech)
        note = Notification.objects.get(recipient=self.admin, kind="stock")
        self.assertEqual(note.url, reverse("core:stock_detail", args=[self.part.pk]))
        self.assertNotContains(self.client.get(reverse("core:notifications_list")), "Stok hareketini aç")

    def test_preferences_hide_kinds_in_panel_badge_and_feed(self):
        self._request()
        self.assertEqual(self.client.get(reverse("core:notifications_feed")).json()["unread"], 1)
        out = self.client.post(reverse("core:notification_pref"), {"name": "part_requests", "value": "0"})
        self.assertEqual(out.json(), {"ok": True})
        self.assertEqual(self.client.get(reverse("core:notifications_feed")).json()["unread"], 0)
        self.assertNotContains(self.client.get(reverse("core:notifications_list")), "parçası talep etti")
        self.assertEqual(self.client.post(reverse("core:notification_pref"), {"name": "bogus", "value": "1"}).status_code, 400)
        self.client.post(reverse("core:notification_pref"), {"name": "part_requests", "value": "1"})
        self.assertEqual(self.client.get(reverse("core:notifications_feed")).json()["unread"], 1)
        self.assertFalse(NotificationPreference.objects.get(user=self.admin).statuses)  # varsayılan: durum güncellemeleri kapalı

    def test_other_roles_keep_old_notification_page(self):
        self.client.force_login(self.tech)
        page = self.client.get(reverse("core:notifications_list"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Bildirim tercihleri")


# --------------------------------------------------------------------------
# Zil kutusu, admin sol menüsü, Personel Yönetimi
# --------------------------------------------------------------------------
from datetime import timedelta as _td  # noqa: E402

from django.core.exceptions import ValidationError as _VE  # noqa: E402
from django.utils import timezone as _tz  # noqa: E402

from .staffing import business_days  # noqa: E402


def _mk_tech(username="ayse.demir", first="Ayşe", last="Demir", **extra):
    return User.objects.create_user(username, f"{username}@x.com", "Pass#12345", role=User.Role.TECHNICIAN,
                                    first_name=first, last_name=last, is_temporary_password=False, **extra)


def _mk_device(**extra):
    data = dict(customer_name="Mehmet", customer_phone="05321234567", brand="Dell", model="L5420", issue_description="x")
    data.update(extra)
    return Device.objects.create(**data)


class NotificationBellPopupTests(TestCase):
    def setUp(self):
        self.admin = User.objects.get(username="admin")

    def test_every_role_gets_popup_with_view_all_link(self):
        tech = _mk_tech()
        customer = User.objects.create_user("c@x.com", "c@x.com", "Pass#12345", role=User.Role.CUSTOMER,
                                            is_temporary_password=False)
        pages = {self.admin: "core:admin_dashboard", tech: "core:technician_dashboard", customer: "core:customer_home"}
        for user, name in pages.items():
            self.client.force_login(user)
            page = self.client.get(reverse(name))
            self.assertContains(page, 'id="nt-btn"')
            self.assertContains(page, 'id="nt-pop"')
            self.assertContains(page, "Tümünü görüntüle")
            self.assertContains(page, f'href="{reverse("core:notifications_list")}"')

    def test_admin_bell_is_button_not_link(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("core:staff_list"))
        self.assertContains(page, '<button type="button" class="nt-btn"')

    def test_technician_page_has_single_real_bell(self):
        self.client.force_login(_mk_tech())
        page = self.client.get(reverse("core:technician_dashboard"))
        self.assertContains(page, 'id="nt-btn"', count=1)
        self.assertNotContains(page, "tech-bell")

    def test_feed_returns_only_last_six(self):
        for i in range(8):
            Notification.objects.create(recipient=self.admin, title=f"Bildirim {i}")
        self.client.force_login(self.admin)
        feed = self.client.get(reverse("core:notifications_feed")).json()
        self.assertEqual(len(feed["recent"]), 6)
        self.assertEqual(feed["recent"][0]["title"], "Bildirim 7")
        self.assertEqual(feed["recent"][-1]["title"], "Bildirim 2")
        self.assertEqual(feed["unread"], 8)

    def test_view_all_opens_notifications_page(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("core:notifications_list")).status_code, 200)


class AdminSidebarTests(TestCase):
    def setUp(self):
        self.admin = User.objects.get(username="admin")
        self.client.force_login(self.admin)

    def test_menu_matches_design_and_unfinished_items_are_not_links(self):
        page = self.client.get(reverse("core:admin_dashboard"))
        for text in ("Anasayfa", "Cihazlar", "Yeni kayıtlar", "Tüm cihazlar", "Kargolanacaklar", "Personel Yönetimi",
                     "Stok Depo", "Bildirimlerim"):
            self.assertContains(page, text)
        # «Tüm cihazlar» artık gerçek bir bağlantı; yalnızca «Kargolanacaklar» henüz tıklanamaz.
        self.assertContains(page, f'<a class="nav-sub" href="{reverse("core:admin_device_list")}"')
        self.assertContains(page, 'aria-disabled="true" title="Yakında">Kargolanacaklar')
        self.assertNotContains(page, 'aria-disabled="true" title="Yakında">Tüm cihazlar')
        self.assertNotContains(page, "İşlem kayıtları")
        self.assertNotContains(page, ">İzinler<")

    def test_counts_come_from_devices(self):
        S = Device.Status
        tech = _mk_tech()
        for status in (S.PRE_REGISTERED, S.PRE_REGISTERED, S.AWAITING_CARGO):
            _mk_device(status=status)
        _mk_device(status=S.RECEIVED)                      # atanmamış: yeni kayıt
        _mk_device(status=S.RECEIVED, technician=tech)     # atanmış: sayılmaz
        _mk_device(status=S.READY, delivery_method="cargo")
        _mk_device(status=S.READY, delivery_method="cargo")
        _mk_device(status=S.READY, delivery_method="self")  # kargo değil: sayılmaz
        page = self.client.get(reverse("core:admin_dashboard"))
        self.assertEqual(page.context["nav_new_devices"], 4)
        self.assertEqual(page.context["nav_cargo_devices"], 2)
        self.assertContains(page, "nav-badge-teal")

    def test_devices_group_is_collapsible_with_three_subitems(self):
        _mk_device(status=Device.Status.PRE_REGISTERED)
        _mk_device(status=Device.Status.READY, delivery_method="cargo")
        page = self.client.get(reverse("core:admin_dashboard"))
        self.assertContains(page, 'id="nav-devices" aria-expanded="false" aria-controls="nav-devices-list"')
        self.assertContains(page, 'id="nav-devices-list"')
        self.assertEqual(page.context["nav_total_devices"], 2)

    def test_zero_counts_show_no_badge(self):
        page = self.client.get(reverse("core:admin_dashboard"))
        self.assertNotContains(page, "nav-badge-teal")

    def test_technician_menu_has_no_admin_items(self):
        self.client.force_login(_mk_tech())
        page = self.client.get(reverse("core:technician_dashboard"))
        for text in ("Cihazlarım", "Geçmiş işlerim", "Kargolanacaklar", "Bildirimlerim"):
            self.assertContains(page, text)
        self.assertNotContains(page, "Personel olarak görüntüle")
        self.assertNotContains(page, "Stok Depo")
        self.assertNotContains(page, "Personel Yönetimi")

    def test_view_as_staff_removed_from_sidebar(self):
        page = self.client.get(reverse("core:admin_dashboard"))
        self.assertNotContains(page, "Personel olarak görüntüle")
        self.assertNotContains(page, "nav-view")

    def test_view_as_staff_button_sits_next_to_names_and_is_disabled(self):
        tech = _mk_tech()
        page = self.client.get(reverse("core:staff_list"))
        self.assertContains(page, "Personel olarak görüntüle")
        self.assertContains(page, f'class="pm-viewas" disabled aria-disabled="true" title="Yakında" data-staff-id="{tech.pk}"')
        self.assertNotContains(page, "<a class=\"pm-viewas")


class PersonnelManagementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.get(username="admin")
        self.client.force_login(self.admin)
        self.today = _tz.localdate()
        self.ayse = _mk_tech()
        self.burak = _mk_tech("burak.yildiz", "Burak", "Yıldız", phone="0533 222 33 44")
        self.elif_ = _mk_tech("elif.celik", "Elif", "Çelik")

    def _leave(self, tech, start, end, status="approved", **extra):
        return TechnicianLeave.objects.create(technician=tech, start_date=start, end_date=end, status=status, **extra)

    def _post_leave(self, tech, start, end, **extra):
        data = {"leave_type": "annual", "start_date": start.isoformat(), "end_date": end.isoformat(), "description": ""}
        data.update(extra)
        return self.client.post(reverse("core:staff_leave_create", args=[tech.pk]), data)

    def test_page_renders_like_design(self):
        page = self.client.get(reverse("core:staff_list"))
        self.assertEqual(page.status_code, 200)
        for text in ("Personel Yönetimi", "Teknisyen hesapları, iş yükü ve izinler", "Yeni teknisyen", "Ad soyad",
                     "E-posta", "Telefon", "Açık iş", "Tamamlanan", "Durum", "İzin takvimi",
                     "Devam eden ve planlı izinler", "Ayşe Demir", "burak.yildiz", "0533 222 33 44", "İzin ver", "Sil",
                     "Silmek yerine pasife alınan teknisyenlerin geçmiş kayıtları korunur. "
                     "İzindeki teknisyenlere yeni iş atanmaz."):
            self.assertContains(page, text)
        self.assertContains(page, reverse("core:staff_create"))
        self.assertNotContains(page, "Bilgileri yeniden gönder")

    def test_only_technicians_listed_and_admin_only(self):
        rows = self.client.get(reverse("core:staff_list")).context["rows"]
        self.assertEqual({r["tech"].username for r in rows}, {"ayse.demir", "burak.yildiz", "elif.celik"})
        self.client.force_login(self.ayse)
        self.assertEqual(self.client.get(reverse("core:staff_list")).status_code, 403)

    def test_workload_and_leave_columns(self):
        S = Device.Status
        for status in (S.REPAIRING, S.WAITING_PART, S.DELIVERED, S.DELIVERED, S.DELIVERED, S.CANCELLED):
            _mk_device(status=status, technician=self.ayse)
        start, end = self.today - _td(days=1), self.today + _td(days=3)
        self._leave(self.burak, start, end, leave_type="annual")
        self._leave(self.elif_, self.today + _td(days=7), self.today + _td(days=11))
        rows = {r["tech"].username: r for r in self.client.get(reverse("core:staff_list")).context["rows"]}
        self.assertEqual((rows["ayse.demir"]["open_jobs"], rows["ayse.demir"]["done_jobs"]), (2, 3))
        self.assertEqual((rows["ayse.demir"]["state"], rows["ayse.demir"]["leave_text"]), ("active", ""))
        self.assertEqual(rows["burak.yildiz"]["state_label"], "İzinde")
        self.assertTrue(rows["burak.yildiz"]["leave_text"].startswith("Yıllık izin · "))
        self.assertEqual(rows["elif.celik"]["state_label"], "Aktif")
        self.assertTrue(rows["elif.celik"]["leave_text"].startswith("Planlı · "))

    def test_calendar_lists_ongoing_and_planned_only(self):
        start, end = self.today - _td(days=1), self.today + _td(days=3)
        self._leave(self.burak, start, end)
        self._leave(self.elif_, self.today + _td(days=7), self.today + _td(days=11))
        self._leave(self.ayse, self.today - _td(days=20), self.today - _td(days=15))               # geçmiş
        self._leave(self.ayse, self.today + _td(days=30), self.today + _td(days=31), status="pending")  # onaysız
        self._leave(self.ayse, self.today + _td(days=40), self.today + _td(days=41), status="cancelled")
        page = self.client.get(reverse("core:staff_list"))
        leaves = page.context["leaves"]
        self.assertEqual([l.technician.username for l in leaves], ["burak.yildiz", "elif.celik"])
        self.assertEqual([l.ongoing for l in leaves], [True, False])
        self.assertContains(page, "Devam ediyor")
        self.assertContains(page, "Planlandı")
        self.assertContains(page, f"{business_days(start, end)} iş günü")

    def test_business_days(self):
        import datetime
        self.assertEqual(business_days(datetime.date(2025, 10, 6), datetime.date(2025, 10, 10)), 5)
        self.assertEqual(business_days(datetime.date(2025, 10, 6), datetime.date(2025, 10, 12)), 5)

    def test_grant_leave_creates_approved_leave_and_notifies(self):
        start, end = self.today + _td(days=2), self.today + _td(days=6)
        self.assertEqual(self.client.get(reverse("core:staff_leave_create", args=[self.ayse.pk])).status_code, 200)
        out = self._post_leave(self.ayse, start, end, leave_type="sick")
        self.assertRedirects(out, reverse("core:staff_list"))
        leave = TechnicianLeave.objects.get(technician=self.ayse)
        self.assertEqual((leave.status, leave.leave_type, leave.reviewed_by), ("approved", "sick", self.admin))
        self.assertTrue(Notification.objects.filter(recipient=self.ayse, title="Size izin tanımlandı").exists())
        self.assertTrue(AuditLog.objects.filter(action="leave", description__contains="İzin verildi").exists())
        self.assertContains(self.client.get(reverse("core:staff_list")), "Hastalık izni")

    def test_grant_leave_validation(self):
        start, end = self.today + _td(days=2), self.today + _td(days=6)
        self._post_leave(self.ayse, start, end)
        # çakışma, ters tarih, geçmiş tarih, bozuk tarih reddedilir
        self.assertEqual(self._post_leave(self.ayse, start + _td(days=1), end + _td(days=3)).status_code, 200)
        self.assertEqual(self._post_leave(self.burak, end, start).status_code, 200)
        self.assertEqual(self._post_leave(self.burak, self.today - _td(days=9), self.today - _td(days=5)).status_code, 200)
        self.assertEqual(self.client.post(reverse("core:staff_leave_create", args=[self.burak.pk]),
                                          {"leave_type": "annual", "start_date": "x", "end_date": ""}).status_code, 200)
        self.assertEqual(self.client.post(reverse("core:staff_leave_create", args=[self.burak.pk]),
                                          {"leave_type": "bogus", "start_date": start.isoformat(),
                                           "end_date": end.isoformat()}).status_code, 200)
        self.assertEqual(TechnicianLeave.objects.count(), 1)

    def test_grant_leave_rules_for_roles_and_inactive(self):
        start, end = self.today + _td(days=2), self.today + _td(days=3)
        self.ayse.is_active = False
        self.ayse.save(update_fields=["is_active"])
        self.assertRedirects(self._post_leave(self.ayse, start, end), reverse("core:staff_list"))
        self.assertFalse(TechnicianLeave.objects.exists())
        self.assertEqual(self._post_leave(self.admin, start, end).status_code, 404)  # yalnızca teknisyen
        self.client.force_login(self.burak)
        self.assertEqual(self._post_leave(self.elif_, start, end).status_code, 403)

    def test_edit_leave(self):
        start, end = self.today + _td(days=2), self.today + _td(days=4)
        leave = self._leave(self.ayse, start, end)
        url = reverse("core:staff_leave_edit", args=[leave.pk])
        page = self.client.get(url)
        self.assertContains(page, start.isoformat())
        out = self.client.post(url, {"leave_type": "excuse", "start_date": start.isoformat(),
                                     "end_date": (end + _td(days=2)).isoformat(), "description": "Taşınma"})
        self.assertRedirects(out, reverse("core:staff_list"))
        leave.refresh_from_db()
        self.assertEqual((leave.leave_type, leave.end_date, leave.description), ("excuse", end + _td(days=2), "Taşınma"))
        self.assertTrue(Notification.objects.filter(recipient=self.ayse, title="İzniniz güncellendi").exists())

    def test_edit_cannot_overlap_other_leave_but_can_overlap_itself(self):
        start, end = self.today + _td(days=2), self.today + _td(days=4)
        mine = self._leave(self.ayse, start, end)
        self._leave(self.ayse, end + _td(days=2), end + _td(days=5))
        url = reverse("core:staff_leave_edit", args=[mine.pk])
        bad = self.client.post(url, {"leave_type": "annual", "start_date": start.isoformat(),
                                     "end_date": (end + _td(days=3)).isoformat()})
        self.assertEqual(bad.status_code, 200)
        ok = self.client.post(url, {"leave_type": "annual", "start_date": start.isoformat(),
                                    "end_date": (end + _td(days=1)).isoformat()})
        self.assertRedirects(ok, reverse("core:staff_list"))

    def test_finished_or_unapproved_leave_cannot_be_edited_or_cancelled(self):
        old = self._leave(self.ayse, self.today - _td(days=9), self.today - _td(days=5))
        pending = self._leave(self.ayse, self.today + _td(days=2), self.today + _td(days=3), status="pending")
        for leave in (old, pending):
            self.assertEqual(self.client.get(reverse("core:staff_leave_edit", args=[leave.pk])).status_code, 404)
            self.assertEqual(self.client.post(reverse("core:staff_leave_cancel", args=[leave.pk])).status_code, 404)

    def test_cancel_leave(self):
        leave = self._leave(self.burak, self.today - _td(days=1), self.today + _td(days=2))
        url = reverse("core:staff_leave_cancel", args=[leave.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertRedirects(self.client.post(url), reverse("core:staff_list"))
        leave.refresh_from_db()
        self.assertEqual(leave.status, "cancelled")
        self.assertTrue(Notification.objects.filter(recipient=self.burak, title="İzniniz iptal edildi").exists())
        rows = {r["tech"].username: r for r in self.client.get(reverse("core:staff_list")).context["rows"]}
        self.assertEqual(rows["burak.yildiz"]["state_label"], "Aktif")
        self.assertEqual(self.client.get(reverse("core:staff_list")).context["leaves"], [])

    def test_delete_without_history_removes_account(self):
        url = reverse("core:staff_delete", args=[self.elif_.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertRedirects(self.client.post(url), reverse("core:staff_list"))
        self.assertFalse(User.objects.filter(pk=self.elif_.pk).exists())

    def test_delete_with_history_archives_and_keeps_records(self):
        device = _mk_device(technician=self.ayse, status=Device.Status.DELIVERED)
        self._leave(self.ayse, self.today + _td(days=2), self.today + _td(days=3))
        url = reverse("core:staff_delete", args=[self.ayse.pk])
        self.client.post(url)
        self.ayse.refresh_from_db()
        self.assertFalse(self.ayse.is_active)
        device.refresh_from_db()
        self.assertEqual(device.technician, self.ayse)
        self.assertEqual(TechnicianLeave.objects.filter(technician=self.ayse).count(), 1)
        page = self.client.get(reverse("core:staff_list"))
        self.assertContains(page, "Pasif")
        self.assertContains(page, "Aktifleştir")
        self.client.post(url)  # ikinci kez: hâlâ silinmez
        self.assertTrue(User.objects.filter(pk=self.ayse.pk).exists())
        self.client.post(reverse("core:staff_toggle", args=[self.ayse.pk]))
        self.ayse.refresh_from_db()
        self.assertTrue(self.ayse.is_active)

    def test_delete_is_admin_only_and_technician_only(self):
        self.assertEqual(self.client.post(reverse("core:staff_delete", args=[self.admin.pk])).status_code, 404)
        self.client.force_login(self.burak)
        self.assertEqual(self.client.post(reverse("core:staff_delete", args=[self.elif_.pk])).status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.elif_.pk).exists())

    def test_no_new_jobs_for_technician_on_leave_or_inactive(self):
        self._leave(self.burak, self.today - _td(days=1), self.today + _td(days=2))
        device = _mk_device()
        device.technician = self.burak
        with self.assertRaises(_VE) as ctx:
            device.clean()
        self.assertIn("technician", ctx.exception.message_dict)
        device.technician = self.ayse
        device.clean()  # izinde olmayan teknisyene atanabilir
        self.ayse.is_active = False
        self.ayse.save(update_fields=["is_active"])
        with self.assertRaises(_VE):
            device.clean()

    def test_existing_assignment_not_blocked_when_leave_starts(self):
        device = _mk_device(technician=self.burak)
        self._leave(self.burak, self.today - _td(days=1), self.today + _td(days=2))
        device = Device.objects.get(pk=device.pk)
        device.clean()  # teknisyen değişmedi: mevcut iş etkilenmez


class AdminNewDevicesPageTests(TestCase):
    def setUp(self):
        self.admin = User.objects.get(username="admin")
        self.client.force_login(self.admin)
        self.url = reverse("core:admin_new_devices")

    def _mk(self, name, status=Device.Status.RECEIVED, **extra):
        return _mk_device(customer_name=name, status=status, **extra)

    def test_admin_only(self):
        self.client.force_login(_mk_tech())
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_lists_only_unassigned_new_registrations(self):
        S = Device.Status
        tech = _mk_tech()
        self._mk("Zehra Polat")
        self._mk("Kerem Arslan", status=S.PRE_REGISTERED)
        self._mk("Selin Aksoy", status=S.AWAITING_CARGO)
        self._mk("Murat Koç", status=S.IN_TRANSIT)
        self._mk("Atanmış Kişi", technician=tech)
        self._mk("Tamirde Kişi", status=S.REPAIRING)
        page = self.client.get(self.url)
        names = {d.customer_name for d in page.context["devices"]}
        self.assertEqual(names, {"Zehra Polat", "Kerem Arslan", "Selin Aksoy", "Murat Koç"})
        self.assertEqual(page.context["total_new"], 4)
        self.assertContains(page, "4 yeni kayıt teknisyen atanmasını bekliyor")
        self.assertContains(page, "Toplam 4 yeni kayıt")
        self.assertContains(page, "Atama bekliyor", count=1)  # yalnızca servise ulaşmış kayıt
        self.assertContains(page, "Kargo bekleniyor")

    def test_page_matches_design_and_assign_button_opens_detail(self):
        self._mk("Zehra Polat", brand="Canon", model="PIXMA G3420", issue_title="Kağıt sıkışıyor")
        page = self.client.get(self.url)
        for text in ("Yeni kayıtlar", "Müşterilerden gelen ve teknisyen atanmayı bekleyen servis kayıtları",
                     "Takip kodu", "Müşteri", "Cihaz", "Arıza özeti", "Kayıt tarihi", "Durum", "Canon PIXMA G3420",
                     "Kağıt sıkışıyor", "Detay ve ata", "Tüm cihaz türleri", "En yeni önce",
                     "Teknisyen atanan kayıtlar bu listeden çıkar ve “Tüm cihazlar” sayfasında izlenir."):
            self.assertContains(page, text)
        device = page.context["devices"][0]
        self.assertContains(page, f'class="nd-assign" href="{reverse("core:admin_new_device_detail", args=[device.pk])}"')
        self.assertNotContains(page, 'aria-disabled="true" title="Yakında">Detay ve ata')
        self.assertNotContains(page, "<form method=\"post\" action=\"/panel/cihazlar")

    def test_sidebar_link_active_and_group_open(self):
        page = self.client.get(self.url)
        self.assertContains(page, f'href="{self.url}" aria-current="page"')
        self.assertContains(page, 'aria-expanded="true" aria-controls="nav-devices-list"')
        other = self.client.get(reverse("core:admin_dashboard"))
        self.assertContains(other, 'aria-expanded="false" aria-controls="nav-devices-list"')
        self.assertContains(other, f'href="{self.url}"')

    def test_search_type_and_sort(self):
        self._mk("Ayşe", brand="HP", model="Pavilion 15", device_type="laptop")
        self._mk("Burak", brand="Canon", model="G3420", device_type="printer")
        names = lambda q: [d.customer_name for d in self.client.get(self.url, q).context["devices"]]
        self.assertEqual(names({"q": "canon"}), ["Burak"])
        self.assertEqual(names({"q": "Ayşe"}), ["Ayşe"])
        self.assertEqual(names({"type": "printer"}), ["Burak"])
        self.assertEqual(names({"type": "bogus"}), ["Burak", "Ayşe"])  # geçersiz tür yok sayılır
        self.assertEqual(names({"sort": "old"}), ["Ayşe", "Burak"])
        self.assertEqual(names({}), ["Burak", "Ayşe"])
        page = self.client.get(self.url, {"q": "yok böyle"})
        self.assertContains(page, "Aramanıza uyan yeni kayıt bulunamadı.")
        self.assertContains(page, "2 yeni kayıt teknisyen atanmasını bekliyor")  # banner filtreden bağımsız

    def test_empty_state(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Teknisyen atanmasını bekleyen yeni kayıt yok")
        self.assertContains(page, "Toplam 0 yeni kayıt")

    def test_date_label_is_turkish(self):
        d = self._mk("Zehra")
        local = _tz.localtime(d.received_at)
        label = self.client.get(self.url).context["devices"][0].registered_label
        self.assertRegex(label, rf"^{local.day} [A-ZÇĞİÖŞÜ][a-zçğıöşü]+ {local.year} · {local:%H:%M}$")


class TechnicianAssignmentTests(TestCase):
    def setUp(self):
        from .staffing import assign_device
        self.assign = assign_device
        self.admin = User.objects.get(username="admin")
        self.tech = _mk_tech()
        self.other = _mk_tech("burak.yildiz", "Burak", "Yıldız")
        self.device = _mk_device(brand="Dell", model="Latitude 5420", issue_description="Ekran titriyor.")
        Notification.objects.all().delete()

    def test_assignment_notifies_technician_and_lands_on_panel(self):
        self.assign(self.device, self.tech, self.admin)
        self.device.refresh_from_db()
        self.assertEqual(self.device.technician, self.tech)
        self.assertIsNotNone(self.device.assigned_at)
        note = Notification.objects.get(recipient=self.tech)
        self.assertEqual(note.title, "Size yeni bir iş atandı")
        self.assertIn(self.device.tracking_id, note.body)
        self.assertEqual(note.url, reverse("core:technician_dashboard"))
        self.assertEqual(note.device, self.device)
        self.assertFalse(Notification.objects.filter(recipient=self.admin).exists())
        self.client.force_login(self.tech)
        page = self.client.get(reverse("core:technician_dashboard"))
        self.assertContains(page, "Dell Latitude 5420")
        self.assertContains(page, self.device.tracking_id)
        self.assertContains(page, "Yeni atandı")
        self.assertEqual(self.client.get(reverse("core:notifications_feed")).json()["unread"], 1)

    def test_opening_notification_goes_to_technician_panel_and_marks_read(self):
        self.assign(self.device, self.tech, self.admin)
        note = Notification.objects.get(recipient=self.tech)
        self.client.force_login(self.tech)
        out = self.client.get(reverse("core:notification_open", args=[note.pk]))
        self.assertRedirects(out, reverse("core:technician_dashboard"))
        note.refresh_from_db()
        self.assertIsNotNone(note.read_at)

    def test_device_only_visible_to_assigned_technician(self):
        self.assign(self.device, self.tech, self.admin)
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse("core:technician_dashboard")), self.device.tracking_id)

    def test_reassignment_notifies_both_technicians(self):
        self.assign(self.device, self.tech, self.admin)
        Notification.objects.all().delete()
        self.assign(self.device, self.other, self.admin)
        self.assertTrue(Notification.objects.filter(recipient=self.other, title="Size yeni bir iş atandı").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.tech, title="Bir iş sizden alındı").exists())

    def test_saving_other_fields_does_not_renotify(self):
        self.assign(self.device, self.tech, self.admin)
        Notification.objects.all().delete()
        self.device.refresh_from_db()
        self.device.diagnosis_notes = "Not"
        self.device.save()
        self.assertFalse(Notification.objects.filter(recipient=self.tech).exists())

    def test_assignment_via_django_admin_save_also_notifies(self):
        Device.objects.filter(pk=self.device.pk)  # ORM kaydı (admin formu da save() çağırır)
        self.device.technician = self.tech
        self.device.save()
        self.assertEqual(Notification.objects.filter(recipient=self.tech, title="Size yeni bir iş atandı").count(), 1)

    def test_cannot_assign_to_technician_on_leave_inactive_or_non_technician(self):
        from django.core.exceptions import ValidationError
        today = _tz.localdate()
        TechnicianLeave.objects.create(technician=self.tech, start_date=today, end_date=today + _td(days=2),
                                       status="approved")
        with self.assertRaises(ValidationError):
            self.assign(self.device, self.tech, self.admin)
        self.other.is_active = False
        self.other.save(update_fields=["is_active"])
        with self.assertRaises(ValidationError):
            self.assign(self.device, self.other, self.admin)
        with self.assertRaises(ValidationError):
            self.assign(self.device, self.admin, self.admin)
        self.device.refresh_from_db()
        self.assertIsNone(self.device.technician)
        self.assertFalse(Notification.objects.exists())

    def test_new_assignment_floats_to_top_and_is_marked_new(self):
        old = _mk_device(brand="HP", model="Pavilion", technician=self.tech)
        Device.objects.filter(pk=old.pk).update(received_at=_tz.now() - _td(days=30), assigned_at=None)
        self.assign(self.device, self.tech, self.admin)
        cards = self.client.force_login(self.tech) or self.client.get(reverse("core:technician_dashboard")).context["device_cards"]
        self.assertEqual([c.pk for c in cards], [self.device.pk, old.pk])
        self.assertTrue(cards[0].is_new)
        self.assertFalse(cards[1].is_new)

    def test_panel_matches_design(self):
        _mk_device(brand="Dell", model="Latitude 5420", status="repairing", technician=self.tech)
        _mk_device(brand="Apple", model="iPhone 12", status="waiting_part", technician=self.tech)
        _mk_device(brand="Lenovo", model="IdeaPad 3", status="diagnosing", technician=self.tech)
        self.client.force_login(self.tech)
        page = self.client.get(reverse("core:technician_dashboard"))
        for text in ("Cihazlarım", "Ayşe Demir, size atanan 3 aktif kayıt", "Tamirde", "Parça bekleyen", "İnceleniyor",
                     "Tümü", "Aktif", "Tamamlanan", "Detayı aç", "Parça bekleniyor", "Kayıt:"):
            self.assertContains(page, text)
        self.assertContains(page, 'class="tech-open" href="/teknisyen/cihaz/', count=3)         # «Detayı aç» artık cihaz sayfasına gider
        self.assertNotContains(page, 'aria-disabled="true" title="Yakında">Detayı aç')
        self.assertEqual((page.context["repairing_count"], page.context["waiting_part_count"],
                          page.context["diagnosing_count"]), (1, 1, 1))


# --------------------------------------------------------------------------
# LDAP modu: kimlik bilgileri dizinde (sahte MemoryDirectory), uygulama verisi veritabanında
# --------------------------------------------------------------------------
from io import StringIO  # noqa: E402
from unittest import mock  # noqa: E402

from django.core.management import call_command  # noqa: E402

from . import accounts as _acc  # noqa: E402
from .directory import DirectoryEntry, DirectoryUnavailable, MemoryDirectory  # noqa: E402

TEMP_PW = "Temp#Pass2026x"


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class LdapModeBase(TestCase):
    def setUp(self):
        self.dir = MemoryDirectory()
        _acc.set_directory(self.dir)
        override = override_settings(LDAP_ENABLED=True, AUTHENTICATION_BACKENDS=["core.ldap_backend.LdapBackend"])
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(_acc.set_directory, None)
        self.admin = User.objects.get(username="admin")
        self.dir.create(DirectoryEntry("admin", "admin", "admin@fixlab.local", "Sistem", "Yöneticisi"), "Admin#Sifre2026")

    def _login(self, username, password, url="core:login"):
        self.client.logout()
        return self.client.post(reverse(url), {"username": username, "password": password})

    def _authed(self):
        return "_auth_user_id" in self.client.session

    def _tech_in_ldap(self, username="mert.kaya", email="mert@example.com", password="Mert#Sifre2026"):
        user = User(username=username, email=email, first_name="Mert", last_name="Kaya", role="technician",
                    is_temporary_password=False)
        _acc.create_account(user, password)
        return user


class LdapLoginTests(LdapModeBase):
    def test_password_is_checked_in_ldap(self):
        self._login("admin", "yanlis-sifre")
        self.assertFalse(self._authed())
        self.assertRedirects(self._login("admin", "Admin#Sifre2026"), reverse("core:home"), fetch_redirect_response=False)
        self.assertTrue(self._authed())
        self.assertEqual(self.client.get(reverse("core:admin_dashboard")).status_code, 200)  # oturum sonraki istekte de geçerli
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.has_usable_password())  # gölge kayıtta şifre tutulmaz

    def test_old_local_password_no_longer_works(self):
        local = User.objects.create_user("yerel", "yerel@x.com", "Yerel#Sifre2026", role="technician",
                                         is_temporary_password=False)
        self._login("yerel", "Yerel#Sifre2026")
        self.assertFalse(self._authed())
        self.assertTrue(local.check_password("Yerel#Sifre2026"))  # yalnızca yerel özet duruyor; giriş LDAP'tan

    def test_empty_password_rejected(self):
        self._login("admin", "")
        self.assertFalse(self._authed())

    def test_ldap_only_account_is_provisioned_on_first_login(self):
        self.dir.create(DirectoryEntry("elif.celik", "technician", "elif@example.com", "Elif", "Çelik", "0534 333 44 55"), "Elif#Sifre2026")
        self.assertFalse(User.objects.filter(username="elif.celik").exists())
        self._login("elif@example.com", "Elif#Sifre2026")
        self.assertTrue(self._authed())
        user = User.objects.get(username="elif.celik")
        self.assertEqual((user.role, user.email, user.first_name, user.phone, user.is_staff, user.is_temporary_password),
                         ("technician", "elif@example.com", "Elif", "0534 333 44 55", False, False))
        self.assertFalse(user.has_usable_password())

    def test_login_with_email_works_for_ldap_accounts(self):
        self._tech_in_ldap()
        self._login("mert@example.com", "Mert#Sifre2026")
        self.assertTrue(self._authed())

    def test_username_login_rejected_for_ldap_staff_but_first_admin_allowed(self):
        self._tech_in_ldap()
        self._login("mert.kaya", "Mert#Sifre2026")
        self.assertFalse(self._authed())
        self._login("admin", "Admin#Sifre2026")
        self.assertTrue(self._authed())

    def test_role_comes_from_directory_ou(self):
        user = User.objects.create_user("terfi", "terfi@x.com", "x", role="technician", is_temporary_password=False)
        self.dir.create(DirectoryEntry("terfi", "admin", "terfi@x.com"), "Terfi#Sifre2026")
        self._login("terfi@x.com", "Terfi#Sifre2026")
        user.refresh_from_db()
        self.assertEqual((user.role, user.is_superuser), ("admin", True))
        self.dir.update(DirectoryEntry("terfi", "technician", "terfi@x.com"))
        self._login("terfi@x.com", "Terfi#Sifre2026")
        user.refresh_from_db()
        self.assertEqual((user.role, user.is_superuser, user.is_staff), ("technician", False, False))

    def test_inactive_account_cannot_login_even_if_in_directory(self):
        user = self._tech_in_ldap()
        user.is_active = False
        user.save(update_fields=["is_active"])
        self._login("mert@example.com", "Mert#Sifre2026")
        self.assertFalse(self._authed())

    def test_customer_login_with_email(self):
        customer = User(username="musteri@example.com", email="musteri@example.com", first_name="Ayşe", last_name="Demir",
                        role="customer", is_temporary_password=False)
        _acc.create_account(customer, "Musteri#Sifre2026")
        response = self._login("musteri@example.com", "Musteri#Sifre2026", url="core:customer_login")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self._authed())
        self.assertEqual(self.client.get(reverse("core:customer_home")).status_code, 200)

    def test_customer_cannot_log_in_from_staff_login_ldap(self):
        customer = User(username="musteri@example.com", email="musteri@example.com", first_name="Ayşe", last_name="Demir",
                        role="customer", is_temporary_password=False)
        _acc.create_account(customer, "Musteri#Sifre2026")
        response = self._login("musteri@example.com", "Musteri#Sifre2026")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bu giriş personel içindir. Müşteri girişini kullanın.")
        self.assertFalse(self._authed())

    def test_directory_down_shows_real_reason(self):
        with mock.patch.object(self.dir, "authenticate", side_effect=DirectoryUnavailable("kapalı")):
            response = self._login("admin", "Admin#Sifre2026")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kimlik doğrulama sunucusuna")
        self.assertFalse(self._authed())


class LdapAccountFlowTests(LdapModeBase):
    def test_customer_profile_update_is_written_to_ldap_and_failure_changes_nothing(self):
        customer = User(username="musteri@example.com", email="musteri@example.com", first_name="Ayşe", last_name="Demir",
                        role="customer", is_temporary_password=False)
        _acc.create_account(customer, "Musteri#Sifre2026")
        self.client.force_login(customer)
        data = {"first_name": "Ayşenur", "last_name": "Kaya", "phone": "0533 111 22 33", "city": "Düzce",
                "district": "Merkez", "address": "Gül Sk. No:5"}
        self.assertRedirects(self.client.post(reverse("core:customer_profile"), data), reverse("core:customer_profile"))
        entry = self.dir.find("musteri@example.com")
        self.assertEqual((entry.first_name, entry.last_name, entry.phone), ("Ayşenur", "Kaya", "(533) 111 22 33"))
        customer.refresh_from_db()
        self.assertEqual((customer.city, customer.address), ("Düzce", "Gül Sk. No:5"))
        # dizin ulaşılamıyorsa hiçbir şey değişmez
        with mock.patch("core.accounts.update_account", side_effect=_acc.DirectoryUnavailable("down")):
            response = self.client.post(reverse("core:customer_profile"), {**data, "first_name": "Zeynep"})
        self.assertEqual(response.status_code, 200)
        customer.refresh_from_db()
        self.assertEqual(customer.first_name, "Ayşenur")

    def test_admin_creates_technician_in_ldap(self):
        self.client.force_login(self.admin)
        with mock.patch("core.services.generate_temp_password", return_value=TEMP_PW):
            response = self.client.post(reverse("core:staff_create"), {
                "role": "technician", "first_name": "Ayşe", "last_name": "Demir", "email": "ayse@example.com",
                "phone": "0532 111 22 33", "specialization": ""})
        self.assertRedirects(response, reverse("core:staff_list"))
        user = User.objects.get(email="ayse@example.com")
        self.assertEqual(user.username, "ayse.demir")
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.is_temporary_password)
        entry = self.dir.find("ayse.demir")
        self.assertEqual((entry.role, entry.email, entry.phone), ("technician", "ayse@example.com", "(532) 111 22 33"))
        self.assertIsNotNone(self.dir.authenticate("ayse.demir", TEMP_PW))
        self.assertIn(TEMP_PW, mail.outbox[-1].body)
        # ilk giriş: geçici şifre LDAP'ta doğrulanır, kalıcı şifre ekranına yönlendirilir
        self._login("ayse@example.com", TEMP_PW)
        self.assertTrue(self._authed())
        self.assertRedirects(self.client.get(reverse("core:home")), reverse("core:force_password_change"),
                             fetch_redirect_response=False)

    def test_forced_password_change_writes_to_ldap(self):
        self.client.force_login(self.admin)
        with mock.patch("core.services.generate_temp_password", return_value=TEMP_PW):
            self.client.post(reverse("core:staff_create"), {"role": "technician", "first_name": "Ayşe", "last_name": "Demir",
                                                            "email": "ayse@example.com", "phone": "", "specialization": ""})
        self._login("ayse@example.com", TEMP_PW)
        url = reverse("core:force_password_change")
        bad = self.client.post(url, {"old_password": "yanlis", "new_password1": "Karpuz-Bulut-7391!", "new_password2": "Karpuz-Bulut-7391!"})
        self.assertEqual(bad.status_code, 200)
        self.assertIsNotNone(self.dir.authenticate("ayse.demir", TEMP_PW))  # yanlış eski şifre: değişmedi
        ok = self.client.post(url, {"old_password": TEMP_PW, "new_password1": "Karpuz-Bulut-7391!", "new_password2": "Karpuz-Bulut-7391!"})
        self.assertRedirects(ok, reverse("core:technician_dashboard"), fetch_redirect_response=False)
        self.assertIsNone(self.dir.authenticate("ayse.demir", TEMP_PW))
        self.assertIsNotNone(self.dir.authenticate("ayse.demir", "Karpuz-Bulut-7391!"))
        user = User.objects.get(username="ayse.demir")
        self.assertFalse(user.is_temporary_password)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(self.client.get(reverse("core:technician_dashboard")).status_code, 200)  # oturum açık kaldı

    def test_staff_create_rejects_email_already_in_directory(self):
        self.dir.create(DirectoryEntry("baskasi", "technician", "ayse@example.com"), "Baskasi#Sifre2026")
        self.client.force_login(self.admin)
        response = self.client.post(reverse("core:staff_create"), {
            "role": "technician", "first_name": "Ayşe", "last_name": "Demir", "email": "ayse@example.com",
            "phone": "", "specialization": ""})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="ayse.demir").exists())
        self.assertIsNone(self.dir.find("ayse.demir"))

    def test_username_collision_in_directory_gets_suffix(self):
        self.dir.create(DirectoryEntry("ayse.demir", "technician", "eski@example.com"), "Eski#Sifre2026")  # veritabanında yok
        self.client.force_login(self.admin)
        self.client.post(reverse("core:staff_create"), {"role": "technician", "first_name": "Ayşe", "last_name": "Demir",
                                                        "email": "yeni@example.com", "phone": "", "specialization": ""})
        user = User.objects.get(email="yeni@example.com")
        self.assertTrue(user.username.startswith("ayse.demir") and user.username != "ayse.demir")
        self.assertIsNotNone(self.dir.find(user.username))

    def test_directory_failure_leaves_no_half_created_account(self):
        self.client.force_login(self.admin)
        with mock.patch.object(self.dir, "create", side_effect=DirectoryUnavailable("kapalı")):
            response = self.client.post(reverse("core:staff_create"), {
                "role": "technician", "first_name": "Ayşe", "last_name": "Demir", "email": "ayse@example.com",
                "phone": "", "specialization": ""})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="ayse@example.com").exists())

    def test_database_failure_rolls_back_directory_entry(self):
        from django.db import IntegrityError, transaction
        user = User(username="rollback", email="admin@fixlab.local", first_name="R", last_name="B", role="technician")  # e-posta çakışır
        with self.assertRaises(IntegrityError), transaction.atomic():
            _acc.create_account(user, "Rollback#Sifre2026")
        self.assertIsNone(self.dir.find("rollback"))

    def test_customer_register_creates_ldap_account(self):
        data = {"first_name": "Zehra", "last_name": "Polat", "email": "zehra@example.com", "phone": "0532 111 22 33",
                "password1": "Karpuz-Bulut-7391!", "password2": "Karpuz-Bulut-7391!", "accept_terms": "on"}
        self.assertRedirects(self.client.post(reverse("core:customer_register"), data), reverse("core:customer_verify"))
        entry = self.dir.find("zehra@example.com")
        self.assertEqual((entry.role, entry.first_name), ("customer", "Zehra"))
        self.assertIsNotNone(self.dir.authenticate("zehra@example.com", "Karpuz-Bulut-7391!"))
        user = User.objects.get(email="zehra@example.com")
        self.assertFalse(user.has_usable_password())
        # e-posta doğrulanmadan giriş yapılamaz; kod girilince yapılabilir
        self._login("zehra@example.com", "Karpuz-Bulut-7391!", url="core:customer_login")
        self.assertFalse(self._authed())
        code = re.search(r"Doğrulama kodunuz: (\d{6})", mail.outbox[0].body).group(1)
        self.assertRedirects(self.client.post(reverse("core:customer_verify"), {"code": code}), reverse("core:customer_login"))
        self._login("zehra@example.com", "Karpuz-Bulut-7391!", url="core:customer_login")
        self.assertTrue(self._authed())

    def test_customer_register_rejects_email_that_exists_only_in_directory(self):
        self.dir.create(DirectoryEntry("zehra@example.com", "customer", "zehra@example.com"), "Karpuz-Bulut-7391!")
        data = {"first_name": "Zehra", "last_name": "Polat", "email": "zehra@example.com", "phone": "",
                "password1": "Cinar-Ordek-5528!", "password2": "Cinar-Ordek-5528!", "accept_terms": "on"}
        self.assertEqual(self.client.post(reverse("core:customer_register"), data).status_code, 200)
        self.assertFalse(User.objects.filter(email="zehra@example.com").exists())
        self.assertIsNone(self.dir.authenticate("zehra@example.com", "Cinar-Ordek-5528!"))

    def test_password_reset_flow_writes_to_ldap(self):
        self._tech_in_ldap()
        self.client.post(reverse("core:staff_forgot"), {"email": "mert@example.com"})
        code = re.search(r"Doğrulama kodunuz: (\d{6})", mail.outbox[-1].body).group(1)
        self.assertRedirects(self.client.post(reverse("core:staff_reset_code"), {"code": code}), reverse("core:staff_reset_password"))
        out = self.client.post(reverse("core:staff_reset_password"),
                               {"password1": "Karpuz-Bulut-7391!", "password2": "Karpuz-Bulut-7391!"})
        self.assertRedirects(out, reverse("core:login"), fetch_redirect_response=False)
        self.assertIsNone(self.dir.authenticate("mert.kaya", "Mert#Sifre2026"))
        self.assertIsNotNone(self.dir.authenticate("mert.kaya", "Karpuz-Bulut-7391!"))
        self._login("mert@example.com", "Karpuz-Bulut-7391!")
        self.assertTrue(self._authed())

    def test_customer_password_reset_flow_writes_to_ldap(self):
        customer = User(username="c@example.com", email="c@example.com", first_name="C", last_name="K", role="customer",
                        is_temporary_password=False)
        _acc.create_account(customer, "Musteri#Sifre2026")
        self.client.post(reverse("core:customer_forgot"), {"email": "c@example.com"})
        code = re.search(r"Doğrulama kodunuz: (\d{6})", mail.outbox[-1].body).group(1)
        self.client.post(reverse("core:customer_reset_code"), {"code": code})
        self.client.post(reverse("core:customer_reset_password"), {"password1": "Karpuz-Bulut-7391!", "password2": "Karpuz-Bulut-7391!"})
        self.assertIsNone(self.dir.authenticate("c@example.com", "Musteri#Sifre2026"))
        self.assertIsNotNone(self.dir.authenticate("c@example.com", "Karpuz-Bulut-7391!"))

    def test_reset_shows_error_when_directory_down(self):
        self._tech_in_ldap()
        self.client.post(reverse("core:staff_forgot"), {"email": "mert@example.com"})
        code = re.search(r"Doğrulama kodunuz: (\d{6})", mail.outbox[-1].body).group(1)
        self.client.post(reverse("core:staff_reset_code"), {"code": code})
        with mock.patch.object(self.dir, "set_password", side_effect=DirectoryUnavailable("kapalı")):
            response = self.client.post(reverse("core:staff_reset_password"),
                                        {"password1": "Karpuz-Bulut-7391!", "password2": "Karpuz-Bulut-7391!"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kimlik doğrulama sunucusuna")
        self.assertIsNotNone(self.dir.authenticate("mert.kaya", "Mert#Sifre2026"))  # eski şifre duruyor

    def test_resend_credentials_changes_ldap_password(self):
        tech = self._tech_in_ldap()
        self.client.force_login(self.admin)
        with mock.patch("core.services.generate_temp_password", return_value=TEMP_PW):
            self.client.post(reverse("core:staff_resend", args=[tech.pk]))
        self.assertIsNone(self.dir.authenticate("mert.kaya", "Mert#Sifre2026"))
        self.assertIsNotNone(self.dir.authenticate("mert.kaya", TEMP_PW))
        tech.refresh_from_db()
        self.assertTrue(tech.is_temporary_password)

    def test_deleting_technician_removes_directory_entry(self):
        tech = self._tech_in_ldap()
        self.client.force_login(self.admin)
        self.client.post(reverse("core:staff_delete", args=[tech.pk]))
        self.assertFalse(User.objects.filter(pk=tech.pk).exists())
        self.assertIsNone(self.dir.find("mert.kaya"))

    def test_archived_technician_keeps_entry_but_cannot_login(self):
        tech = self._tech_in_ldap()
        _mk_device(technician=tech, status=Device.Status.DELIVERED)
        self.client.force_login(self.admin)
        self.client.post(reverse("core:staff_delete", args=[tech.pk]))
        tech.refresh_from_db()
        self.assertFalse(tech.is_active)
        self.assertIsNotNone(self.dir.find("mert.kaya"))
        self._login("mert@example.com", "Mert#Sifre2026")
        self.assertFalse(self._authed())

    def test_delete_blocked_when_directory_down(self):
        tech = self._tech_in_ldap()
        self.client.force_login(self.admin)
        with mock.patch.object(self.dir, "delete", side_effect=DirectoryUnavailable("kapalı")):
            self.client.post(reverse("core:staff_delete", args=[tech.pk]))
        self.assertTrue(User.objects.filter(pk=tech.pk).exists())  # dizin silinemediyse kullanıcı da silinmez

    def test_update_account_pushes_changes_to_directory(self):
        tech = self._tech_in_ldap()
        tech.first_name, tech.phone = "Mehmet", "0533 000 00 00"
        tech.save()
        _acc.update_account(tech)
        entry = self.dir.find("mert.kaya")
        self.assertEqual((entry.first_name, entry.phone), ("Mehmet", "0533 000 00 00"))

    def test_verify_password_and_apply_password(self):
        tech = self._tech_in_ldap()
        self.assertTrue(_acc.verify_password(tech, "Mert#Sifre2026"))
        self.assertFalse(_acc.verify_password(tech, "yanlis"))
        _acc.apply_password(tech, "Karpuz-Bulut-7391!")
        self.assertTrue(_acc.verify_password(tech, "Karpuz-Bulut-7391!"))

    def test_apply_password_creates_missing_directory_entry(self):
        legacy = User.objects.create_user("eski", "eski@x.com", "x", role="technician", is_temporary_password=False)
        _acc.apply_password(legacy, "Eski#Sifre2026zz")
        self.assertEqual(self.dir.find("eski").role, "technician")
        self.assertTrue(_acc.verify_password(legacy, "Eski#Sifre2026zz"))


class LdapCommandTests(LdapModeBase):
    def setUp(self):
        super().setUp()
        self.dir = MemoryDirectory()  # boş dizin: taşıma komutu sıfırdan doldurmalı
        _acc.set_directory(self.dir)

    def test_bootstrap_moves_existing_users_to_directory(self):
        from django.conf import settings
        User.objects.create_user("sila.karatas", "sila@x.com", "EskiSifre#1", role="technician",
                                 first_name="Sıla", last_name="Karataş", is_temporary_password=False)
        User.objects.create_user("musteri@x.com", "musteri@x.com", "EskiSifre#2", role="customer", is_temporary_password=False)
        out = StringIO()
        call_command("ldap_bootstrap", stdout=out)
        self.assertEqual({e.uid: e.role for e in self.dir.list_users()},
                         {"admin": "admin", "sila.karatas": "technician", "musteri@x.com": "customer"})
        self.assertIsNotNone(self.dir.authenticate("admin", settings.DEFAULT_ADMIN_PASSWORD))
        self.assertIsNone(self.dir.authenticate("sila.karatas", "EskiSifre#1"))  # eski özet taşınamaz
        text = out.getvalue()
        self.assertIn("sila.karatas", text)
        self.assertNotIn("EskiSifre", text)
        # geçici şifre çıktıda yazar ve dizinde geçerlidir
        password = re.search(r"sila\.karatas\s+Teknisyen\s+(\S+)", text).group(1)
        self.assertIsNotNone(self.dir.authenticate("sila.karatas", password))
        tech = User.objects.get(username="sila.karatas")
        customer = User.objects.get(username="musteri@x.com")
        self.assertEqual((tech.is_temporary_password, customer.is_temporary_password), (True, False))
        self.assertFalse(tech.has_usable_password())
        self.assertFalse(User.objects.get(username="admin").is_temporary_password)

    def test_bootstrap_is_idempotent_and_does_not_reset_passwords(self):
        call_command("ldap_bootstrap", stdout=StringIO())
        before = {e.uid for e in self.dir.list_users()}
        self.dir.set_password("admin", "Degisti#Sifre2026")
        out = StringIO()
        call_command("ldap_bootstrap", stdout=out)
        self.assertEqual({e.uid for e in self.dir.list_users()}, before)
        self.assertIsNotNone(self.dir.authenticate("admin", "Degisti#Sifre2026"))
        self.assertIn("0 kullanıcı LDAP'a taşındı", out.getvalue())

    def test_bootstrap_requires_ldap_enabled(self):
        from django.core.management.base import CommandError
        with override_settings(LDAP_ENABLED=False):
            with self.assertRaises(CommandError):
                call_command("ldap_bootstrap", stdout=StringIO())

    def test_bootstrap_notify_sends_email(self):
        User.objects.create_user("sila.karatas", "sila@x.com", "x", role="technician", is_temporary_password=False)
        call_command("ldap_bootstrap", "--notify", stdout=StringIO())
        self.assertEqual([m.to for m in mail.outbox], [["sila@x.com"]])

    def test_ldap_check_reports_missing_users(self):
        User.objects.create_user("sila.karatas", "sila@x.com", "x", role="technician", is_temporary_password=False)
        out = StringIO()
        call_command("ldap_check", stdout=out)
        self.assertIn("sila.karatas", out.getvalue())
        call_command("ldap_bootstrap", stdout=StringIO())
        out = StringIO()
        call_command("ldap_check", stdout=out)
        self.assertIn("Tüm veritabanı kullanıcılarının dizinde kaydı var", out.getvalue())

    def test_create_default_admin_writes_password_to_directory(self):
        from django.conf import settings
        call_command("create_default_admin", "--reset-password", stdout=StringIO())
        self.assertIsNotNone(self.dir.authenticate("admin", settings.DEFAULT_ADMIN_PASSWORD))


class SignalRawGuardTests(TestCase):
    def test_loaddata_style_raw_save_creates_no_notifications_or_history(self):
        tech = _mk_tech()
        device = Device(customer_name="X", customer_phone="05321234567", brand="Dell", model="L", issue_description="x",
                        tracking_id="TS-2000-1001", technician=tech)
        from django.db.models.signals import post_save, pre_save
        pre_save.send(sender=Device, instance=device, raw=True)
        post_save.send(sender=Device, instance=device, created=True, raw=True)
        self.assertFalse(Notification.objects.filter(recipient=tech).exists())
        self.assertIsNone(device.assigned_at)


# --------------------------------------------------------------------------
# Yeni kayıt › Detay ve ata (açılır liste, iş yükü, A–Z sıralama) ve ad/soyad kısıtları
# --------------------------------------------------------------------------
class AdminNewDeviceDetailTests(TestCase):
    def setUp(self):
        self.admin = User.objects.get(username="admin")
        self.client.force_login(self.admin)
        self.device = _mk_device(customer_name="Murat Koç", brand="HP", model="Pavilion 15-eg2012nt",
                                 device_type="desktop", serial_number="5CD2314XYZ",
                                 issue_title="Mavi ekran", issue_description="Açılışta mavi ekran hatası veriyor.")
        self.url = reverse("core:admin_new_device_detail", args=[self.device.pk])

    def _jobs(self, tech, count):
        for _ in range(count):
            _mk_device(technician=tech, status=Device.Status.REPAIRING)

    def test_admin_only_and_only_new_records(self):
        self.client.force_login(_mk_tech("tek.nisyen", "Tek", "Nisyen"))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.admin)
        repairing = _mk_device(status=Device.Status.REPAIRING)
        self.assertEqual(self.client.get(reverse("core:admin_new_device_detail", args=[repairing.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("core:admin_new_device_detail", args=[999999])).status_code, 404)

    def test_detail_shows_design_sections(self):
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        for text in ("Yeni kayıtlara dön", "HP Pavilion 15-eg2012nt", self.device.tracking_id, "Atama bekliyor",
                     "Cihaz bilgileri", "Masaüstü bilgisayar", "5CD2314XYZ", "Açılışta mavi ekran hatası veriyor.",
                     "Müşteri bilgileri", "Murat Koç", "Durum — hangi aşamada?", "Kayıt alındı",
                     "Kayıt teknisyen atanmayı bekliyor; atama yapılınca teknisyen paneline düşer.",
                     "İşlem geçmişi", "Kayıt özeti", "Henüz atanmadı", "Belirlenmedi",
                     "Müşterinin önceki kayıtları", "Teknisyene ata", "Teknisyene not", "Vazgeç"):
            self.assertContains(page, text)
        self.assertContains(page, f'href="{reverse("core:admin_new_devices")}"')

    def test_sidebar_stays_on_new_records(self):
        page = self.client.get(self.url)
        self.assertContains(page, f'href="{reverse("core:admin_new_devices")}" aria-current="page"')
        self.assertContains(page, 'aria-expanded="true" aria-controls="nav-devices-list"')

    def test_stage_bar_hides_cargo_steps_for_hand_delivery(self):
        page = self.client.get(self.url)
        self.assertEqual(len(page.context["steps"]), 5)
        self.assertNotContains(page, "Kargoda · Servise geliyor")
        cargo = _mk_device(delivery_method="cargo", status=Device.Status.IN_TRANSIT)
        page = self.client.get(reverse("core:admin_new_device_detail", args=[cargo.pk]))
        self.assertEqual(len(page.context["steps"]), 7)
        self.assertContains(page, "Mevcut aşama: Kargoda · Servise geliyor.")

    def test_technicians_sorted_by_fewest_jobs_then_name_with_jobs_and_specialization(self):
        zehra = _mk_tech("zehra.polat", "Zehra", "Polat", specialization="Yazıcı")
        _mk_tech("ayse.demir", "Ayşe", "Demir", specialization="Dizüstü, ekran onarımı")
        _mk_tech("caglar.aksoy", "Çağlar", "Aksoy")
        _mk_tech("ilker.yildiz", "İlker", "Yıldız")
        _mk_tech("ilgaz.kaya", "Ilgaz", "Kaya")
        self._jobs(zehra, 2)
        page = self.client.get(self.url)
        names = [r["tech"].first_name for r in page.context["technicians"]]
        # iş sayısı azdan çoğa; eşitlikte Türkçe alfabe (... ğ h ı i ...): Zehra'nın 2 işi var, sona düşer
        self.assertEqual(names, ["Ayşe", "Çağlar", "Ilgaz", "İlker", "Zehra"])
        self.assertContains(page, "Ayşe Demir — 0 aktif iş · Dizüstü, ekran onarımı")
        self.assertContains(page, "Zehra Polat — 2 aktif iş · Yazıcı")
        self.assertContains(page, "Çağlar Aksoy — 0 aktif iş")

    def test_default_is_technician_with_fewest_open_jobs(self):
        ayse = _mk_tech("ayse.demir", "Ayşe", "Demir")
        burak = _mk_tech("burak.yildiz", "Burak", "Yıldız")
        elif_ = _mk_tech("elif.celik", "Elif", "Çelik")
        self._jobs(ayse, 3)
        self._jobs(burak, 2)
        self._jobs(elif_, 1)
        _mk_device(technician=elif_, status=Device.Status.DELIVERED)  # teslim edilmiş iş yük sayılmaz
        page = self.client.get(self.url)
        self.assertEqual([r["tech"].first_name for r in page.context["technicians"]], ["Elif", "Burak", "Ayşe"])
        self.assertEqual(page.context["selected_id"], elif_.pk)
        self.assertContains(page, f'<option value="{elif_.pk}" data-name="Elif Çelik" selected>')
        self.assertContains(page, "Seçili teknisyen: <b>Elif Çelik</b>")

    def test_default_tie_goes_to_first_in_alphabetical_order(self):
        _mk_tech("zehra.polat", "Zehra", "Polat")
        ayse = _mk_tech("ayse.demir", "Ayşe", "Demir")
        page = self.client.get(self.url)
        self.assertEqual(page.context["selected_id"], ayse.pk)

    def test_default_skips_technicians_on_leave_and_inactive(self):
        from .models import TechnicianLeave
        today = _tz.localdate()
        on_leave = _mk_tech("ayse.demir", "Ayşe", "Demir")
        TechnicianLeave.objects.create(technician=on_leave, leave_type="annual", start_date=today - _td(days=1),
                                       end_date=today + _td(days=2), status=TechnicianLeave.Status.APPROVED)
        inactive = _mk_tech("bora.kurt", "Bora", "Kurt")
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        busy = _mk_tech("cem.acar", "Cem", "Acar")
        self._jobs(busy, 4)
        page = self.client.get(self.url)
        self.assertEqual(page.context["selected_id"], busy.pk)
        names = [r["tech"].first_name for r in page.context["technicians"]]
        self.assertEqual(names, ["Cem", "Ayşe"])  # pasif olan listede yok; izindeki (seçilemez) sona düşer
        self.assertContains(page, "Ayşe Demir — 0 aktif iş · İzinde")
        self.assertContains(page, f'<option value="{on_leave.pk}" data-name="Ayşe Demir" disabled>')

    def test_no_available_technician_disables_assignment(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Şu anda atanabilecek teknisyen yok")
        self.assertIsNone(page.context["selected_id"])
        post = self.client.post(self.url, {"technician": ""})
        self.assertEqual(post.status_code, 200)
        self.device.refresh_from_db()
        self.assertIsNone(self.device.technician)

    def test_assign_moves_job_to_technician_and_notifies_with_note(self):
        from .models import Notification
        tech = _mk_tech()
        Notification.objects.all().delete()
        response = self.client.post(self.url, {"technician": tech.pk, "note": "Müşteri veriyi yedekletmek istemiyor."})
        self.assertRedirects(response, reverse("core:admin_new_devices"), fetch_redirect_response=False)
        self.device.refresh_from_db()
        self.assertEqual(self.device.technician, tech)
        self.assertIsNotNone(self.device.assigned_at)
        note = Notification.objects.get(recipient=tech)
        self.assertEqual(note.title, "Size yeni bir iş atandı")
        self.assertIn(self.device.tracking_id, note.body)
        self.assertIn("Not: Müşteri veriyi yedekletmek istemiyor.", note.body)
        listing = self.client.get(reverse("core:admin_new_devices"))
        self.assertContains(listing, "kaydı Ayşe Demir teknisyenine atandı.")
        self.assertEqual(listing.context["total_new"], 0)  # atanan kayıt «Yeni kayıtlar»dan çıkar
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(tech)
        self.assertContains(self.client.get(reverse("core:technician_dashboard")), self.device.tracking_id)

    def test_assign_without_note_keeps_notification_body_plain(self):
        from .models import Notification
        tech = _mk_tech()
        Notification.objects.all().delete()
        self.client.post(self.url, {"technician": tech.pk, "note": "  "})
        self.assertNotIn("Not:", Notification.objects.get(recipient=tech).body)

    def test_assign_rejects_leave_inactive_bogus_and_non_technician(self):
        from .models import Notification, TechnicianLeave
        today = _tz.localdate()
        on_leave = _mk_tech("ayse.demir", "Ayşe", "Demir")
        TechnicianLeave.objects.create(technician=on_leave, leave_type="annual", start_date=today,
                                       end_date=today + _td(days=1), status=TechnicianLeave.Status.APPROVED)
        inactive = _mk_tech("bora.kurt", "Bora", "Kurt")
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        customer = User.objects.create_user("c@x.com", "c@x.com", "Pass#12345", role=User.Role.CUSTOMER,
                                            first_name="Can", last_name="Er", is_temporary_password=False)
        Notification.objects.all().delete()
        for bad in (on_leave.pk, inactive.pk, customer.pk, 999999, "abc", "", "9" * 40):
            response = self.client.post(self.url, {"technician": bad})
            self.assertEqual(response.status_code, 200, bad)
            self.device.refresh_from_db()
            self.assertIsNone(self.device.technician, bad)
        self.assertFalse(Notification.objects.filter(kind="general", title="Size yeni bir iş atandı").exists())
        self.assertContains(self.client.post(self.url, {"technician": on_leave.pk}), "şu an izinde")

    def test_pre_registered_stays_listed_and_can_be_reassigned(self):
        from .models import Notification
        first, second = _mk_tech("ayse.demir", "Ayşe", "Demir"), _mk_tech("burak.yildiz", "Burak", "Yıldız")
        device = _mk_device(status=Device.Status.IN_TRANSIT, delivery_method="cargo")  # kargoya verilmiş: atama açık
        url = reverse("core:admin_new_device_detail", args=[device.pk])
        self.client.post(url, {"technician": first.pk})
        page = self.client.get(url)  # servise henüz ulaşmamış kayıt listede kalır
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Teknisyen atandı")
        self.assertContains(page, "Teknisyeni değiştir")
        self.assertContains(page, f'<option value="{first.pk}" data-name="Ayşe Demir" selected>')
        self.assertContains(page, "teknisyenine atandı")  # işlem geçmişinde atama satırı
        Notification.objects.all().delete()
        self.client.post(url, {"technician": second.pk})
        device.refresh_from_db()
        self.assertEqual(device.technician, second)
        self.assertEqual(Notification.objects.get(recipient=first).title, "Bir iş sizden alındı")

    def test_previous_records_of_same_customer(self):
        customer = User.objects.create_user("m@example.com", "m@example.com", "Pass#12345", role=User.Role.CUSTOMER,
                                            first_name="Murat", last_name="Koç", is_temporary_password=False)
        device = _mk_device(customer=customer, customer_name="Murat Koç")
        _mk_device(customer=customer, customer_name="Murat Koç", brand="Apple", model="iPhone 12",
                   status=Device.Status.DELIVERED)
        _mk_device(customer_name="Başka Kişi", brand="Canon", model="G3420")
        page = self.client.get(reverse("core:admin_new_device_detail", args=[device.pk]))
        self.assertContains(page, "Apple iPhone 12")
        self.assertContains(page, "Teslim edildi")
        self.assertContains(page, f"MÜŞ-{customer.pk:04d}")
        self.assertNotContains(page, "Canon G3420")

    def test_attachment_chips_link_to_files(self):
        from .models import DeviceAttachment
        att = DeviceAttachment.objects.create(device=self.device, original_name="mavi_ekran.jpg", kind="photo", size=3,
                                              file="device_attachments/test-mavi-ekran.jpg")  # dosya yazılmaz, yalnızca kayıt
        page = self.client.get(self.url)
        self.assertContains(page, f'href="{reverse("core:attachment", args=[att.pk])}"')
        self.assertContains(page, "mavi_ekran.jpg")


class TurkishSortTests(TestCase):
    def test_turkish_alphabet_order(self):
        from .staffing import turkish_sort_key
        names = ["Zehra", "Şule", "İlker", "Ilgaz", "Çağlar", "Ayşe", "Ömer", "Oya", "Ümit", "Umut", "Gül", "Hasan"]
        self.assertEqual(sorted(names, key=turkish_sort_key),
                         ["Ayşe", "Çağlar", "Gül", "Hasan", "Ilgaz", "İlker", "Oya", "Ömer", "Şule", "Umut", "Ümit", "Zehra"])


class PersonNameRuleTests(TestCase):
    """Ad / soyad alanlarına yalnızca harf (ve kelimeler arası boşluk) girilebilir."""

    GOOD = ("Ayşe", "Şükrü", "İsmail", "Ayşe Nur", "Çelik", "Öztürk")
    BAD = ("Ali1", "12345", "Ali_", "Ali-Veli", "O'Neil", "Ali.", "Ali@x", "Ali<b>", "Ali ✓", "Ali\u200b1")

    def _staff(self, first, last):
        from .forms import StaffCreateForm
        return StaffCreateForm({"role": "technician", "first_name": first, "last_name": last, "email": "kisi@example.com"})

    def _register(self, first, last):
        from .forms import CustomerRegisterForm
        return CustomerRegisterForm({"first_name": first, "last_name": last, "email": "yeni@example.com", "phone": "",
                                     "password1": "GucluSifre#2026", "password2": "GucluSifre#2026", "accept_terms": "on"})

    def test_staff_form(self):
        for ok in self.GOOD:
            self.assertTrue(self._staff(ok, "Demir").is_valid(), ok)
            self.assertTrue(self._staff("Ali", ok).is_valid(), ok)
        for bad in self.BAD:
            first, last = self._staff(bad, "Demir"), self._staff("Ali", bad)
            self.assertIn("first_name", first.errors, bad)
            self.assertIn("last_name", last.errors, bad)

    def test_register_form(self):
        for ok in self.GOOD:
            form = self._register(ok, "Demir")
            self.assertNotIn("first_name", form.errors, ok)
        for bad in self.BAD:
            self.assertIn("first_name", self._register(bad, "Demir").errors, bad)
            self.assertIn("last_name", self._register("Ali", bad).errors, bad)
        self.assertIn("yalnızca harflerden", self._register("Ali1", "Demir").errors["first_name"][0])

    def test_extra_spaces_are_collapsed(self):
        form = self._register("  Ayşe    Nur ", " Demir ")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual((form.cleaned_data["first_name"], form.cleaned_data["last_name"]), ("Ayşe Nur", "Demir"))

    def test_register_page_rejects_digits_and_creates_nothing(self):
        response = self.client.post(reverse("core:customer_register"), {
            "first_name": "Ayşe9", "last_name": "Demir", "email": "yeni@example.com", "phone": "",
            "password1": "GucluSifre#2026", "password2": "GucluSifre#2026", "accept_terms": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="yeni@example.com").exists())
        self.assertContains(response, "Ad yalnızca harflerden oluşmalıdır")
        self.assertContains(response, 'data-name-only="1"', count=2)
        self.assertContains(response, "core/js/names.js")

    def test_staff_create_page_rejects_digits(self):
        self.client.force_login(User.objects.get(username="admin"))
        response = self.client.post(reverse("core:staff_create"), {
            "role": "technician", "first_name": "Ali1", "last_name": "Demir", "email": "ali@example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="ali@example.com").exists())
        self.assertContains(response, "Ad yalnızca harflerden oluşmalıdır")
        page = self.client.get(reverse("core:staff_create"))
        self.assertContains(page, 'data-name-only="1"', count=2)
        self.assertContains(page, "core/js/names.js")

    def test_cargo_recipient_name_rule(self):
        from .forms import DeviceRegistrationForm
        base = {"device_type": "laptop", "brand": "ASUS", "model": "Vivobook 15", "issue_title": "Titreme",
                "issue_description": "Ekran titriyor.", "delivery_method": "cargo", "return_phone": "0542 555 12 34",
                "return_city": "İstanbul", "return_district": "Kadıköy", "return_address": "Caferağa Mah. No: 12"}
        bad = DeviceRegistrationForm({**base, "return_recipient": "Ayşe 123"})
        self.assertFalse(bad.is_valid())
        self.assertIn("return_recipient", bad.errors)
        good = DeviceRegistrationForm({**base, "return_recipient": "Ayşe Yılmaz"})
        self.assertTrue(good.is_valid(), good.errors)

    def test_customer_login_uses_staff_sized_fields(self):
        page = self.client.get(reverse("core:customer_login"))
        self.assertContains(page, 'class="cp-ico"', count=2)
        self.assertContains(page, "customer.css?v=4")


class AdminNotificationClickTests(TestCase):
    """Admin bildirimine tıklayınca Django yönetimi değil, Bildirimler paneli açılır; panelde «Aç» bağlantısı yoktur."""

    def setUp(self):
        from .models import Notification
        self.admin = User.objects.get(username="admin")
        self.client.force_login(self.admin)
        self.device = _mk_device()
        Notification.objects.all().delete()
        self.note = Notification.objects.create(recipient=self.admin, title="Yeni cihaz ön kaydı", body="x",
                                                url=reverse("admin:core_device_change", args=[self.device.pk]),
                                                device=self.device)

    def test_click_goes_to_notifications_panel_and_marks_read(self):
        out = self.client.get(reverse("core:notification_open", args=[self.note.pk]))
        self.assertRedirects(out, reverse("core:notifications_list"))
        self.note.refresh_from_db()
        self.assertIsNotNone(self.note.read_at)

    def test_feed_urls_point_through_open_and_panel_has_no_open_links(self):
        feed = self.client.get(reverse("core:notifications_feed")).json()
        self.assertEqual(feed["recent"][0]["url"], reverse("core:notification_open", args=[self.note.pk]))
        page = self.client.get(reverse("core:notifications_list"))
        self.assertContains(page, "Yeni cihaz ön kaydı")
        self.assertNotContains(page, "an-link")




class LandingPageTests(TestCase):
    """Açılış sayfası: ziyaretçiye gösterilir, giriş yapmış kullanıcı rol sayfasına gider."""

    def test_anonymous_sees_landing_page_at_root(self):
        response = self.client.get(reverse("core:home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/landing.html")
        self.assertContains(response, "Cihazınız güvenli ellerde")

    def test_header_links_point_to_right_pages(self):
        page = self.client.get(reverse("core:home")).content.decode()
        self.assertIn(f'<a href="{reverse("core:login")}">Bayi Portal</a>', page)            # personel girişi
        self.assertIn(f'href="{reverse("core:customer_login")}">Giriş Yap</a>', page)          # müşteri girişi
        self.assertIn(f'href="{reverse("core:customer_register")}">Üye Ol</a>', page)          # müşteri üye ol

    def test_section_links_are_in_page_anchors_with_targets(self):
        page = self.client.get(reverse("core:home")).content.decode()
        for anchor in ("hakkimizda", "markalar", "sss", "iletisim"):
            self.assertIn(f'href="#{anchor}"', page)       # aynı sayfada kayar, yeni sayfa açmaz
            self.assertIn(f'id="{anchor}"', page)

    def test_header_is_sticky(self):
        from pathlib import Path
        css = (Path(__file__).parent / "static" / "core" / "css" / "landing.css").read_text(encoding="utf-8")
        self.assertIn(".ld-header{position:sticky;top:0", css)

    def test_logged_in_users_are_redirected_to_their_home(self):
        customer = User.objects.create_user("c@x.com", "c@x.com", "Pass#12345", role=User.Role.CUSTOMER,
                                            is_temporary_password=False)
        self.client.force_login(customer)
        self.assertRedirects(self.client.get(reverse("core:home")), reverse("core:customer_home"),
                             fetch_redirect_response=False)

    def test_login_pages_still_work(self):
        for name in ("core:login", "core:customer_login", "core:customer_register"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)



class CustomerPanelV2Tests(TestCase):
    """Yeni müşteri paneli: ana sayfa bölümleri, Hemen Ara, kullanıcı menüsü, yardım, profil, logo bağlantıları."""

    def setUp(self):
        self.customer = User.objects.create_user("ayse@example.com", "ayse@example.com", "GucluSifre#2026",
                                                 role=User.Role.CUSTOMER, is_temporary_password=False,
                                                 first_name="Ayşe", last_name="Yılmaz", phone="05321234567")
        self.other = User.objects.create_user("veli@example.com", "veli@example.com", "GucluSifre#2026",
                                              role=User.Role.CUSTOMER, is_temporary_password=False)
        self.client.force_login(self.customer)

    def _device(self, owner=None, **extra):
        values = dict(customer=owner or self.customer, customer_name="Ayşe Yılmaz", customer_phone="05321234567",
                      brand="Lenovo", model="ThinkPad E14", issue_description="Açılmıyor")
        values.update(extra)
        return Device.objects.create(**values)

    def test_hemen_ara_opens_dialer_with_number(self):
        for name in ("core:customer_home", "core:customer_help", "core:customer_profile"):
            page = self.client.get(reverse(name))
            from django.conf import settings as dj_settings
            from .context_processors import tel_href
            self.assertContains(page, f'href="tel:{tel_href(dj_settings.SUPPORT_PHONE)}"', msg_prefix=name)
            self.assertContains(page, "Hemen Ara")

    def test_tel_href_normalisation(self):
        from .context_processors import tel_href
        self.assertEqual(tel_href("0533 305 54 24"), "+905333055424")
        self.assertEqual(tel_href("+90 533 305 54 24"), "+905333055424")
        self.assertEqual(tel_href("5333055424"), "+905333055424")
        self.assertEqual(tel_href(""), "")

    def test_home_matches_new_design_sections(self):
        new = self._device(status=Device.Status.AWAITING_CARGO, delivery_method="cargo", model="Vivobook 15")
        current = self._device(status=Device.Status.DIAGNOSING)
        page = self.client.get(reverse("core:customer_home"))
        for text in ("tekrar hoş geldin!", "Toplam Servis Kaydı", "İncelenen Cihaz", "Teslimat Hazır", "Hızlı İşlemler",
                     "Yardıma mı ihtiyacınız var?", "Mevcut servis kayıtlarınız", "Yeni servis kaydınız",
                     "Tümünü Gör", "veri yedeklemesi yapmayı unutmayın"):
            self.assertContains(page, text)
        self.assertContains(page, new.tracking_id)
        self.assertContains(page, current.tracking_id)
        self.assertEqual([d.pk for d in page.context["new_devices"]], [new.pk])
        self.assertEqual([d.pk for d in page.context["current_devices"]], [current.pk])
        self.assertEqual(page.context["total_count"], 2)

    def test_home_hides_delivered_and_excludes_cancelled_from_total(self):
        done = self._device(status=Device.Status.DELIVERED)
        gone = self._device(status=Device.Status.CANCELLED)
        page = self.client.get(reverse("core:customer_home"))
        self.assertNotContains(page, done.tracking_id)
        self.assertNotContains(page, gone.tracking_id)
        self.assertEqual(page.context["total_count"], 1)          # iptal edilen sayılmaz
        # ama «Tümünü Gör» listesinde ikisi de görünür
        listing = self.client.get(reverse("core:customer_devices"))
        self.assertContains(listing, done.tracking_id)
        self.assertContains(listing, gone.tracking_id)

    def test_user_menu_has_profile_and_logout(self):
        page = self.client.get(reverse("core:customer_home"))
        self.assertContains(page, 'id="pt-user-btn"')
        self.assertContains(page, "Ayşe Yılmaz")            # ad soyad kutuda
        self.assertContains(page, f'href="{reverse("core:customer_profile")}"')
        self.assertContains(page, "Profil")
        self.assertContains(page, f'action="{reverse("core:logout")}"')
        self.assertContains(page, "Çıkış Yap")
        self.assertContains(page, 'id="pt-user-pop"')

    def test_sidebar_has_help_and_support_link(self):
        page = self.client.get(reverse("core:customer_home"))
        self.assertContains(page, f'href="{reverse("core:customer_help")}"')
        self.assertContains(page, "Yardım &amp; Destek")

    def test_help_page_content(self):
        page = self.client.get(reverse("core:customer_help"))
        from django.conf import settings as dj_settings
        for text in ("Servis süreci nasıl işler?", "Sık sorulan sorular", "Cihazınızı teslim etmeden önce", "Bize ulaşın",
                     dj_settings.SUPPORT_PHONE, "Kaydı iptal et"):
            self.assertContains(page, text)

    def test_profile_shows_account_info_and_summary(self):
        self._device(status=Device.Status.DIAGNOSING)
        self._device(status=Device.Status.DELIVERED)
        page = self.client.get(reverse("core:customer_profile"))
        for text in ("Ayşe", "Yılmaz", "ayse@example.com", "(532) 123 45 67", "Müşteri", "Aktif", "Üyelik tarihi", "Son giriş"):
            self.assertContains(page, text)
        self.assertEqual((page.context["total_count"], page.context["active_count"], page.context["delivered_count"]), (2, 1, 1))

    def test_profile_and_help_closed_to_staff_and_anonymous(self):
        for name in ("core:customer_profile", "core:customer_help", "core:customer_devices"):
            self.client.logout()
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)
        admin = User.objects.create_user("adm", "adm@example.com", "Pass#12345", role=User.Role.ADMIN,
                                         is_temporary_password=False)
        self.client.force_login(admin)
        for name in ("core:customer_profile", "core:customer_help", "core:customer_devices"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_search_finds_only_own_devices(self):
        mine = self._device(model="Vivobook 15")
        self._device(owner=self.other, model="Vivobook Gizli")
        by_model = self.client.get(reverse("core:customer_devices"), {"q": "vivobook"})
        self.assertContains(by_model, mine.tracking_id)
        self.assertNotContains(by_model, "Gizli")
        by_code = self.client.get(reverse("core:customer_devices"), {"q": mine.tracking_id})
        self.assertContains(by_code, mine.tracking_id)
        none = self.client.get(reverse("core:customer_devices"), {"q": "yokboyle"})
        self.assertContains(none, "eşleşen bir kayıt bulunamadı")

    def test_cancel_button_still_on_cards(self):
        device = self._device(status=Device.Status.PRE_REGISTERED)
        page = self.client.get(reverse("core:customer_home"))
        self.assertContains(page, reverse("core:customer_device_cancel", args=[device.tracking_id]))
        self.assertContains(page, "Kaydı iptal et")

    def test_logos_link_to_landing_page_everywhere(self):
        landing = reverse("core:landing")
        pages = {
            "login": self.client.get(reverse("core:login")),
            "customer_login": self.client.get(reverse("core:customer_login")),
            "customer_register": self.client.get(reverse("core:customer_register")),
            "customer_panel": self.client.get(reverse("core:customer_home")),
        }
        admin = User.objects.create_user("adm", "adm@example.com", "Pass#12345", role=User.Role.ADMIN,
                                         is_temporary_password=False)
        self.client.force_login(admin)
        pages["admin_panel"] = self.client.get(reverse("core:admin_dashboard"))
        for name, response in pages.items():
            self.assertEqual(response.status_code, 200, name)
            html = response.content.decode()
            logo = html[:html.index("logo.png")]
            self.assertIn(f'href="{landing}"', logo[logo.rindex("<a "):], name)

    def test_landing_reachable_when_logged_in_and_offers_panel(self):
        page = self.client.get(reverse("core:landing"))
        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, "core/landing.html")
        self.assertContains(page, "Panelim")
        self.assertNotContains(page, ">Üye Ol<")
        self.client.logout()
        anon = self.client.get(reverse("core:landing"))
        self.assertContains(anon, ">Üye Ol<")
        self.assertNotContains(anon, "Panelim")

    def test_top_search_bar_removed(self):
        page = self.client.get(reverse("core:customer_home"))
        self.assertNotContains(page, 'class="pt-search"')
        self.assertNotContains(page, "Cihaz, takip kodu veya işlem ara")
        self.assertContains(page, 'id="pt-theme"')            # sağdaki zil ve tema düğmesi yerinde

    # --- profil düzenleme + adres ---
    def _profile_post(self, **over):
        data = {"first_name": "Ayşe", "last_name": "Yılmaz", "phone": "05321234567", "city": "", "district": "", "address": ""}
        data.update(over)
        return self.client.post(reverse("core:customer_profile"), data)

    def test_profile_page_has_editable_form_with_optional_address(self):
        page = self.client.get(reverse("core:customer_profile"))
        for text in ('name="first_name"', 'name="last_name"', 'name="phone"', 'name="city"', 'name="district"',
                     'name="address"', "Değişiklikleri Kaydet", "İsteğe bağlı", "Giriş kimliğiniz olduğu için değiştirilemez"):
            self.assertContains(page, text)
        self.assertNotContains(page, 'name="email"')           # e-posta düzenlenemez
        self.assertNotRegex(page.content.decode(), r'name="(city|district|address)"[^>]*required')

    def test_customer_updates_profile_and_address(self):
        response = self._profile_post(first_name="Ayşenur", last_name="Demir", phone="0533 111 22 33",
                                      city="Düzce", district="Merkez", address="Cumhuriyet Mah. Gül Sk. No:5 D:3")
        self.assertRedirects(response, reverse("core:customer_profile"))
        self.customer.refresh_from_db()
        self.assertEqual((self.customer.first_name, self.customer.last_name, self.customer.phone),
                         ("Ayşenur", "Demir", "(533) 111 22 33"))
        self.assertEqual((self.customer.city, self.customer.district, self.customer.address),
                         ("Düzce", "Merkez", "Cumhuriyet Mah. Gül Sk. No:5 D:3"))
        self.assertEqual(self.customer.email, "ayse@example.com")
        page = self.client.get(reverse("core:customer_profile"))
        self.assertContains(page, "Profil bilgileriniz güncellendi.")
        self.assertContains(page, "Cumhuriyet Mah.")
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.UPDATE, user=self.customer).exists())

    def test_address_is_not_required(self):
        self.customer.city, self.customer.address = "Düzce", "Eski adres"
        self.customer.save()
        self.assertRedirects(self._profile_post(), reverse("core:customer_profile"))      # adres alanları boş
        self.customer.refresh_from_db()
        self.assertEqual((self.customer.city, self.customer.district, self.customer.address), ("", "", ""))

    def test_profile_validation_and_email_cannot_change(self):
        for bad in ({"first_name": ""}, {"last_name": "Yılmaz9"}, {"phone": "abc"}):
            response = self._profile_post(**bad)
            self.assertEqual(response.status_code, 200, bad)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.first_name, "Ayşe")
        self._profile_post(email="hacker@example.com")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.email, "ayse@example.com")
        self.assertEqual(self.customer.username, "ayse@example.com")

    def test_profile_edit_only_affects_own_account(self):
        self._profile_post(first_name="Değişti")
        self.other.refresh_from_db()
        self.assertNotEqual(self.other.first_name, "Değişti")

    def test_cargo_address_prefilled_from_profile(self):
        self.customer.city, self.customer.district = "Düzce", "Merkez"
        self.customer.address = "Cumhuriyet Mah. Gül Sk. No:5 D:3"
        self.customer.save()
        page = self.client.get(reverse("core:customer_device_create"))
        self.assertContains(page, 'name="return_city"')
        self.assertContains(page, 'value="Düzce"')
        self.assertContains(page, 'value="Merkez"')
        self.assertContains(page, "Cumhuriyet Mah. Gül Sk. No:5 D:3")
        self.assertContains(page, 'value="Ayşe Yılmaz"')            # alıcı adı
        self.assertContains(page, 'value="(532) 123 45 67"')        # alıcı telefonu (yeni biçimde)

    def test_cargo_address_empty_when_profile_has_none_and_can_be_edited(self):
        page = self.client.get(reverse("core:customer_device_create"))
        self.assertNotContains(page, "Cumhuriyet")
        self.assertContains(page, '<input type="text" id="f-rc" name="return_city" maxlength="60" value="">')

    def test_theme_toggle_present(self):
        page = self.client.get(reverse("core:customer_home"))
        self.assertContains(page, 'id="pt-theme"')
        self.assertContains(page, "fx-theme")


from .forms import CustomerRegisterForm, DeviceRegistrationForm  # noqa: E402


class PhoneFormatTests(TestCase):
    """Telefon numaraları nasıl girilirse girilsin «(533) 305 54 24» biçiminde kaydedilir."""

    GOOD = {
        "0533 305 54 24": "(533) 305 54 24", "(533) 305 54 24": "(533) 305 54 24", "5333055424": "(533) 305 54 24",
        "05333055424": "(533) 305 54 24", "+90 533 305 54 24": "(533) 305 54 24", "+90 (533) 305-54-24": "(533) 305 54 24",
        "0090 533 305 54 24": "(533) 305 54 24", "905333055424": "(533) 305 54 24", "0 533 305 54 24": "(533) 305 54 24",
        "0212 345 67 89": "(212) 345 67 89",
    }
    BAD = ("0533", "abc", "533 305 54 2", "0533 305 54 245", "1234567890123")

    def test_normalize_phone(self):
        from .phone import normalize_phone, pretty_phone
        for raw, expected in self.GOOD.items():
            self.assertEqual(normalize_phone(raw), expected, raw)
        for raw in self.BAD:
            self.assertIsNone(normalize_phone(raw), raw)
        self.assertEqual(pretty_phone("05321234567"), "(532) 123 45 67")
        self.assertEqual(pretty_phone("eski-kayit"), "eski-kayit")       # çevrilemeyen eski kayıt bozulmaz
        self.assertEqual(pretty_phone(""), "")

    def test_register_form_formats_phone(self):
        for raw, expected in self.GOOD.items():
            form = CustomerRegisterForm({"first_name": "Ali", "last_name": "Demir", "email": "yeni@example.com",
                                         "phone": raw, "password1": "GucluSifre#2026", "password2": "GucluSifre#2026",
                                         "accept_terms": "on"})
            self.assertTrue(form.is_valid(), (raw, form.errors))
            self.assertEqual(form.cleaned_data["phone"], expected, raw)
        form = CustomerRegisterForm({"first_name": "Ali", "last_name": "Demir", "email": "yeni@example.com", "phone": "12345",
                                     "password1": "GucluSifre#2026", "password2": "GucluSifre#2026", "accept_terms": "on"})
        self.assertIn("phone", form.errors)
        self.assertIn("(533) 305 54 24", form.errors["phone"][0])
        empty = CustomerRegisterForm({"first_name": "Ali", "last_name": "Demir", "email": "yeni@example.com", "phone": "",
                                      "password1": "GucluSifre#2026", "password2": "GucluSifre#2026", "accept_terms": "on"})
        self.assertTrue(empty.is_valid(), empty.errors)                 # telefon isteğe bağlı
        self.assertEqual(empty.cleaned_data["phone"], "")

    def test_registration_stores_formatted_phone(self):
        self.client.post(reverse("core:customer_register"), {
            "first_name": "Ali", "last_name": "Demir", "email": "ali@example.com", "phone": "+90 533 305 5424",
            "password1": "GucluSifre#2026", "password2": "GucluSifre#2026", "accept_terms": "on"})
        self.assertEqual(User.objects.get(email="ali@example.com").phone, "(533) 305 54 24")

    def test_profile_and_device_forms_format_phone(self):
        customer = User.objects.create_user("c@x.com", "c@x.com", "Pass#12345", role=User.Role.CUSTOMER,
                                            is_temporary_password=False, first_name="Ali", last_name="Demir")
        self.client.force_login(customer)
        self.client.post(reverse("core:customer_profile"), {"first_name": "Ali", "last_name": "Demir", "phone": "05333055424",
                                                            "city": "", "district": "", "address": ""})
        customer.refresh_from_db()
        self.assertEqual(customer.phone, "(533) 305 54 24")
        page = self.client.get(reverse("core:customer_profile"))
        self.assertContains(page, 'value="(533) 305 54 24"')
        self.assertContains(page, "core/js/phone.js")
        bad = self.client.post(reverse("core:customer_profile"), {"first_name": "Ali", "last_name": "Demir", "phone": "123",
                                                                  "city": "", "district": "", "address": ""})
        self.assertEqual(bad.status_code, 200)
        self.assertContains(bad, "Geçerli bir telefon numarası girin")
        form = DeviceRegistrationForm({"device_type": "phone", "brand": "Apple", "model": "iPhone", "issue_title": "x",
                                       "issue_description": "y", "delivery_method": "cargo", "contact_phone": "0533 305 54 24",
                                       "return_recipient": "Ali Demir", "return_phone": "5333055424", "return_city": "Düzce",
                                       "return_district": "Merkez", "return_address": "Gül Sk."})
        form.is_valid()
        self.assertEqual(form.cleaned_data.get("contact_phone"), "(533) 305 54 24")
        self.assertEqual(form.cleaned_data.get("return_phone"), "(533) 305 54 24")

    def test_staff_create_formats_phone(self):
        admin = User.objects.create_user("adm", "adm@example.com", "Pass#12345", role=User.Role.ADMIN,
                                         is_temporary_password=False)
        self.client.force_login(admin)
        self.client.post(reverse("core:staff_create"), {"role": "technician", "first_name": "Ali", "last_name": "Demir",
                                                        "email": "ali@example.com", "phone": "0 533 305 54 24",
                                                        "specialization": ""})
        self.assertEqual(User.objects.get(email="ali@example.com").phone, "(533) 305 54 24")
        bad = self.client.post(reverse("core:staff_create"), {"role": "technician", "first_name": "Veli", "last_name": "Kaya",
                                                              "email": "veli@example.com", "phone": "123", "specialization": ""})
        self.assertEqual(bad.status_code, 200)
        self.assertFalse(User.objects.filter(email="veli@example.com").exists())
        self.assertContains(self.client.get(reverse("core:staff_create")), "core/js/phone.js")

    def test_register_page_loads_phone_script(self):
        page = self.client.get(reverse("core:customer_register"))
        self.assertContains(page, "core/js/phone.js")
        self.assertContains(page, "(5XX) XXX XX XX")


class PaymentReceiptEmailTests(TestCase):
    """Ödeme alındığında («Ödendi» işaretlenince) müşteriye ödeme dökümü e-postayla gider."""

    def setUp(self):
        from decimal import Decimal
        from .models import DevicePartUsage, StockPart
        self.customer = User.objects.create_user("ayse@example.com", "ayse@example.com", "GucluSifre#2026",
                                                 role=User.Role.CUSTOMER, is_temporary_password=False,
                                                 first_name="Ayşe", last_name="Yılmaz")
        self.device = Device.objects.create(customer=self.customer, customer_name="Ayşe Yılmaz", brand="Lenovo",
                                            model="ThinkPad E14", issue_title="Ekran gelmiyor", issue_description="x",
                                            status=Device.Status.READY, labor_cost=Decimal("500.00"),
                                            estimated_cost=Decimal("1000.00"))
        part = StockPart.objects.create(name="Ekran paneli 14 inç", code="EKR-14", quantity=5,
                                        unit_price=Decimal("375.00"))
        DevicePartUsage.objects.create(device=self.device, part=part, quantity=2)
        self.device.recalculate_parts_cost()
        self.device.refresh_from_db()
        mail.outbox.clear()

    def _pay(self, device=None):
        device = device or self.device
        device.is_paid = True
        with self.captureOnCommitCallbacks(execute=True):
            device.save()

    def test_receipt_sent_to_customer_when_payment_received(self):
        self._pay()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["ayse@example.com"])
        self.assertIn(self.device.tracking_id, message.subject)
        body = message.body
        for text in (self.device.tracking_id, "ThinkPad E14", "Ödendi", "İşçilik", "Ekran paneli 14 inç",
                     "2 adet", "TOPLAM ÖDENEN", "1.250,00 ₺"):
            self.assertIn(text, body)
        self.assertIn("500,00 ₺", body)           # işçilik
        self.assertIn("750,00 ₺", body)           # 2 × 375 parça
        self.assertIn(self.device.tracking_id, message.alternatives[0][0])

    def test_receipt_not_sent_without_payment_or_on_other_changes(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.device.status = Device.Status.DELIVERED
            self.device.save()
        self.assertEqual(len(mail.outbox), 0)
        self._pay()
        self.assertEqual(len(mail.outbox), 1)
        with self.captureOnCommitCallbacks(execute=True):    # zaten ödenmiş kayıt tekrar kaydedilince yeniden gitmez
            self.device.note = "güncelleme"
            self.device.save()
        self.assertEqual(len(mail.outbox), 1)

    def test_creating_an_already_paid_record_does_not_send_receipt(self):
        with self.captureOnCommitCallbacks(execute=True):
            Device.objects.create(customer=self.customer, customer_name="Ayşe", brand="HP", model="X",
                                  issue_description="x", is_paid=True)
        self.assertEqual([m.subject for m in mail.outbox if "ödeme" in m.subject.lower()], [])

    def test_receipt_falls_back_to_estimate_without_itemised_costs(self):
        from decimal import Decimal
        device = Device.objects.create(customer=self.customer, customer_name="Ayşe", brand="HP", model="Pavilion",
                                       issue_description="x", status=Device.Status.READY, estimated_cost=Decimal("850.00"))
        mail.outbox.clear()
        self._pay(device)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Servis ücreti", mail.outbox[0].body)
        self.assertIn("850,00 ₺", mail.outbox[0].body)

    def test_walk_in_customer_uses_record_email_and_no_address_means_no_mail(self):
        walk_in = Device.objects.create(customer_name="Ziyaretçi", customer_email="walkin@example.com", brand="HP",
                                        model="X", issue_description="x")
        nameless = Device.objects.create(customer_name="Mailsiz", brand="HP", model="Y", issue_description="x")
        mail.outbox.clear()
        self._pay(walk_in)
        self._pay(nameless)
        self.assertEqual([m.to for m in mail.outbox], [["walkin@example.com"]])

    def test_email_failure_does_not_break_saving_payment(self):
        with mock.patch("core.utils.send_mail", side_effect=OSError("smtp down")):
            self._pay()
        self.device.refresh_from_db()
        self.assertTrue(self.device.is_paid)



class CargoFlowTests(TestCase):
    """Kargo akışı: müşteri «Kod oluştur» -> teknisyen «Kargo teslim alındı» -> işleme başla -> geri kargo kodu
    -> müşteriye bildirim + e-posta -> müşteri «Ürünü teslim aldım» -> admin + teknisyene bildirim."""

    def setUp(self):
        self.admin = User.objects.create_user("adm", "adm@example.com", "Pass#12345", role=User.Role.ADMIN,
                                              is_temporary_password=False, first_name="Ada", last_name="Min")
        self.tech = _mk_tech("tek.nisyen", "Tek", "Nisyen")
        self.other_tech = _mk_tech("baska.tek", "Başka", "Tek")
        self.customer = User.objects.create_user("ayse@example.com", "ayse@example.com", "Pass#12345",
                                                 role=User.Role.CUSTOMER, is_temporary_password=False,
                                                 first_name="Ayşe", last_name="Yılmaz")
        self.other = User.objects.create_user("veli@example.com", "veli@example.com", "Pass#12345",
                                              role=User.Role.CUSTOMER, is_temporary_password=False)
        self.device = _mk_device(customer=self.customer, customer_name="Ayşe Yılmaz", delivery_method="cargo",
                                 status=Device.Status.AWAITING_CARGO, technician=self.tech, brand="Lenovo", model="E14",
                                 return_recipient="Ayşe Yılmaz", return_city="Düzce", return_district="Merkez",
                                 return_address="Gül Sk. No:5")
        mail.outbox.clear()
        Notification.objects.all().delete()

    def _url(self, name, device=None, **kw):
        return reverse(f"core:{name}", args=[(device or self.device).tracking_id], **kw)

    def _as(self, user):
        self.client.logout()
        self.client.force_login(user)

    def _create_code(self):
        self._as(self.customer)
        return self.client.post(self._url("customer_cargo_code"))

    # --- müşteri: kod oluştur ---
    def test_cargo_page_shows_create_code_with_red_warning(self):
        self._as(self.customer)
        page = self.client.get(self._url("customer_cargo"))
        self.assertContains(page, "Kod oluştur")
        self.assertContains(page, "Kargo şirketine verileceği zaman oluşturulmalıdır.")
        self.assertContains(page, 'class="pt-warn"')
        self.assertNotContains(page, "Kod oluştu, teslim sağlanmadı")           # kod yokken görünmez

    def test_create_code_marks_in_transit_and_notifies_admin_and_technician(self):
        response = self._create_code()
        self.assertRedirects(response, self._url("customer_cargo"), fetch_redirect_response=False)
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, Device.Status.IN_TRANSIT)
        self.assertRegex(self.device.cargo_code, r"^KRG-[A-HJKMNP-Z2-9]{8}$")
        self.assertIsNotNone(self.device.cargo_code_created_at)
        for user in (self.admin, self.tech):
            note = Notification.objects.get(recipient=user, title="Cihaz kargoya verildi")
            self.assertIn(self.device.cargo_code, note.body)
        self.assertFalse(Notification.objects.filter(recipient=self.other_tech).exists())     # atanmamış teknisyen almaz
        self.assertFalse(Notification.objects.filter(recipient=self.customer).exists())
        tech_note = Notification.objects.get(recipient=self.tech)
        self.assertEqual(tech_note.url, reverse("core:technician_device", args=[self.device.tracking_id]))
        page = self.client.get(self._url("customer_cargo"))
        self.assertContains(page, self.device.cargo_code)
        self.assertContains(page, "Kod oluştu, teslim sağlanmadı")
        self.assertNotContains(page, "Kod oluştur<")                              # düğme artık yok
        self.assertFalse(self.device.customer_can_cancel)                          # kargoya verilen kayıt iptal edilemez

    def test_unassigned_device_notifies_only_admins(self):
        self.device.technician = None
        self.device.save()
        Notification.objects.all().delete()
        self._create_code()
        self.assertEqual(sorted(Notification.objects.values_list("recipient__username", flat=True)), ["adm"])

    def test_code_creation_rules(self):
        self._create_code()
        first = Device.objects.get(pk=self.device.pk).cargo_code
        self.client.post(self._url("customer_cargo_code"))                          # ikinci kez: kod değişmez
        self.assertEqual(Device.objects.get(pk=self.device.pk).cargo_code, first)
        # kendi teslim kaydı için kod oluşturulamaz
        selfd = _mk_device(customer=self.customer, delivery_method="self", status=Device.Status.PRE_REGISTERED)
        self.client.post(self._url("customer_cargo_code", selfd))
        self.assertEqual(Device.objects.get(pk=selfd.pk).cargo_code, "")
        # başkasının kaydı / GET / personel
        self._as(self.other)
        self.assertEqual(self.client.post(self._url("customer_cargo_code")).status_code, 404)
        self._as(self.customer)
        self.assertEqual(self.client.get(self._url("customer_cargo_code")).status_code, 405)
        self._as(self.tech)
        self.assertEqual(self.client.post(self._url("customer_cargo_code")).status_code, 403)

    # --- müşteri: teslim sağlanmadı ---
    def test_handover_failed_notifies_staff_resets_record_and_allows_new_code(self):
        self._create_code()
        code = Device.objects.get(pk=self.device.pk).cargo_code
        Notification.objects.all().delete()
        response = self.client.post(self._url("customer_cargo_handover_failed"))
        self.assertRedirects(response, self._url("customer_cargo"), fetch_redirect_response=False)
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, Device.Status.AWAITING_CARGO)
        self.assertEqual(self.device.cargo_code, "")
        for user in (self.admin, self.tech):
            note = Notification.objects.get(recipient=user, title="Kargo teslimi gerçekleşmedi")
            self.assertIn(code, note.body)
        self.assertTrue(self.device.status_history.filter(note__contains="teslim sağlanmadı").exists())
        self.assertTrue(self.device.customer_can_cancel)                           # kargoya verilmediği için iptal yeniden açık
        page = self.client.get(self._url("customer_cargo"))
        self.assertContains(page, "Kod oluştur")
        self._create_code()                                                         # yeni kod oluşturulabilir
        self.assertNotEqual(Device.objects.get(pk=self.device.pk).cargo_code, code)

    def test_handover_failed_not_possible_without_code_or_after_receipt(self):
        self._as(self.customer)
        self.client.post(self._url("customer_cargo_handover_failed"))               # kod yok
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.AWAITING_CARGO)
        self.assertEqual(Notification.objects.count(), 0)
        self._create_code()
        self._as(self.tech)
        self.client.post(self._url("technician_device_action"), {"action": "receive"})
        self._as(self.customer)
        self.client.post(self._url("customer_cargo_handover_failed"))               # servis teslim aldıktan sonra olmaz
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.RECEIVED)
        self.assertNotContains(self.client.get(self._url("customer_cargo")), "Kod oluştu, teslim sağlanmadı")

    # --- teknisyen ---
    def test_technician_page_and_permissions(self):
        self._as(self.tech)
        page = self.client.get(self._url("technician_device"))
        self.assertContains(page, self.device.tracking_id)
        self.assertContains(page, "Müşterinin kargo kodunu oluşturması bekleniyor")
        self._as(self.other_tech)
        self.assertEqual(self.client.get(self._url("technician_device")).status_code, 404)    # başkasının işi
        self.assertEqual(self.client.post(self._url("technician_device_action"), {"action": "receive"}).status_code, 404)
        self._as(self.customer)
        self.assertEqual(self.client.get(self._url("technician_device")).status_code, 403)
        self._as(self.admin)
        self.assertEqual(self.client.get(self._url("technician_device")).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self._url("technician_device")).status_code, 302)

    def test_technician_marks_cargo_received_then_starts_work(self):
        self._create_code()
        self._as(self.tech)
        page = self.client.get(self._url("technician_device"))
        self.assertContains(page, "Kargo teslim alındı")
        self.assertNotContains(page, "İşleme başla")                                # önce kargo teslim alınmalı
        # kargo teslim alınmadan işleme başlanamaz
        self.client.post(self._url("technician_device_action"), {"action": "start"})
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.IN_TRANSIT)
        Notification.objects.all().delete()
        self.client.post(self._url("technician_device_action"), {"action": "receive"})
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, Device.Status.RECEIVED)
        self.assertTrue(Notification.objects.filter(recipient=self.customer, title="Cihazınız servise ulaştı").exists())
        page = self.client.get(self._url("technician_device"))
        self.assertContains(page, "İşleme başla")
        self.client.post(self._url("technician_device_action"), {"action": "start"})
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.DIAGNOSING)

    def test_technician_status_pipeline_rules(self):
        self._create_code()
        self._as(self.tech)
        self.client.post(self._url("technician_device_action"), {"action": "status", "status": "repairing"})
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.IN_TRANSIT)    # kargo adımı atlanamaz
        self.client.post(self._url("technician_device_action"), {"action": "receive"})
        self.client.post(self._url("technician_device_action"), {"action": "start"})
        self.client.post(self._url("technician_device_action"), {"action": "status", "status": "repairing"})
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.REPAIRING)
        self.client.post(self._url("technician_device_action"), {"action": "status", "status": "delivered"})   # geçersiz hedef
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.REPAIRING)
        self.client.post(self._url("technician_device_action"), {"action": "bilinmeyen"})
        self.assertEqual(self.client.get(self._url("technician_device")).status_code, 200)

    def _to_ready(self):
        self._create_code()
        self._as(self.tech)
        for payload in ({"action": "receive"}, {"action": "start"}, {"action": "status", "status": "ready"}):
            self.client.post(self._url("technician_device_action"), payload)
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.READY)
        mail.outbox.clear()
        Notification.objects.all().delete()

    # --- teknisyen: geri kargo kodu ---
    def test_return_code_notifies_customer_and_sends_email_with_confirm_button(self):
        self._to_ready()
        page = self.client.get(self._url("technician_device"))
        self.assertContains(page, "Geri kargo kodu oluştur")
        self.assertContains(page, "Kargo şirketine verileceği zaman oluşturulmalıdır.")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self._url("technician_device_action"),
                             {"action": "return_code", "cargo_company": "PTT Kargo", "cargo_tracking_code": "PTT-99"})
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, Device.Status.RETURN_SHIPPING)
        self.assertRegex(self.device.return_cargo_code, r"^GKR-[A-HJKMNP-Z2-9]{8}$")
        self.assertEqual((self.device.return_cargo_company, self.device.return_cargo_tracking_code), ("PTT Kargo", "PTT-99"))
        note = Notification.objects.get(recipient=self.customer)
        self.assertEqual(note.title, "Cihazınız geri kargoya verildi")
        self.assertEqual(note.url, reverse("core:customer_cargo", args=[self.device.tracking_id]))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["ayse@example.com"])
        self.assertIn(self.device.return_cargo_code, message.body)
        self.assertIn("Ürünü teslim aldım", message.alternatives[0][0])
        link = re.search(r"https?://\S+/teslim-aldim/\S+/", message.body).group(0)
        self.assertIn("/teslim-aldim/", link)

    def test_return_code_rules(self):
        self._create_code()
        self._as(self.tech)
        self.client.post(self._url("technician_device_action"), {"action": "return_code"})      # hazır değil
        self.assertEqual(Device.objects.get(pk=self.device.pk).return_cargo_code, "")
        selfd = _mk_device(delivery_method="self", status=Device.Status.READY, technician=self.tech)
        self.client.post(self._url("technician_device_action", selfd), {"action": "return_code"})   # kargo kaydı değil
        self.assertEqual(Device.objects.get(pk=selfd.pk).status, Device.Status.READY)
        self.assertNotContains(self.client.get(self._url("technician_device", selfd)), "Geri kargo kodu oluştur")
        self._to_ready()
        self.client.post(self._url("technician_device_action"), {"action": "return_code", "cargo_company": "Sahte Kargo"})
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.READY)     # geçersiz firma

    # --- müşteri: ürünü teslim aldım ---
    def _ship_back(self):
        self._to_ready()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self._url("technician_device_action"), {"action": "return_code"})
        Notification.objects.all().delete()
        return re.search(r"/teslim-aldim/\S+?/(?=\s|$)", mail.outbox[-1].body).group(0)

    def test_email_link_needs_confirmation_click_then_notifies_staff(self):
        path = self._ship_back()
        self.client.logout()
        opened = self.client.get(path)                                              # bağlantıyı açmak tek başına işlem yapmaz
        self.assertEqual(opened.status_code, 200)
        self.assertContains(opened, "Ürünü teslim aldım")
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.RETURN_SHIPPING)
        self.assertEqual(Notification.objects.count(), 0)
        done = self.client.post(path)
        self.assertEqual(done.status_code, 200)
        self.assertContains(done, "Teşekkürler")
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, Device.Status.DELIVERED)
        self.assertIsNotNone(self.device.return_received_at)
        for user in (self.admin, self.tech):
            note = Notification.objects.get(recipient=user, title="Ürün müşteriye teslim edildi")
            self.assertIn("teslim aldığını onayladı", note.body)
        self.assertFalse(Notification.objects.filter(recipient=self.customer).exists())
        again = self.client.get(path)                                               # tekrar açılırsa «zaten onaylandı»
        self.assertContains(again, "Teslim zaten onaylandı")
        self.client.post(path)
        self.assertEqual(Notification.objects.filter(title="Ürün müşteriye teslim edildi").count(), 2)   # ikinci bildirim yok

    def test_receipt_link_security(self):
        path = self._ship_back()
        self.client.logout()
        self.assertEqual(self.client.get(path[:-3] + "xx/").status_code, 404)        # bozuk imza
        self.assertEqual(self.client.get("/teslim-aldim/uydurma-kod/").status_code, 404)
        from .cargo import RECEIPT_SALT
        from django.core import signing
        stale = signing.dumps({"d": self.device.pk, "c": "GKR-ESKIKODU"}, salt=RECEIPT_SALT)
        self.assertEqual(self.client.get(reverse("core:receipt_confirm", args=[stale])).status_code, 404)   # kod değişmiş
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.RETURN_SHIPPING)

    def test_customer_confirms_receipt_in_app(self):
        self._ship_back()
        self._as(self.customer)
        page = self.client.get(self._url("customer_cargo"))
        self.assertContains(page, "Ürünü teslim aldım")
        self.assertContains(page, self.device.return_cargo_code)
        self._as(self.other)
        self.assertEqual(self.client.post(self._url("customer_receipt_confirm")).status_code, 404)
        self._as(self.customer)
        self.client.post(self._url("customer_receipt_confirm"))
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.DELIVERED)
        self.assertEqual(Notification.objects.filter(title="Ürün müşteriye teslim edildi").count(), 2)
        self.assertNotContains(self.client.get(self._url("customer_cargo")), "Ürünü teslim aldım")

    def test_receipt_not_possible_before_return_shipping(self):
        from .cargo import RECEIPT_SALT
        from django.core import signing
        token = signing.dumps({"d": self.device.pk, "c": ""}, salt=RECEIPT_SALT)
        self.client.logout()
        response = self.client.post(reverse("core:receipt_confirm", args=[token]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "şu anda yapılamıyor")
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.AWAITING_CARGO)

    def test_return_email_failure_does_not_break_status_change(self):
        self._to_ready()
        with mock.patch("core.utils.send_mail", side_effect=OSError("smtp down")):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(self._url("technician_device_action"), {"action": "return_code"})
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.RETURN_SHIPPING)

    def test_old_manual_tracking_form_still_works_and_notifies_technician_too(self):
        self._as(self.customer)
        self.client.post(self._url("customer_cargo"), {"cargo_company": "PTT Kargo", "cargo_tracking_code": "PTT-1"})
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, Device.Status.IN_TRANSIT)
        self.assertTrue(Notification.objects.filter(recipient=self.admin, title="Cihaz kargoya verildi").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.tech, title="Cihaz kargoya verildi").exists())



class InPersonReceiptTests(TestCase):
    """Elden teslim: admin cihazın teslim alındığı bilgisini girer."""

    def setUp(self):
        self.admin = User.objects.get(username="admin")
        self.tech = _mk_tech("tek.nisyen", "Tek", "Nisyen")
        self.customer = User.objects.create_user("ayse@example.com", "ayse@example.com", "Pass#12345",
                                                 role=User.Role.CUSTOMER, is_temporary_password=False)
        self.device = _mk_device(customer=self.customer, customer_name="Ayşe Yılmaz", delivery_method="self",
                                 status=Device.Status.PRE_REGISTERED, brand="Lenovo", model="E14")
        Notification.objects.all().delete()
        self.client.force_login(self.admin)
        self.url = reverse("core:admin_new_device_detail", args=[self.device.pk])

    def test_receive_card_only_for_in_person_pre_registered(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Cihaz elden teslim alındı")
        self.assertContains(page, "Teslim alındı olarak işaretle")
        cargo = _mk_device(delivery_method="cargo", status=Device.Status.AWAITING_CARGO)
        page = self.client.get(reverse("core:admin_new_device_detail", args=[cargo.pk]))
        self.assertNotContains(page, "Cihaz elden teslim alındı")

    def test_admin_records_receipt_with_info(self):
        response = self.client.post(self.url, {"action": "receive", "delivered_by": "Ahmet Yılmaz (eşi)",
                                               "receive_note": "Şarj aleti ve kılıf birlikte alındı."})
        self.assertRedirects(response, self.url)                         # atanmamış: «yeni kayıtlar»da kalır, atama yapılabilir
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, Device.Status.RECEIVED)
        history = self.device.status_history.filter(new_status=Device.Status.RECEIVED).get()
        self.assertEqual(history.changed_by, self.admin)
        for text in ("Elden teslim alındı", "Ahmet Yılmaz (eşi)", "Şarj aleti ve kılıf birlikte alındı."):
            self.assertIn(text, history.note)
        note = Notification.objects.get(recipient=self.customer)
        self.assertEqual(note.title, "Cihazınız servise ulaştı")
        page = self.client.get(self.url)
        self.assertNotContains(page, "Cihaz elden teslim alındı")           # tekrar gösterilmez
        self.assertContains(page, "Şarj aleti ve kılıf birlikte alındı.")    # işlem geçmişinde görünür
        self.assertTrue(AuditLog.objects.filter(description__startswith=f"Elden teslim alındı: {self.device.tracking_id}").exists())

    def test_info_is_optional_and_assignment_still_works_afterwards(self):
        self.client.post(self.url, {"action": "receive"})
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.RECEIVED)
        self.client.post(self.url, {"technician": str(self.tech.pk), "note": ""})
        self.device.refresh_from_db()
        self.assertEqual(self.device.technician, self.tech)
        self.assertEqual(self.device.status, Device.Status.RECEIVED)

    def test_assigned_technician_is_told_and_can_start_work(self):
        self.device.technician = self.tech
        self.device.save()
        Notification.objects.all().delete()
        response = self.client.post(self.url, {"action": "receive", "receive_note": "Ekran çizik"})
        self.assertRedirects(response, reverse("core:admin_new_devices"))        # atanmış kayıt yeni kayıtlardan çıkar
        note = Notification.objects.get(recipient=self.tech)
        self.assertEqual(note.title, "Cihaz teslim alındı")
        self.assertIn("Ekran çizik", note.body)
        self.client.force_login(self.tech)
        url = reverse("core:technician_device_action", args=[self.device.tracking_id])
        self.assertContains(self.client.get(reverse("core:technician_device", args=[self.device.tracking_id])), "İşleme başla")
        self.client.post(url, {"action": "start"})
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.DIAGNOSING)

    def test_rules_cargo_and_already_received_and_permissions(self):
        cargo = _mk_device(delivery_method="cargo", status=Device.Status.AWAITING_CARGO)
        self.client.post(reverse("core:admin_new_device_detail", args=[cargo.pk]), {"action": "receive"})
        self.assertEqual(Device.objects.get(pk=cargo.pk).status, Device.Status.AWAITING_CARGO)
        self.client.post(self.url, {"action": "receive"})
        count = self.device.status_history.count()
        self.client.post(self.url, {"action": "receive"})                      # ikinci kez: değişmez
        self.assertEqual(self.device.status_history.count(), count)
        for user in (self.tech, self.customer):
            self.client.force_login(user)
            self.assertEqual(self.client.post(self.url, {"action": "receive"}).status_code, 403)

    def test_customer_can_no_longer_cancel_after_receipt(self):
        self.client.post(self.url, {"action": "receive"})
        self.client.force_login(self.customer)
        self.client.post(reverse("core:customer_device_cancel", args=[self.device.tracking_id]))
        self.assertEqual(Device.objects.get(pk=self.device.pk).status, Device.Status.RECEIVED)



class StaffThemeToggleTests(TestCase):
    """Açık / koyu tema düğmesi admin ve teknisyen panellerinde de var."""

    def setUp(self):
        self.admin = User.objects.get(username="admin")
        self.tech = _mk_tech("tek.nisyen", "Tek", "Nisyen")
        self.device = _mk_device(technician=self.tech, status=Device.Status.DIAGNOSING)

    def _assert_themed(self, page, name):
        self.assertContains(page, "data-theme-toggle", msg_prefix=name)
        self.assertContains(page, 'class="nt-theme"', msg_prefix=name)               # zil simgesinin yanındaki düğme
        self.assertContains(page, "Koyu temaya geç", msg_prefix=name)
        html = page.content.decode()
        self.assertLess(html.index('id="nt-btn"'), html.index('class="nt-theme"'), name)   # zil solda, ay/tema düğmesi sağında
        self.assertEqual(html.count("data-theme-toggle"), 1, name)
        self.assertContains(page, "core/css/dark.css", msg_prefix=name)
        self.assertContains(page, "core/js/theme.js", msg_prefix=name)
        self.assertContains(page, "localStorage.getItem('fx-theme')", msg_prefix=name)      # sayfa açılırken temayı uygular

    def test_admin_pages_have_theme_toggle(self):
        self.client.force_login(self.admin)
        for name in ("core:admin_dashboard", "core:staff_list", "core:stock_list", "core:admin_new_devices",
                     "core:notifications_list", "core:staff_create"):
            self._assert_themed(self.client.get(reverse(name)), name)

    def test_technician_pages_have_theme_toggle(self):
        self.client.force_login(self.tech)
        self._assert_themed(self.client.get(reverse("core:technician_dashboard")), "technician_dashboard")
        self._assert_themed(self.client.get(reverse("core:technician_device", args=[self.device.tracking_id])), "technician_device")
        self._assert_themed(self.client.get(reverse("core:notifications_list")), "notifications_list")

    def test_dark_stylesheet_is_scoped_and_covers_panels(self):
        from pathlib import Path
        base = Path(__file__).parent / "static" / "core"
        css = (base / "css" / "dark.css").read_text(encoding="utf-8")
        import re
        selectors = [s.strip() for block in re.findall(r"([^{}]+)\{", css) for s in block.split(",") if s.strip()]
        selectors = [s for s in selectors if not s.startswith("/*") and "*/" not in s]
        self.assertTrue(selectors)
        self.assertTrue(all(s.startswith('html[data-theme="dark"]') for s in selectors), [s for s in selectors if not s.startswith('html[data-theme="dark"]')][:3])
        for needle in (".sidebar", ".dash-panel", ".pm-card", ".st-card", ".nd-card", ".ndd-card", ".tech-card", ".td-card", ".an-list", ".nt-theme", ".pill-ready"):
            self.assertIn(needle, css)
        js = (base / "js" / "theme.js").read_text(encoding="utf-8")
        self.assertIn("fx-theme", js)
        self.assertIn(".nt-theme", (base / "css" / "portal.css").read_text(encoding="utf-8"))

    def test_customer_panel_keeps_its_own_toggle(self):
        customer = User.objects.create_user("c@x.com", "c@x.com", "Pass#12345", role=User.Role.CUSTOMER,
                                            is_temporary_password=False)
        self.client.force_login(customer)
        page = self.client.get(reverse("core:customer_home"))
        self.assertContains(page, 'id="pt-theme"')
        self.assertNotContains(page, "core/css/dark.css")
        self.assertNotContains(page, "nt-theme")                      # müşteride çift düğme olmaz
        self.assertEqual(page.content.decode().count("data-theme-toggle"), 0)



# --------------------------------------------------------------------------
# Admin › Cihazlar › Tüm cihazlar · Yeni cihaz kaydı · Kayıt oluşturuldu
# --------------------------------------------------------------------------
from .context_processors import new_devices_filter as _new_devices_filter  # noqa: E402


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", MEDIA_ROOT=_TMP_MEDIA)
class AdminAllDevicesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.get(username="admin")
        self.client.force_login(self.admin)
        self.list_url = reverse("core:admin_device_list")
        self.create_url = reverse("core:admin_device_create")

    def _form(self, **extra):
        data = {"customer_name": "Mehmet Kaya", "customer_phone": "0533 305 54 24", "customer_email": "Mehmet@Mail.com",
                "device_type": "laptop", "brand": "Dell", "model": "Latitude 5420", "serial_number": "",
                "issue_description": "Açılmıyor.", "technician": ""}
        data.update(extra)
        return data

    # --- liste -----------------------------------------------------------------
    def test_list_is_admin_only(self):
        self.client.force_login(_mk_tech("tek.nisyen", "Tek", "Nisyen"))
        self.assertEqual(self.client.get(self.list_url).status_code, 403)
        self.assertEqual(self.client.get(self.create_url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.list_url).status_code, 302)

    def test_list_shows_every_device_with_design_columns(self):
        tech = _mk_tech()
        S = Device.Status
        a = _mk_device(customer_name="Mehmet Kaya", brand="Dell", model="Latitude 5420", technician=tech, status=S.REPAIRING)
        b = _mk_device(customer_name="Selin Arslan", brand="Apple", model="iPhone 12", status=S.READY)
        _mk_device(customer_name="Ali Şahin", brand="Samsung", model="Galaxy S21", status=S.IN_TRANSIT)
        page = self.client.get(self.list_url)
        for text in ("Tüm cihazlar", "Tüm servis kayıtları", "Yeni cihaz kaydı", "Takip kodu", "Müşteri", "Cihaz", "Teknisyen",
                     "Durum", "Tarih", "Detay", "Toplam 3 kayıt", "Tüm durumlar", "Tüm teknisyenler",
                     a.tracking_id, "Mehmet Kaya", "Dell Latitude 5420", "Ayşe Demir", "Tamirde", "Hazır",
                     "Kargoda · Servise geliyor"):
            self.assertContains(page, text)
        self.assertContains(page, f'href="{reverse("core:admin_device_detail", args=[b.pk])}"')
        self.assertContains(page, f'href="{self.create_url}"')
        self.assertNotContains(page, "Teslime hazır</span>")  # listede tasarımdaki kısa etiket kullanılır
        self.assertEqual(len(page.context["devices"]), 3)

    def test_search_status_and_technician_filters(self):
        tech = _mk_tech()
        S = Device.Status
        _mk_device(customer_name="Mehmet Kaya", brand="Dell", model="Latitude 5420", technician=tech, status=S.REPAIRING)
        _mk_device(customer_name="Selin Arslan", brand="Apple", model="iPhone 12", status=S.READY)
        self.assertEqual(len(self.client.get(self.list_url, {"q": "dell latitude"}).context["devices"]), 1)
        self.assertEqual(len(self.client.get(self.list_url, {"q": "selin"}).context["devices"]), 1)
        self.assertEqual(len(self.client.get(self.list_url, {"status": "ready"}).context["devices"]), 1)
        self.assertEqual(len(self.client.get(self.list_url, {"tech": str(tech.pk)}).context["devices"]), 1)
        self.assertEqual(len(self.client.get(self.list_url, {"tech": "none"}).context["devices"]), 1)
        self.assertEqual(len(self.client.get(self.list_url, {"status": "bogus", "tech": "abc"}).context["devices"]), 2)
        self.assertContains(self.client.get(self.list_url, {"q": "yokboyle"}), "Aramanıza uyan cihaz kaydı bulunamadı.")

    def test_pagination_keeps_filters(self):
        for _ in range(23):
            _mk_device(brand="Lenovo")
        page = self.client.get(self.list_url, {"q": "lenovo"})
        self.assertEqual(len(page.context["devices"]), 10)
        self.assertContains(page, "Toplam 23 kayıt")
        self.assertContains(page, "?q=lenovo&amp;page=2")
        last = self.client.get(self.list_url, {"q": "lenovo", "page": 3})
        self.assertEqual(len(last.context["devices"]), 3)

    def test_sidebar_opens_devices_group_and_marks_all_devices(self):
        page = self.client.get(self.list_url)
        self.assertContains(page, f'href="{self.list_url}" aria-current="page"')
        self.assertContains(page, 'aria-expanded="true" aria-controls="nav-devices-list"')
        create = self.client.get(self.create_url)
        self.assertContains(create, f'href="{self.list_url}" aria-current="page"')

    # --- yeni cihaz kaydı: form --------------------------------------------------
    def test_create_form_matches_design_and_preselects_least_busy_technician(self):
        busy = _mk_tech("ayse.demir", "Ayşe", "Demir", specialization="Ekran Onarımı")
        free = _mk_tech("sila.karatas", "Sıla", "Karataş", specialization="Anakart Onarımı")
        for _ in range(3):
            _mk_device(technician=busy, status=Device.Status.REPAIRING)
        _mk_device(technician=free, status=Device.Status.REPAIRING)
        page = self.client.get(self.create_url)
        for text in ("Yeni cihaz kaydı", "Müşteri", "Ad soyad", "Telefon", "E-posta", "Cihaz", "Kategori", "Marka", "Model",
                     "Seri no", "Müşterinin bildirdiği arıza", "Fotoğraf ve video ekle", "Dosya Seç", "Teknisyen ata", "Teknisyene not",
                     "Kayıt özeti", "Kaydı oluştur", "Vazgeç"):
            self.assertContains(page, text)
        self.assertEqual(page.context["selected_id"], free.pk)  # en az işi olan seçili gelir
        html = page.content.decode()
        free_opt = f'value="{free.pk}" data-name="Sıla Karataş" selected>Sıla Karataş — 1 aktif iş · Anakart Onarımı'
        self.assertIn(free_opt, html)
        # liste azdan çoğa sıralı
        self.assertLess(html.index("Sıla Karataş — 1 aktif iş"), html.index("Ayşe Demir — 3 aktif iş · Ekran Onarımı"))

    def test_create_form_lists_on_leave_technician_last_and_disabled(self):
        away = _mk_tech("izinli.tek", "İzinli", "Tek")
        TechnicianLeave.objects.create(technician=away, start_date=_tz.localdate(), end_date=_tz.localdate(),
                                       status=TechnicianLeave.Status.APPROVED)
        page = self.client.get(self.create_url)
        self.assertContains(page, "İzinli Tek — 0 aktif iş · İzinde")
        self.assertIn(f'value="{away.pk}" data-name="İzinli Tek" disabled', page.content.decode())
        self.assertIsNone(page.context["selected_id"])

    # --- yeni cihaz kaydı: kaydetme ----------------------------------------------
    def test_create_without_technician_waits_in_new_records_and_emails_code(self):
        resp = self.client.post(self.create_url, self._form())
        device = Device.objects.get()
        self.assertRedirects(resp, reverse("core:admin_device_created", args=[device.pk]))
        self.assertTrue(device.tracking_id.startswith(f"TS-{_tz.now().year}-"))
        self.assertEqual(device.status, Device.Status.RECEIVED)
        self.assertIsNone(device.technician)
        self.assertEqual(device.customer_email, "mehmet@mail.com")
        self.assertEqual(device.customer_phone, "(533) 305 54 24")
        self.assertEqual(device.created_by, self.admin)
        self.assertTrue(Device.objects.filter(_new_devices_filter(), pk=device.pk).exists())
        # tam bir e-posta: takip kodu müşteriye gider (çift gönderim yok)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["mehmet@mail.com"])
        self.assertIn(device.tracking_id, mail.outbox[0].body)
        done = self.client.get(resp.url)
        self.assertContains(done, "Kayıt oluşturuldu")
        self.assertContains(done, device.tracking_id)
        self.assertContains(done, "Müşteriye bu takip kodu e-posta ile iletilmiştir.")
        self.assertContains(done, "Cihaz detayına git")
        self.assertContains(done, "Cihazlar listesini görüntüle")
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.CREATE, target_id=str(device.pk)).exists())

    def test_create_with_technician_assigns_and_notifies(self):
        tech = _mk_tech()
        self.client.post(self.create_url, self._form(technician=str(tech.pk), technician_note="Önce depolamayı kontrol edin."))
        device = Device.objects.get()
        self.assertEqual(device.technician, tech)
        self.assertIn("Önce depolamayı kontrol edin.", Notification.objects.get(recipient=tech, device=device).body)
        self.assertIsNotNone(device.assigned_at)
        self.assertTrue(Notification.objects.filter(recipient=tech, device=device).exists())
        self.assertEqual(len(mail.outbox), 1)

    def test_create_links_existing_customer_account_by_email(self):
        customer = User.objects.create_user("musteri1", "mehmet@mail.com", "Pass#12345", role=User.Role.CUSTOMER,
                                            first_name="Mehmet", last_name="Kaya", is_temporary_password=False)
        self.client.post(self.create_url, self._form())
        self.assertEqual(Device.objects.get().customer, customer)

    def test_create_rejects_bad_input_and_unassignable_technician(self):
        away = _mk_tech("izinli.tek", "İzinli", "Tek")
        TechnicianLeave.objects.create(technician=away, start_date=_tz.localdate(), end_date=_tz.localdate(),
                                       status=TechnicianLeave.Status.APPROVED)
        for bad in (self._form(customer_name="Mehmet 123"), self._form(customer_phone="123"),
                    self._form(customer_email="gecersiz"), self._form(brand=""), self._form(model=""),
                    self._form(issue_description="  "), self._form(device_type="rocket"),
                    self._form(technician=str(away.pk)), self._form(technician="999999")):
            resp = self.client.post(self.create_url, bad)
            self.assertEqual(resp.status_code, 200)
        self.assertEqual(Device.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_create_with_attachments(self):
        photo = SimpleUploadedFile("ekran.jpg", b"\xff\xd8\xff" + b"0" * 100, content_type="image/jpeg")
        bad = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/octet-stream")
        resp = self.client.post(self.create_url, {**self._form(), "attachments": [photo]})
        device = Device.objects.get()
        self.assertEqual(device.attachments.count(), 1)
        self.assertEqual(device.attachments.get().kind, DeviceAttachment.Kind.PHOTO)
        self.assertEqual(resp.status_code, 302)
        resp = self.client.post(self.create_url, {**self._form(customer_email="x@y.com"), "attachments": [bad]})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "desteklenmiyor")
        self.assertEqual(Device.objects.count(), 1)

    def test_created_page_warns_when_email_fails(self):
        with mock.patch("core.views.send_device_registered_email", return_value=False):
            resp = self.client.post(self.create_url, self._form())
        done = self.client.get(resp.url)
        self.assertContains(done, "Takip kodu e-posta ile gönderilemedi")
        self.assertNotContains(done, "Müşteriye bu takip kodu e-posta ile iletilmiştir.")

    # --- detay -------------------------------------------------------------------
    def test_detail_opens_any_device_and_hides_assign_for_closed_ones(self):
        repairing = _mk_device(status=Device.Status.REPAIRING)
        page = self.client.get(reverse("core:admin_device_detail", args=[repairing.pk]))
        self.assertContains(page, "Tüm cihazlara dön")
        self.assertContains(page, "Teknisyene ata")
        delivered = _mk_device(status=Device.Status.DELIVERED)
        closed = self.client.get(reverse("core:admin_device_detail", args=[delivered.pk]))
        self.assertEqual(closed.status_code, 200)
        self.assertNotContains(closed, 'id="assign-form"')
        tech = _mk_tech()
        resp = self.client.post(reverse("core:admin_device_detail", args=[delivered.pk]), {"technician": str(tech.pk)})
        self.assertEqual(resp.status_code, 302)
        delivered.refresh_from_db()
        self.assertIsNone(delivered.technician)
        self.assertEqual(self.client.get(reverse("core:admin_device_detail", args=[999999])).status_code, 404)

    def test_detail_assignment_from_all_devices_stays_on_detail(self):
        tech = _mk_tech()
        device = _mk_device(status=Device.Status.REPAIRING)
        url = reverse("core:admin_device_detail", args=[device.pk])
        resp = self.client.post(url, {"technician": str(tech.pk)})
        self.assertRedirects(resp, url)
        device.refresh_from_db()
        self.assertEqual(device.technician, tech)



# --------------------------------------------------------------------------
# Teknisyen atama: yalnızca kargoya verilmiş / elden teslim alınmış cihazlarda aktif
# Müşteri ön kaydında admin bildirimi (ayrıntılı)
# --------------------------------------------------------------------------
class AssignOnlyAfterHandoverTests(TestCase):
    def setUp(self):
        self.admin = User.objects.get(username="admin")
        self.tech = _mk_tech()
        self.client.force_login(self.admin)
        Notification.objects.all().delete()

    def _url(self, device):
        return reverse("core:admin_new_device_detail", args=[device.pk])

    def test_locked_before_cargo_or_in_person_handover(self):
        cargo = _mk_device(delivery_method="cargo", status=Device.Status.AWAITING_CARGO)
        person = _mk_device(delivery_method="self", status=Device.Status.PRE_REGISTERED)
        for device, hint in ((cargo, "Müşteri cihazı kargoya verdiğinde teknisyen atayabilirsiniz."),
                             (person, "Cihaz elden teslim alındığında teknisyen atayabilirsiniz.")):
            page = self.client.get(self._url(device))
            self.assertContains(page, hint)
            self.assertContains(page, 'id="ndd-tech" name="technician" required disabled')
            self.assertRegex(page.content.decode(), r'<button class="ndd-btn ndd-btn-primary" type="submit" disabled>Teknisyene ata')
            resp = self.client.post(self._url(device), {"technician": str(self.tech.pk)})
            self.assertRedirects(resp, self._url(device))
            device.refresh_from_db()
            self.assertIsNone(device.technician)
        self.assertFalse(Notification.objects.filter(recipient=self.tech).exists())

    def test_assign_device_service_refuses_too(self):
        from django.core.exceptions import ValidationError
        from .staffing import assign_device
        device = _mk_device(delivery_method="cargo", status=Device.Status.AWAITING_CARGO)
        with self.assertRaises(ValidationError):
            assign_device(device, self.tech, self.admin)
        device.refresh_from_db()
        self.assertIsNone(device.technician)

    def test_unlocks_when_cargo_is_given(self):
        device = _mk_device(delivery_method="cargo", status=Device.Status.IN_TRANSIT)
        page = self.client.get(self._url(device))
        self.assertNotContains(page, "teknisyen atayabilirsiniz.")
        self.assertNotContains(page, 'name="technician" required disabled')
        self.client.post(self._url(device), {"technician": str(self.tech.pk)})
        device.refresh_from_db()
        self.assertEqual(device.technician, self.tech)

    def test_unlocks_after_in_person_receipt(self):
        device = _mk_device(delivery_method="self", status=Device.Status.PRE_REGISTERED)
        self.client.post(self._url(device), {"action": "receive"})
        device.refresh_from_db()
        self.assertEqual(device.status, Device.Status.RECEIVED)
        self.client.post(self._url(device), {"technician": str(self.tech.pk)})
        device.refresh_from_db()
        self.assertEqual(device.technician, self.tech)

    def test_all_devices_detail_is_locked_the_same_way(self):
        device = _mk_device(delivery_method="cargo", status=Device.Status.AWAITING_CARGO)
        url = reverse("core:admin_device_detail", args=[device.pk])
        self.assertContains(self.client.get(url), "Müşteri cihazı kargoya verdiğinde teknisyen atayabilirsiniz.")
        self.client.post(url, {"technician": str(self.tech.pk)})
        device.refresh_from_db()
        self.assertIsNone(device.technician)


class CustomerRegistrationAdminNotificationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.get(username="admin")
        self.customer = User.objects.create_user("musteri@example.com", "musteri@example.com", "Pass#12345",
                                                 role=User.Role.CUSTOMER, first_name="Ayşe", last_name="Yılmaz",
                                                 is_temporary_password=False)

    def _register(self, **extra):
        device = Device(customer=self.customer, customer_name="Ayşe Yılmaz", customer_phone="(533) 305 54 24",
                        customer_email="musteri@example.com", device_type="laptop", brand="Dell", model="Latitude 5420",
                        issue_description="Ekran titriyor ve zaman zaman kararıyor.", **extra)
        device._actor = self.customer
        device._skip_email = True
        device.save()
        return device

    def test_admin_gets_detailed_notification_with_detail_button(self):
        device = self._register(delivery_method="cargo", status=Device.Status.AWAITING_CARGO)
        note = Notification.objects.get(recipient=self.admin)
        self.assertEqual(note.title, "Yeni cihaz ön kaydı")
        for text in (device.tracking_id, "Ayşe Yılmaz", "Dell Latitude 5420", "Dizüstü", "kargo ile gönderecek",
                     "(533) 305 54 24", "Ekran titriyor"):
            self.assertIn(text, note.body)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("core:notifications_list"))
        self.assertContains(page, "Cihaz detayı")
        self.assertContains(page, f'href="{reverse("core:admin_new_device_detail", args=[device.pk])}"')

    def test_notification_for_in_person_delivery(self):
        self._register(delivery_method="self", status=Device.Status.PRE_REGISTERED)
        self.assertIn("kendisi teslim edecek", Notification.objects.get(recipient=self.admin).body)
