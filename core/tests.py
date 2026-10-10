import re

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import AuditLog, Device, User


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
        self.assertIn("sukru.celik", body)
        password = re.search(r"Geçici şifre: (\S+)", body).group(1)
        self.assertTrue(tech.check_password(password))
        self.assertGreaterEqual(len(password), 12)

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

        response = self.client.post(reverse("core:login"), {"username": "sukru.celik", "password": password})
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
        self.assertRegex(d1.tracking_id, r"^SRV-\d{4}-1001$")
        self.assertRegex(d2.tracking_id, r"^SRV-\d{4}-1002$")


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
        self.client.post(reverse("core:login"), {"username": "elif.yildiz", "password": password})
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
