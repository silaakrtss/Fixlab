"""Müşteri onayı akışı: teknisyen açıklamayla onay ister → müşteri onaylar / reddeder → teknisyene bildirim."""
import re

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Device, DeviceApproval, DeviceNote, Notification, User


def _user(username, role, **extra):
    return User.objects.create_user(username, f"{username}@example.com", "Pass#12345", role=role,
                                    first_name=username.title(), last_name="T", is_temporary_password=False, **extra)


MESAJ = "Klavye sıvı teması nedeniyle değişmeli. Parça + işçilik 1.800 TL, süre 2 iş günü."


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ApprovalFlowTests(TestCase):
    def setUp(self):
        self.tech = _user("ayse", User.Role.TECHNICIAN)
        self.customer = _user("mehmet", User.Role.CUSTOMER)
        self.device = Device.objects.create(customer=self.customer, customer_name="Mehmet T", customer_email="mehmet@example.com",
                                            brand="Asus", model="IdeaPad", issue_description="Klavye çalışmıyor",
                                            status=Device.Status.DIAGNOSING, technician=self.tech)
        self.act = reverse("core:technician_device_action", args=[self.device.tracking_id])
        self.tech_url = reverse("core:technician_device", args=[self.device.tracking_id])
        Notification.objects.all().delete()
        mail.outbox.clear()

    def _iste(self, note=MESAJ, **extra):
        self.client.force_login(self.tech)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.act, {"action": "update", "status": "waiting_approval", "note": note, **extra})

    # --- teknisyen ---
    def test_note_is_required(self):
        self._iste(note="")
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, Device.Status.DIAGNOSING)
        self.assertFalse(DeviceApproval.objects.exists())
        self.assertContains(self.client.get(self.tech_url), "müşteriye iletilecek açıklamayı yazın")

    def test_request_sends_message_to_customer(self):
        self._iste()
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, Device.Status.WAITING_APPROVAL)
        ap = DeviceApproval.objects.get()
        self.assertEqual((ap.message, ap.status), (MESAJ, "pending"))
        # müşteri: açıklamalı tek bildirim (genel «Onayınız bekleniyor» bildirimi tekrar gitmez) + onay bağlantılı e-posta
        notes = Notification.objects.filter(recipient=self.customer)
        self.assertEqual(notes.count(), 1)
        self.assertIn("Klavye sıvı teması", notes.get().body)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(MESAJ, mail.outbox[0].body)
        self.assertIn("/onarim-onayi/", mail.outbox[0].body)
        # not müşteriye açık olarak geçmişe yazılır
        self.assertTrue(DeviceNote.objects.filter(customer_visible=True, text__contains=MESAJ).exists())
        self.assertContains(self.client.get(self.tech_url), "Müşteri onayı bekleniyor")

    def test_cannot_request_twice_while_pending(self):
        self._iste()
        self._iste(reapprove="on")
        self.assertEqual(DeviceApproval.objects.count(), 1)

    def test_plain_note_while_waiting_does_not_create_new_request(self):
        self._iste()
        ap = DeviceApproval.objects.get()
        ap.status = "rejected"; ap.save()
        self._iste(note="Müşteri aradı, düşünecekmiş.")  # durum aynı, «yeniden onay» işaretli değil → iç not
        self.assertEqual(DeviceApproval.objects.count(), 1)
        self._iste(note="Yeni teklif: yalnızca klavye değişimi 1.200 TL.", reapprove="on")
        self.assertEqual(DeviceApproval.objects.filter(status="pending").count(), 1)

    def test_leaving_stage_cancels_pending_request(self):
        self._iste()
        self.client.post(self.act, {"action": "update", "status": "repairing"})
        self.assertEqual(DeviceApproval.objects.get().status, "cancelled")

    # --- müşteri (uygulama) ---
    def _karar(self, decision, note=""):
        self.client.force_login(self.customer)
        return self.client.post(reverse("core:customer_approval_decide", args=[self.device.tracking_id]),
                                {"decision": decision, "note": note})

    def test_customer_sees_and_approves(self):
        self._iste()
        self.client.force_login(self.customer)
        page = self.client.get(reverse("core:customer_device_lookup") + f"?kod={self.device.tracking_id}")
        self.assertContains(page, "Onarım için onayınız bekleniyor")
        self.assertContains(page, "Klavye sıvı teması")
        Notification.objects.all().delete()
        self._karar("approve", "Önce arayın lütfen")
        ap = DeviceApproval.objects.get()
        self.assertEqual((ap.status, ap.customer_note, ap.decided_via), ("approved", "Önce arayın lütfen", "uygulama"))
        n = Notification.objects.get(recipient=self.tech)
        self.assertEqual(n.title, "Müşteri onarımı onayladı")
        self.assertIn("Önce arayın lütfen", n.body)
        self.client.force_login(self.tech)
        page = self.client.get(self.tech_url)
        self.assertContains(page, "Müşteri onarımı onayladı")
        self.assertContains(page, "Onarıma devam edin")

    def test_customer_rejects(self):
        self._iste()
        self._karar("reject")
        self.assertEqual(DeviceApproval.objects.get().status, "rejected")
        self.assertTrue(Notification.objects.filter(recipient=self.tech, title="Müşteri onarımı onaylamadı").exists())
        self.client.force_login(self.tech)
        self.assertContains(self.client.get(self.tech_url), "Onarım yapmayın")

    def test_cannot_decide_twice_or_other_customers_device(self):
        self._iste()
        self._karar("approve")
        self._karar("reject")
        self.assertEqual(DeviceApproval.objects.get().status, "approved")
        other = _user("baska", User.Role.CUSTOMER)
        self.client.force_login(other)
        r = self.client.post(reverse("core:customer_approval_decide", args=[self.device.tracking_id]), {"decision": "reject"})
        self.assertEqual(r.status_code, 404)

    # --- e-posta bağlantısı (oturumsuz) ---
    def test_email_link_needs_click_then_decides(self):
        self._iste()
        link = re.search(r"(/onarim-onayi/[^\s\"']+/)", mail.outbox[0].body).group(1)
        self.client.logout()
        page = self.client.get(link)
        self.assertContains(page, "Onarım için onayınız bekleniyor")
        self.assertEqual(DeviceApproval.objects.get().status, "pending")  # açmak tek başına karar vermez
        self.client.post(link, {"decision": "reject", "note": "Çok pahalı"})
        ap = DeviceApproval.objects.get()
        self.assertEqual((ap.status, ap.decided_via, ap.customer_note), ("rejected", "e-posta bağlantısı", "Çok pahalı"))
        self.assertContains(self.client.get(link), "Kararınız iletildi")
        self.assertEqual(self.client.get("/onarim-onayi/bozuk-token/").status_code, 404)
