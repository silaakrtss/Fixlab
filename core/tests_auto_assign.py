"""Otomatik teknisyen ataması (AI uzmanlık seçimi + iş yükü) testleri. Yapay zekâ çağrısı taklit edilir."""
from unittest import mock

from django.test import TestCase, override_settings

from . import ai, auto_assign
from .models import Device, Notification, User


def _tech(username, spec="", **extra):
    return User.objects.create_user(username, f"{username}@x.com", "Pass#12345", role=User.Role.TECHNICIAN,
                                    first_name=username.title(), last_name="T", specialization=spec,
                                    is_temporary_password=False, **extra)


def _device(status=Device.Status.RECEIVED, **extra):
    data = dict(customer_name="Mehmet Yılmaz", customer_phone="05321234567", brand="Dell", model="IdeaPad",
                issue_description="Klavye tuşları algılanmıyor", status=status)
    data.update(extra)
    return Device.objects.create(**data)


def _ai(uzmanlik, tur="tus"):
    return mock.patch.object(ai, "uzmanlik_sec", return_value={"uzmanlik": uzmanlik, "ariza_turu": tur, "gerekce": "klavye arızası"})


class AutoAssignTests(TestCase):
    def setUp(self):
        self.anakart_yogun = _tech("ali", "Anakart Onarımı")
        self.anakart_bos = _tech("veli", "anakart onarımı ")  # yazım farkı: aynı uzmanlık sayılır
        self.ekran = _tech("ayse", "Ekran Onarımı")
        _device(technician=self.anakart_yogun)
        _device(technician=self.anakart_yogun)

    def test_picks_specialist_with_least_open_jobs(self):
        d = _device()
        with _ai("Anakart Onarımı"):
            tech = auto_assign.otomatik_ata(d.pk)
        d.refresh_from_db()
        self.assertEqual(tech, self.anakart_bos)
        self.assertEqual(d.technician, self.anakart_bos)
        self.assertEqual((d.fault_category, d.fault_category_source), ("tus", "ai"))
        self.assertTrue(Notification.objects.filter(recipient=self.anakart_bos, title="Size yeni bir iş atandı").exists())
        self.assertTrue(Notification.objects.filter(title="Kayıt otomatik atandı").exists())

    def test_other_specialist_even_if_busier_than_generalists(self):
        _device(technician=self.ekran)
        d = _device()
        with _ai("Ekran Onarımı"):
            self.assertEqual(auto_assign.otomatik_ata(d.pk), self.ekran)

    def test_no_matching_specialist_falls_back_to_least_workload(self):
        d = _device()
        with _ai(""):
            tech = auto_assign.otomatik_ata(d.pk)
        self.assertIn(tech, (self.anakart_bos, self.ekran))  # ikisinin de 0 açık işi var
        self.assertNotEqual(tech, self.anakart_yogun)

    def test_ai_unavailable_falls_back_to_least_workload(self):
        d = _device()
        with mock.patch.object(ai, "uzmanlik_sec", return_value=None):
            tech = auto_assign.otomatik_ata(d.pk)
        self.assertNotEqual(tech, self.anakart_yogun)
        self.assertIn("yapay zekâ kullanılamadı", Notification.objects.filter(title="Kayıt otomatik atandı").first().body)

    def test_skips_on_leave_specialist(self):
        self.anakart_bos.is_active = False
        self.anakart_bos.save()
        d = _device()
        with _ai("Anakart Onarımı"):
            self.assertEqual(auto_assign.otomatik_ata(d.pk), self.anakart_yogun)

    def test_does_not_touch_already_assigned_or_not_arrived(self):
        elle = _device(technician=self.ekran)
        yolda = _device(status=Device.Status.AWAITING_CARGO, delivery_method=Device.DeliveryMethod.CARGO)
        with _ai("Anakart Onarımı"):
            self.assertIsNone(auto_assign.otomatik_ata(elle.pk))
            self.assertIsNone(auto_assign.otomatik_ata(yolda.pk))
        elle.refresh_from_db(); yolda.refresh_from_db()
        self.assertEqual(elle.technician, self.ekran)
        self.assertIsNone(yolda.technician)

    def test_admin_can_override_after_auto_assign(self):
        d = _device()
        with _ai("Anakart Onarımı"):
            auto_assign.otomatik_ata(d.pk)
        admin = User.objects.get(username="admin")
        self.client.force_login(admin)
        self.client.post(f"/panel/cihazlar/{d.pk}/", {"technician": self.ekran.pk})
        d.refresh_from_db()
        self.assertEqual(d.technician, self.ekran)

    def test_ai_receives_no_technician_names(self):
        d = _device()
        with mock.patch.object(ai, "_sohbet", return_value=None) as sohbet:
            auto_assign.otomatik_ata(d.pk)
        gonderilen = str(sohbet.call_args)
        for ad in ("Ali", "Veli", "Ayse", "Mehmet", "Yılmaz", "05321234567"):
            self.assertNotIn(ad, gonderilen)
        self.assertIn("anakart onarımı", gonderilen.lower())


class AdminSuggestTests(TestCase):
    """Servis kabulü formu: arıza yazılınca listede AI'ın önerdiği teknisyen seçili gelir."""
    def setUp(self):
        self.yogun = _tech("ali", "Anakart Onarımı")
        self.bos = _tech("veli", "Anakart Onarımı")
        self.ekran = _tech("ayse", "Ekran Onarımı")
        _device(technician=self.yogun)
        self.client.force_login(User.objects.get(username="admin"))
        self.url = "/panel/cihazlar/yeni/ai-oneri/"

    def test_suggests_least_busy_specialist_and_masks_customer(self):
        with _ai("Anakart Onarımı") as m:
            r = self.client.post(self.url, {"aciklama": "Mehmet Bey'in laptopuna çay döküldü", "tur": "Dizüstü",
                                            "marka": "Dell", "model": "X", "ad": "Mehmet Kaya", "telefon": "", "eposta": ""})
        d = r.json()
        self.assertEqual((d["ok"], d["id"], d["uzmanlik"], d["is"]), (True, self.bos.pk, "Anakart Onarımı", 0))
        self.assertEqual(m.call_args.args[3][0], "Mehmet Kaya")  # anonimleştirme için bilinenler listesine gider

    def test_ai_down_returns_not_ok(self):
        with mock.patch.object(ai, "uzmanlik_sec", return_value=None):
            self.assertFalse(self.client.post(self.url, {"aciklama": "Ekran kırık ve çizgili"}).json()["ok"])

    def test_technician_cannot_use(self):
        self.client.force_login(self.ekran)
        self.assertEqual(self.client.post(self.url, {"aciklama": "Ekran kırık ve çizgili"}).status_code, 403)

    def _kaydet(self, tech, kaynak, email):
        self.client.post("/panel/cihazlar/yeni/", {
            "customer_name": "Mehmet Kaya", "customer_phone": "0532 123 45 67", "customer_email": email,
            "device_type": "laptop", "brand": "Dell", "model": "X", "issue_description": "Laptopa çay döküldü",
            "technician": tech.pk, "tech_source": kaynak})
        return Device.objects.get(customer_email=email).technician

    @override_settings(AI_ENABLED=True)
    def test_submit_with_workload_default_uses_specialist(self):
        # Varsayılan (en az iş) Ayşe/Ekran; arıza anakart → kayıtta sunucu Anakart uzmanını seçer
        with _ai("Anakart Onarımı"):
            self.assertEqual(self._kaydet(self.ekran, "default", "a@example.com"), self.bos)

    @override_settings(AI_ENABLED=True)
    def test_submit_keeps_manual_choice(self):
        with _ai("Anakart Onarımı") as m:
            self.assertEqual(self._kaydet(self.ekran, "manual", "b@example.com"), self.ekran)
        m.assert_not_called()

    @override_settings(AI_ENABLED=True)
    def test_submit_keeps_default_when_ai_down(self):
        with mock.patch.object(ai, "uzmanlik_sec", return_value=None):
            self.assertEqual(self._kaydet(self.ekran, "default", "c@example.com"), self.ekran)

    def test_form_no_assignment_disables_auto_assign(self):
        with override_settings(AUTO_ASSIGN=True), mock.patch.object(auto_assign, "planla") as planla:
            self.client.post("/panel/cihazlar/yeni/", {
                "customer_name": "Mehmet Kaya", "customer_phone": "0532 123 45 67", "customer_email": "m@example.com",
                "device_type": "laptop", "brand": "Dell", "model": "X", "issue_description": "Ekran kırık",
                "technician": ""})
        self.assertTrue(Device.objects.filter(customer_email="m@example.com", technician=None).exists())
        planla.assert_not_called()


@override_settings(AUTO_ASSIGN=True)
class AutoAssignTriggerTests(TestCase):
    def setUp(self):
        _tech("ali", "Anakart Onarımı")

    def _planlanan(self, func):
        with mock.patch.object(auto_assign, "planla") as planla:
            obj = func()
        return planla, obj

    def test_triggers_on_create_when_assignable(self):
        planla, d = self._planlanan(lambda: _device())
        planla.assert_called_once_with(d.pk)

    def test_not_triggered_before_handover_but_triggered_on_cargo_sent(self):
        planla, d = self._planlanan(lambda: _device(status=Device.Status.AWAITING_CARGO,
                                                    delivery_method=Device.DeliveryMethod.CARGO))
        planla.assert_not_called()
        d.status = Device.Status.IN_TRANSIT
        planla, _ = self._planlanan(d.save)
        planla.assert_called_once_with(d.pk)

    def test_not_triggered_when_admin_chose_no_assignment(self):
        def yap():
            d = Device(customer_name="M", brand="Dell", model="X", issue_description="x", status=Device.Status.RECEIVED)
            d._no_auto_assign = True
            d.save()
        planla, _ = self._planlanan(yap)
        planla.assert_not_called()
