import mimetypes
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import number_format
from django.views.decorators.http import require_GET, require_POST

from . import accounts
from .approvals import decide_leave, decide_part_request
from .forms import (AdminDeviceRegistrationForm, CargoSentForm, CustomerLoginForm, CustomerProfileForm, CustomerRegisterForm, DeviceRegistrationForm,
                    ForcedPasswordChangeForm, ForgotForm, LeaveForm, LoginForm, ResetCodeForm, ResetPasswordForm,
                    StaffCreateForm, StockEntryForm, StockPartForm, CARGO_COMPANIES)
from . import cargo as cargo_flow
from .cargo import CargoError, PIPELINE_TARGETS
from .context_processors import new_devices_filter
from .models import (AuditLog, Device, DeviceAttachment, Notification, NotificationPreference, PartRequest,
                     StockMovement, StockPart, TechnicianLeave, User)
from .notifications import visible_notifications
from .phone import pretty_phone
from .staffing import (DONE_STATUSES, assign_block_reason, assign_device, assignable_technicians, cancel_leave, grant_leave, leave_calendar,
                       open_job_count, technician_rows, turkish_sort_key, update_leave)
from .staffing import delete_or_archive as delete_or_archive_staff
from .stock import StockError, add_stock, adjust_stock, delete_or_archive
from .timeline import build_stages, phase_of, status_note, status_tone
from .services import (RESET_COOLDOWN_SECONDS, check_reset_code, check_verification_code, consume_reset_codes,
                       consume_verification_codes, issue_reset_code, issue_verification_code, provision_user,
                       reset_credentials)
from .utils import log_action, mask_email, send_device_registered_email


def role_home_url(user):
    return {
        User.Role.ADMIN: "core:admin_dashboard",
        User.Role.TECHNICIAN: "core:technician_dashboard",
    }.get(user.role, "core:customer_home")


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.user.role not in roles:
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return login_required(wrapper)
    return decorator


# --- Giriş / çıkış / yönlendirme -------------------------------------------
class FixlabLoginView(LoginView):
    template_name = "core/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class CustomerLoginView(LoginView):
    template_name = "core/customer_login.html"
    authentication_form = CustomerLoginForm
    redirect_authenticated_user = True

    def form_invalid(self, form):
        # Şifresi doğru ama e-postasını henüz doğrulamamış müşteri: yeni kod gönderilip doğrulama sayfasına alınır.
        if any(e.code == "unverified" for e in form.non_field_errors().as_data()):
            email = (form.cleaned_data.get("username") or "").strip().lower()
            user = _signup_user(email)
            if user is not None:
                self.request.session[SIGNUP_EMAIL] = user.email
                _send_signup_code(self.request, user)
                messages.info(self.request, "E-posta adresiniz henüz doğrulanmadı. Size bir doğrulama kodu gönderdik; "
                                            "kodu girerek üyeliğinizi tamamlayın.")
                return redirect("core:customer_verify")
        return super().form_invalid(form)


class FixlabLogoutView(LogoutView):
    """Müşteri çıkışında müşteri giriş ekranına, personel çıkışında personel girişine döner."""

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        self._was_customer = user.is_authenticated and user.role == User.Role.CUSTOMER
        return super().dispatch(request, *args, **kwargs)

    def get_default_redirect_url(self):
        if getattr(self, "_was_customer", False):
            return resolve_url("core:customer_login")
        return super().get_default_redirect_url()


# --- Üyelik e-posta doğrulaması: üye ol -> e-postaya 6 haneli kod -> kod girilince üyelik tamamlanır ------
SIGNUP_EMAIL = "signup_email"
SIGNUP_SENT_AT = "signup_sent_at"


def _signup_user(email):
    """E-postası henüz doğrulanmamış müşteri hesabı (yoksa None)."""
    if not email:
        return None
    return User.objects.filter(email__iexact=email, role=User.Role.CUSTOMER, email_verified=False,
                               is_active=True).first()


def _send_signup_code(request, user, force=False):
    """Doğrulama kodunu yollar; arka arkaya istekleri bekleme süresiyle sınırlar. Dönüş: sent|failed|limit|wait."""
    now = timezone.now().timestamp()
    if not force and now - request.session.get(SIGNUP_SENT_AT, 0) < RESET_COOLDOWN_SECONDS:
        return "wait"
    request.session[SIGNUP_SENT_AT] = now
    return issue_verification_code(user, request=request)


def customer_register(request):
    if request.user.is_authenticated:
        return redirect("core:home")
    form = CustomerRegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            customer = form.save()
        except accounts.DirectoryConflict:
            form.add_error("email", "Bu e-posta adresiyle kayıtlı bir hesap zaten var. Giriş yapmayı deneyin.")
        except accounts.DirectoryError:
            form.add_error(None, DIRECTORY_DOWN)
        else:
            log_action(AuditLog.Action.CREATE, user=customer, request=request, target=customer,
                       description=f"Müşteri hesabı oluşturuldu (e-posta doğrulaması bekliyor): {customer.email}")
            request.session[SIGNUP_EMAIL] = customer.email
            result = _send_signup_code(request, customer, force=True)
            if result == "sent":
                messages.success(request, "Doğrulama kodu e-posta adresinize gönderildi. Kodu girerek üyeliğinizi tamamlayın.")
            else:
                messages.warning(request, "Hesabınız oluşturuldu ancak doğrulama kodu şu anda gönderilemedi. "
                                          "«Kodu Yeniden Gönder» ile tekrar deneyin.")
            return redirect("core:customer_verify")
    return render(request, "core/customer_register.html", {"form": form})


def customer_verify(request):
    """Üyelik adımı 2: e-postaya gelen 6 haneli kodu doğrula; doğruysa üyelik tamamlanır."""
    if request.user.is_authenticated:
        return redirect("core:home")
    email = request.session.get(SIGNUP_EMAIL)
    if not email:
        return redirect("core:customer_register")
    user = _signup_user(email)
    if user is None:  # zaten doğrulanmış (ya da hesap yok)
        request.session.pop(SIGNUP_EMAIL, None)
        return redirect("core:customer_login")
    form = ResetCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if check_verification_code(user, form.cleaned_data["code"]) is None:
            form.add_error("code", "Kod hatalı veya süresi dolmuş. Gerekirse yeni kod isteyin.")
        else:
            user.email_verified = True
            user.save(update_fields=["email_verified"])
            consume_verification_codes(user)
            request.session.pop(SIGNUP_EMAIL, None)
            request.session.pop(SIGNUP_SENT_AT, None)
            log_action(AuditLog.Action.UPDATE, user=user, request=request, target=user,
                       description=f"Müşteri e-postasını doğruladı, üyelik tamamlandı: {user.email}")
            messages.success(request, "E-posta adresiniz doğrulandı, üyeliğiniz tamamlandı. "
                                      "E-posta ve şifrenizle giriş yapabilirsiniz.")
            return redirect("core:customer_login")
    return render(request, "core/customer_verify.html", {
        "form": form, "masked_email": mask_email(email),
        "resend_url": reverse("core:customer_verify_resend"), "register_url": reverse("core:customer_register"),
        "login_url": reverse("core:customer_login"),
    })


@require_POST
def customer_verify_resend(request):
    email = request.session.get(SIGNUP_EMAIL)
    user = _signup_user(email)
    if user is None:
        return redirect("core:customer_register" if not email else "core:customer_login")
    result = _send_signup_code(request, user)
    if result == "sent":
        messages.info(request, "Yeni doğrulama kodu e-posta adresinize gönderildi.")
    elif result == "wait":
        messages.warning(request, RESET_WAIT)
    elif result == "limit":
        messages.warning(request, "Çok fazla kod istediniz. Lütfen bir süre sonra tekrar deneyin.")
    else:
        messages.warning(request, "Kod şu anda gönderilemedi. Lütfen biraz sonra tekrar deneyin.")
    return redirect("core:customer_verify")


# --- Şifre sıfırlama (müşteri + personel): e-posta -> 6 haneli kod -> yeni şifre ------
# Üç ayrı adım, üç ayrı sayfa: 1) e-posta  2) kod doğrulama  3) yeni şifre.
# Yeni şifre sayfasına yalnızca kodu doğrulamış oturum girebilir.
DIRECTORY_DOWN = "Kimlik doğrulama sunucusuna şu anda ulaşılamıyor. Lütfen biraz sonra tekrar deneyin."
RESET_INFO = "Bu e-posta adresine bağlı bir hesap varsa sıfırlama kodu gönderildi."
RESET_WAIT = "Yeni kod istemeden önce lütfen bir dakika bekleyin."


class ResetFlow:
    """Bir hesap grubunun (müşteri / personel) sıfırlama akışı: hangi roller, hangi adresler, hangi sayfalar."""

    def __init__(self, prefix, roles, urls, templates, scope_label, login_label, audit_text):
        self.roles = roles
        self.urls = urls              # forgot / code / password / resend / login  (URL adları)
        self.templates = templates    # forgot / code / password
        self.scope_label = scope_label
        self.login_label = login_label
        self.audit_text = audit_text
        self.k_email = f"{prefix}_email"
        self.k_sent = f"{prefix}_sent_at"
        self.k_ok = f"{prefix}_verified"   # kodu doğrulanmış PasswordResetCode kaydının numarası

    def find(self, email):
        return User.objects.filter(email__iexact=email, role__in=self.roles, is_active=True).first()

    def clear(self, request):
        for key in (self.k_email, self.k_sent, self.k_ok):
            request.session.pop(key, None)

    def send_code_if_allowed(self, request, email):
        """Hesap varsa kod yollar. Hesabın var olup olmadığı dışarıya belli edilmez.
        Dönüş: bekleme süresi dolmadıysa False."""
        now = timezone.now().timestamp()
        sent_at = request.session.get(self.k_sent, {})  # {e-posta: zaman}
        if now - sent_at.get(email, 0) < RESET_COOLDOWN_SECONDS:
            return False
        sent_at = {k: v for k, v in sent_at.items() if now - v < RESET_COOLDOWN_SECONDS}
        sent_at[email] = now
        request.session[self.k_sent] = sent_at
        request.session.pop(self.k_ok, None)  # yeni kod istendi: önceki doğrulama geçersiz
        user = self.find(email)
        if user:
            issue_reset_code(user, request=request)
        return True

    def verified_record(self, request, user):
        """Oturumda doğrulanmış ve hâlâ geçerli (kullanılmamış, süresi dolmamış) kod kaydı; yoksa None."""
        record_id = request.session.get(self.k_ok)
        if not record_id or user is None:
            return None
        return user.reset_codes.filter(pk=record_id, used_at__isnull=True, expires_at__gt=timezone.now()).first()


CUSTOMER_RESET = ResetFlow(
    prefix="reset", roles=(User.Role.CUSTOMER,),
    urls={"forgot": "core:customer_forgot", "code": "core:customer_reset_code",
          "password": "core:customer_reset_password", "resend": "core:customer_resend_code",
          "login": "core:customer_login"},
    templates={"forgot": "core/customer_forgot.html", "code": "core/customer_reset.html",
               "password": "core/customer_new_password.html"},
    scope_label="Müşteri hesabı", login_label="Müşteri Girişine Dön",
    audit_text="Müşteri şifresi e-posta kodu ile yenilendi")

STAFF_RESET = ResetFlow(
    prefix="staff_reset", roles=(User.Role.ADMIN, User.Role.TECHNICIAN),
    urls={"forgot": "core:staff_forgot", "code": "core:staff_reset_code",
          "password": "core:staff_reset_password", "resend": "core:staff_resend_code",
          "login": "core:login"},
    templates={"forgot": "core/staff_forgot.html", "code": "core/staff_reset_code.html",
               "password": "core/staff_new_password.html"},
    scope_label="Personel hesabı", login_label="Personel Girişine Dön",
    audit_text="Personel şifresi e-posta kodu ile yenilendi")


def _reset_context(flow, **extra):
    return {"scope_label": flow.scope_label, "login_label": flow.login_label,
            "forgot_url": reverse(flow.urls["forgot"]), "resend_url": reverse(flow.urls["resend"]),
            "login_url": reverse(flow.urls["login"]), **extra}


def _reset_forgot(request, flow):
    """1. adım: e-posta adresi -> kod gönder."""
    if request.user.is_authenticated:
        return redirect("core:home")
    form = ForgotForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        request.session.pop(flow.k_ok, None)
        request.session[flow.k_email] = email
        if flow.send_code_if_allowed(request, email):
            messages.info(request, RESET_INFO)
        else:
            messages.warning(request, RESET_WAIT)
        return redirect(flow.urls["code"])
    return render(request, flow.templates["forgot"], _reset_context(flow, form=form))


def _reset_code(request, flow):
    """2. adım: yalnızca 6 haneli kodu doğrula. Doğruysa yeni şifre sayfasına geç."""
    if request.user.is_authenticated:
        return redirect("core:home")
    email = request.session.get(flow.k_email)
    if not email:
        return redirect(flow.urls["forgot"])
    user = flow.find(email)
    form = ResetCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        record = check_reset_code(user, form.cleaned_data["code"]) if user else None
        if record is None:
            form.add_error("code", "Kod hatalı veya süresi dolmuş. Gerekirse yeni kod isteyin.")
        else:
            request.session[flow.k_ok] = record.pk
            return redirect(flow.urls["password"])
    return render(request, flow.templates["code"], _reset_context(flow, form=form, masked_email=mask_email(email)))


def _reset_password(request, flow):
    """3. adım: kod doğrulandıysa yeni şifre belirlet."""
    if request.user.is_authenticated:
        return redirect("core:home")
    email = request.session.get(flow.k_email)
    if not email:
        return redirect(flow.urls["forgot"])
    user = flow.find(email)
    if flow.verified_record(request, user) is None:
        request.session.pop(flow.k_ok, None)
        messages.warning(request, "Önce e-postanıza gelen doğrulama kodunu girin.")
        return redirect(flow.urls["code"])
    form = ResetPasswordForm(request.POST or None, user=user)
    if request.method == "POST" and form.is_valid():
        try:
            accounts.apply_password(user, form.cleaned_data["password1"])  # LDAP açıkken şifre dizine yazılır
        except accounts.DirectoryError:
            form.add_error(None, DIRECTORY_DOWN)
            return render(request, flow.templates["password"], _reset_context(flow, form=form, masked_email=mask_email(email)))
        user.is_temporary_password = False  # kendi belirlediği şifre kalıcıdır
        user.save(update_fields=["password", "is_temporary_password"])
        consume_reset_codes(user)
        flow.clear(request)
        log_action(AuditLog.Action.PASSWORD_CHANGE, user=user, request=request, target=user,
                   description=flow.audit_text)
        messages.success(request, "Şifreniz yenilendi. Yeni şifrenizle giriş yapabilirsiniz.")
        return redirect(flow.urls["login"])
    return render(request, flow.templates["password"], _reset_context(flow, form=form, masked_email=mask_email(email)))


@require_POST
def _reset_resend(request, flow):
    email = request.session.get(flow.k_email)
    if not email:
        return redirect(flow.urls["forgot"])
    if flow.send_code_if_allowed(request, email):
        messages.info(request, RESET_INFO)
    else:
        messages.warning(request, RESET_WAIT)
    return redirect(flow.urls["code"])


def customer_forgot(request):
    return _reset_forgot(request, CUSTOMER_RESET)


def customer_reset_code(request):
    return _reset_code(request, CUSTOMER_RESET)


def customer_reset_password(request):
    return _reset_password(request, CUSTOMER_RESET)


def customer_resend_code(request):
    return _reset_resend(request, CUSTOMER_RESET)


def staff_forgot(request):
    return _reset_forgot(request, STAFF_RESET)


def staff_reset_code(request):
    return _reset_code(request, STAFF_RESET)


def staff_reset_password(request):
    return _reset_password(request, STAFF_RESET)


def staff_resend_code(request):
    return _reset_resend(request, STAFF_RESET)


def landing(request):
    """Açılış (tanıtım) sayfası: logoya tıklanınca giriş yapmış olsun olmasın herkese gösterilir."""
    return render(request, "core/landing.html")


def home(request):
    """Giriş yapmış kullanıcı rol sayfasına gider; ziyaretçiye açılış (tanıtım) sayfası gösterilir."""
    if request.user.is_authenticated:
        return redirect(role_home_url(request.user))
    return render(request, "core/landing.html")


@login_required
def force_password_change(request):
    user = request.user
    if not user.is_temporary_password:
        return redirect(role_home_url(user))

    form = ForcedPasswordChangeForm(user, request.POST or None)
    saved = False
    if request.method == "POST" and form.is_valid():
        try:
            user = form.save(commit=False)  # LDAP açıkken yeni şifre dizine yazılır
            saved = True
        except accounts.DirectoryError:
            form.add_error(None, DIRECTORY_DOWN)
    if saved:
        user.is_temporary_password = False
        user.save(update_fields=["password", "is_temporary_password"])
        update_session_auth_hash(request, user)  # oturum açık kalsın
        log_action(AuditLog.Action.PASSWORD_CHANGE, user=user, request=request, target=user,
                   description="Geçici şifre kalıcı şifreyle değiştirildi")
        messages.success(request, "Şifreniz güncellendi. FIXLAB'e hoş geldiniz!")
        return redirect(role_home_url(user))
    return render(request, "core/force_password_change.html", {"form": form})


# --- Panel ekranları -------------------------------------------------------
def _ago(moment):
    """Kısa, okunur geçen süre: '35 dakika önce', '2 saat önce', '1 gün önce'."""
    seconds = int((timezone.now() - moment).total_seconds())
    if seconds < 60:
        return "az önce"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} dakika önce"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} saat önce"
    days = hours // 24
    if days < 30:
        return f"{days} gün önce"
    return timezone.localtime(moment).strftime("%d.%m.%Y")


@role_required(User.Role.ADMIN)
def admin_dashboard(request):
    status_counts = dict(Device.objects.values_list("status").annotate(c=Count("id")))
    open_statuses = [s for s in Device.Status.values if s not in ("delivered", "cancelled", "unrepairable")]
    critical_all = [p for p in StockPart.objects.filter(is_active=True) if p.is_critical]
    now = timezone.localtime()
    recent_logs = list(AuditLog.objects.select_related("user")[:8])
    for log in recent_logs:
        log.ago = _ago(log.created_at)
    context = {
        "technician_count": User.objects.filter(role=User.Role.TECHNICIAN, is_active=True).count(),
        "open_device_count": Device.objects.filter(status__in=open_statuses).count(),
        "ready_count": status_counts.get(Device.Status.READY, 0),
        "critical_parts": critical_all[:6],
        "pending_leave_count": TechnicianLeave.objects.filter(status=TechnicianLeave.Status.PENDING).count(),
        "recent_logs": recent_logs,
        # Dashboard tasarımındaki özet kartları ve «Son kayıtlar» tablosu
        "month_device_count": Device.objects.filter(received_at__year=now.year, received_at__month=now.month).count(),
        "repairing_count": status_counts.get(Device.Status.REPAIRING, 0) + status_counts.get(Device.Status.DIAGNOSING, 0),
        "critical_count": len(critical_all),
        "recent_devices": Device.objects.select_related("technician").order_by("-received_at")[:5],
    }
    return render(request, "core/admin_dashboard.html", context)


MONTHS_TR = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]


@role_required(User.Role.ADMIN)
def admin_new_devices(request):
    """Cihazlar › Yeni kayıtlar: müşterilerin oluşturduğu, teknisyen atanmayı bekleyen servis kayıtları."""
    base = Device.objects.filter(new_devices_filter())
    total_new = base.count()
    query = request.GET.get("q", "").strip()[:100]
    dtype = request.GET.get("type", "")
    if dtype not in Device.DeviceType.values:
        dtype = ""
    sort = "old" if request.GET.get("sort") == "old" else "new"
    qs = base
    if query:
        qs = qs.filter(Q(tracking_id__icontains=query) | Q(customer_name__icontains=query) | Q(brand__icontains=query)
                       | Q(model__icontains=query) | Q(issue_title__icontains=query)
                       | Q(issue_description__icontains=query))
    if dtype:
        qs = qs.filter(device_type=dtype)
    devices = list(qs.order_by("received_at", "id") if sort == "old" else qs.order_by("-received_at", "-id"))
    for device in devices:
        local = timezone.localtime(device.received_at)
        device.registered_label = f"{local.day} {MONTHS_TR[local.month - 1]} {local.year} · {local:%H:%M}"
        device.waiting_assignment = device.status == Device.Status.RECEIVED
    return render(request, "core/admin_new_devices.html", {
        "devices": devices, "shown_count": len(devices), "total_new": total_new, "q": query, "dtype": dtype,
        "sort": sort, "type_choices": Device.DeviceType.choices, "filtered": bool(query or dtype),
    })


# --- Cihazlar › Yeni kayıtlar › Detay ve ata ---------------------------------------
# «Durum — hangi aşamada?» çubuğu: aşama numaraları timeline.PHASE ile aynıdır (1..7).
DETAIL_PHASES = [(1, "Kayıt alındı"), (2, "Kargoda · Servise geliyor"), (3, "Servis kabulü"), (4, "İnceleme / onarım"),
                 (5, "Ödeme"), (6, "Kargoda · Müşteriye gidiyor"), (7, "Teslim edildi")]
STAGE_HINTS = {
    Device.Status.PRE_REGISTERED: "Müşterinin cihazı servise teslim etmesi bekleniyor. ",
    Device.Status.AWAITING_CARGO: "Müşterinin cihazı kargoya vermesi bekleniyor. ",
    Device.Status.IN_TRANSIT: "Cihaz servise doğru yolda. ",
    Device.Status.RECEIVED: "Cihaz servise ulaştı. ",
}


def _short_date(moment):
    local = timezone.localtime(moment)
    return f"{local.day} {MONTHS_TR[local.month - 1]} {local.year}"


def _long_date(moment):
    local = timezone.localtime(moment)
    return f"{_short_date(moment)}, {local:%H:%M}"


def _stage_bar(device):
    """(adımlar, mevcut aşama etiketi, bilgi cümlesi). Kargosuz (elden teslim) kayıtlarda kargo adımları gösterilmez."""
    phase = phase_of(device.status) or 1
    visible = [(p, label) for p, label in DETAIL_PHASES if device.is_cargo or p not in (2, 6)]
    steps, current_label = [], DETAIL_PHASES[0][1]
    for number, (p, label) in enumerate(visible, start=1):
        state = "done" if p < phase else "current" if p == phase else "todo"
        if state == "current":
            current_label = label
        steps.append({"number": number, "label": label, "state": state})
    tech = device.technician
    tail = (f"Kayıt {tech.display_name} teknisyenine atandı." if tech
            else "Kayıt teknisyen atanmayı bekliyor; atama yapılınca teknisyen paneline düşer.")
    return steps, current_label, f"Mevcut aşama: {current_label}. {STAGE_HINTS.get(device.status, '')}{tail}"


def _device_history(device):
    """İşlem geçmişi: kayıt oluşturma, durum değişiklikleri ve atamalar (eskiden yeniye)."""
    status_labels = dict(Device.Status.choices)
    events = []
    for h in device.status_history.order_by("created_at", "id"):
        if not h.old_status:
            by_customer = bool(device.customer_id and h.changed_by_id == device.customer_id)
            events.append((h.created_at, "Müşteri cihaz kaydı oluşturdu" if by_customer else (h.note or "Kayıt oluşturuldu")))
            if by_customer:
                events.append((h.created_at, "Kayıt “Yeni kayıtlar” listesine düştü, yöneticiye bildirim gitti"))
        else:
            text = f"Durum değişti: {status_labels.get(h.old_status, h.old_status)} → {status_labels.get(h.new_status, h.new_status)}"
            events.append((h.created_at, text + (f" ({h.note})" if h.note else "")))
    for log in AuditLog.objects.filter(target_type="Device", target_id=str(device.pk), action=AuditLog.Action.UPDATE):
        events.append((log.created_at, log.description))
    if not events:
        events.append((device.received_at, "Kayıt oluşturuldu"))
    events.sort(key=lambda e: e[0])
    return [{"text": text, "when": _long_date(moment).replace(",", "")} for moment, text in events]


def _previous_devices(device):
    qs = Device.objects.exclude(pk=device.pk)
    if device.customer_id:
        qs = qs.filter(customer_id=device.customer_id)
    elif device.customer_email:
        qs = qs.filter(customer_email__iexact=device.customer_email)
    else:
        return []
    rows = list(qs.order_by("-received_at")[:3])
    for row in rows:
        local = timezone.localtime(row.received_at)
        row.when_label = f"{MONTHS_TR[local.month - 1]} {local.year}"
        row.tone = status_tone(row.status)
    return rows


@role_required(User.Role.ADMIN)
def admin_new_device_detail(request, pk):
    """Yeni kayıt ayrıntısı + «Teknisyene ata». Yalnızca «Yeni kayıtlar» listesindeki kayıtlar açılır."""
    return _device_detail_page(request, pk, all_devices=False)


@role_required(User.Role.ADMIN)
def admin_device_detail(request, pk):
    """Cihazlar › Tüm cihazlar › Detay: her cihazın ayrıntısı (atama kartı yalnızca kapanmamış kayıtlarda görünür)."""
    return _device_detail_page(request, pk, all_devices=True)


def _device_detail_page(request, pk, all_devices):
    """«Yeni kayıtlar» ve «Tüm cihazlar» detay sayfalarının ortak gövdesi (aynı şablon)."""
    base_qs = Device.objects.all() if all_devices else Device.objects.filter(new_devices_filter())
    queryset = base_qs.select_related("technician", "customer")
    device = get_object_or_404(queryset, pk=pk)
    technicians = assignable_technicians()
    if all_devices:
        detail_name, back_name, back_label, page_label = (
            "core:admin_device_detail", "core:admin_device_list", "Tüm cihazlara dön", "Cihaz detayı")
    else:
        detail_name, back_name, back_label, page_label = (
            "core:admin_new_device_detail", "core:admin_new_devices", "Yeni kayıtlara dön", "Yeni kayıt")
    assign_reason = assign_block_reason(device)       # boş değilse atama şu an yapılamaz
    assign_closed = device.status in DONE_STATUSES    # kapanmış kayıtta atama kartı hiç gösterilmez
    can_assign = not assign_reason
    note = ""
    selected_id = None

    if request.method == "POST" and request.POST.get("action") == "receive":
        # Elden teslim: admin cihazın teslim alındığı bilgisini girer.
        try:
            cargo_flow.admin_receive_in_person(device, request.user, request.POST.get("delivered_by", ""),
                                               request.POST.get("receive_note", ""), request=request)
        except CargoError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{device.tracking_id} cihazı elden teslim alındı olarak kaydedildi.")
        if all_devices:
            return redirect(detail_name, pk=device.pk)
        return redirect("core:admin_new_device_detail", pk=device.pk) \
            if Device.objects.filter(new_devices_filter(), pk=device.pk).exists() else redirect("core:admin_new_devices")

    if request.method == "POST" and not can_assign:
        messages.error(request, assign_reason)
        return redirect(detail_name, pk=device.pk)

    if request.method == "POST":
        raw_id = request.POST.get("technician", "")
        note = request.POST.get("note", "").strip()[:200]
        technician = (User.objects.filter(pk=int(raw_id), role=User.Role.TECHNICIAN).first()
                      if raw_id.isdigit() and len(raw_id) < 10 else None)
        if technician is None:
            messages.error(request, "Lütfen kaydı atayacağınız teknisyeni seçin.")
        else:
            try:
                assign_device(device, technician, request.user, request, note=note)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                device = get_object_or_404(queryset, pk=pk)  # başarısız atama bellekteki kaydı bozmasın
            else:
                messages.success(request, f"{device.tracking_id} kaydı {technician.display_name} teknisyenine atandı.")
                return redirect(detail_name, pk=device.pk) if all_devices else redirect("core:admin_new_devices")

    if selected_id is None:
        keep = device.technician_id if any(r["tech"].pk == device.technician_id and r["available"] for r in technicians) else None
        selected_id = keep or next((r["tech"].pk for r in technicians if r["is_default"]), None)
    selected = next((r for r in technicians if r["tech"].pk == selected_id), None)

    steps, stage_label, stage_note = _stage_bar(device)
    attachments = list(device.attachments.all())
    cargo_address = ""
    if device.is_cargo and device.return_address:
        place = " / ".join(x for x in (device.return_district, device.return_city) if x)
        cargo_address = ", ".join(x for x in (device.return_address, place) if x)
    warranty = f"{device.warranty_months} ay garantili" if device.warranty_months else "Belirtilmedi"
    cost = (f"{number_format(device.estimated_cost, decimal_pos=0, use_l10n=True)} ₺"
            if device.estimated_cost and device.estimated_cost > 0 else "Belirlenmedi")
    return render(request, "core/admin_new_device_detail.html", {
        "device": device, "registered_long": _long_date(device.received_at), "registered_short": _short_date(device.received_at),
        "steps": steps, "stage_label": stage_label, "stage_note": stage_note, "history": _device_history(device),
        "previous_devices": _previous_devices(device), "attachments": attachments,
        "customer_no": f"MÜŞ-{device.customer_id:04d}" if device.customer_id else "—",
        "cargo_address": cargo_address, "warranty_label": warranty, "cost_label": cost,
        "delivery_label": "Kargo ile gönderim" if device.is_cargo else "Elden teslim",
        "technicians": technicians, "available_count": sum(1 for r in technicians if r["available"]),
        "selected_id": selected_id, "selected_name": selected["tech"].display_name if selected else "",
        "note": note, "already_assigned": bool(device.technician_id),
        "can_receive_in_person": (not device.is_cargo) and device.status == Device.Status.PRE_REGISTERED,
        "back_url": reverse(back_name), "back_label": back_label, "page_label": page_label, "can_assign": can_assign,
        "assign_reason": assign_reason, "assign_closed": assign_closed,
    })


# --- Cihazlar › Tüm cihazlar ------------------------------------------------------
LIST_PAGE_SIZE = 10
# Liste rozetleri: durum -> (kısa etiket, renk sınıfı). Etiketler modeldeki durum adlarıyla aynıdır, yalnızca
# «Teslime hazır» listede tasarımdaki gibi «Hazır» yazar.
_S = Device.Status
LIST_STATUS_PILLS = {
    _S.PRE_REGISTERED: (None, "grey"), _S.AWAITING_CARGO: (None, "grey"), _S.IN_TRANSIT: (None, "grey"),
    _S.RECEIVED: (None, "grey"), _S.RETURN_SHIPPING: (None, "grey"),
    _S.DIAGNOSING: (None, "blue"), _S.TESTING: (None, "blue"),
    _S.WAITING_APPROVAL: (None, "amber"), _S.WAITING_PART: (None, "orange"), _S.REPAIRING: (None, "amber"),
    _S.READY: ("Hazır", "green"), _S.DELIVERED: (None, "green"),
    _S.UNREPAIRABLE: (None, "red"), _S.CANCELLED: (None, "red"),
}


def _list_date(moment):
    """«28 Eyl» (bu yıl) ya da «28 Eyl 2025» (önceki yıllar)."""
    local = timezone.localtime(moment)
    text = f"{local.day} {MONTHS_TR[local.month - 1]}"
    return text if local.year == timezone.localdate().year else f"{text} {local.year}"


def _page_numbers(page_obj):
    """Sayfa numaraları: az sayfada hepsi, çoksa geçerli sayfanın çevresi (None = «…»)."""
    total, current = page_obj.paginator.num_pages, page_obj.number
    if total <= 7:
        return list(range(1, total + 1))
    wanted = sorted({1, total} | set(range(max(1, current - 1), min(total, current + 1) + 1)))
    out, last = [], 0
    for n in wanted:
        if n - last > 1:
            out.append(None)
        out.append(n)
        last = n
    return out


@role_required(User.Role.ADMIN)
def admin_device_list(request):
    """Cihazlar › Tüm cihazlar: tüm servis kayıtları; arama, durum ve teknisyen süzgeci, sayfalama."""
    query = request.GET.get("q", "").strip()[:100]
    status = request.GET.get("status", "")
    if status not in Device.Status.values:
        status = ""
    technicians = sorted(User.objects.filter(role=User.Role.TECHNICIAN),
                         key=lambda t: turkish_sort_key(t.display_name))
    tech = request.GET.get("tech", "")
    if tech != "none" and tech not in {str(t.pk) for t in technicians}:
        tech = ""

    qs = Device.objects.select_related("technician")
    for word in query.split()[:5]:  # «Dell Latitude» gibi çok sözcüklü aramada her sözcük bir alanda geçmeli
        qs = qs.filter(Q(tracking_id__icontains=word) | Q(customer_name__icontains=word) | Q(brand__icontains=word)
                       | Q(model__icontains=word) | Q(serial_number__icontains=word) | Q(issue_title__icontains=word))
    if status:
        qs = qs.filter(status=status)
    if tech == "none":
        qs = qs.filter(technician__isnull=True)
    elif tech:
        qs = qs.filter(technician_id=int(tech))

    page_obj = Paginator(qs.order_by("-received_at", "-id"), LIST_PAGE_SIZE).get_page(request.GET.get("page"))
    rows = list(page_obj.object_list)
    for device in rows:
        label, tone = LIST_STATUS_PILLS.get(device.status, (None, "grey"))
        device.pill_label = label or device.get_status_display()
        device.pill_tone = tone
        device.date_label = _list_date(device.received_at)
    keep = request.GET.copy()
    keep.pop("page", None)
    return render(request, "core/admin_device_list.html", {
        "devices": rows, "page_obj": page_obj, "page_numbers": _page_numbers(page_obj), "total": page_obj.paginator.count,
        "q": query, "status": status, "tech": tech, "status_choices": Device.Status.choices, "technicians": technicians,
        "filtered": bool(query or status or tech), "keep_query": keep.urlencode(),
    })


# --- Cihazlar › Yeni cihaz kaydı (admin servis kabulü) ------------------------------
@role_required(User.Role.ADMIN)
def admin_device_create(request):
    """Yönetici, servise getirilen cihazı müşteri adına kaydeder. Takip kodu otomatik üretilir ve müşteriye e-postalanır."""
    technicians = assignable_technicians()
    default_id = next((r["tech"].pk for r in technicians if r["is_default"]), None)
    file_errors = []
    selected_id = default_id  # açılışta en az işi olan teknisyen seçili gelir
    if request.method == "POST":
        form = AdminDeviceRegistrationForm(request.POST)
        uploads = request.FILES.getlist("attachments")
        file_errors = AdminDeviceRegistrationForm.validate_files(uploads)
        raw = request.POST.get("technician", "")
        selected_id = int(raw) if raw.isdigit() and len(raw) < 10 else None  # «atama yapma» seçiliyse boş kalır
        if form.is_valid() and not file_errors:
            try:
                with transaction.atomic():
                    device = form.save(request.user, uploads)
            except ValidationError as exc:
                form.add_error("technician", " ".join(exc.messages))
            else:
                log_action(AuditLog.Action.CREATE, user=request.user, request=request, target=device,
                           description=f"Yönetici cihaz kaydı oluşturdu: {device.tracking_id}")
                # Takip kodu e-postası kayıt işlendikten sonra gönderilir; sonuç başarı sayfasında dürüstçe gösterilir.
                sent = send_device_registered_email(device)
                request.session["admin_device_mail"] = {"pk": device.pk, "sent": bool(sent)}
                return redirect("core:admin_device_created", pk=device.pk)
    else:
        form = AdminDeviceRegistrationForm(initial={"device_type": Device.DeviceType.LAPTOP})
    return render(request, "core/admin_device_create.html", {
        "form": form, "file_errors": file_errors, "technicians": technicians, "selected_id": selected_id,
        "available_count": sum(1 for r in technicians if r["available"]),
        "max_files": settings.ATTACHMENT_MAX_FILES,
        "max_photo_mb": settings.ATTACHMENT_MAX_PHOTO_MB, "max_video_mb": settings.ATTACHMENT_MAX_VIDEO_MB,
        "type_choices": Device.DeviceType.choices,
    })


@role_required(User.Role.ADMIN)
def admin_device_created(request, pk):
    """«Kayıt oluşturuldu» ekranı: takip kodu + e-posta bilgisi."""
    device = get_object_or_404(Device.objects.select_related("technician"), pk=pk)
    info = request.session.get("admin_device_mail") or {}
    mail_sent = info.get("sent") if info.get("pk") == device.pk else None  # None: bu oturumda bilinmiyor
    return render(request, "core/admin_device_created.html", {"device": device, "mail_sent": mail_sent})


@role_required(User.Role.TECHNICIAN)
def technician_dashboard(request):
    mine = Device.objects.filter(technician=request.user)
    done = ("delivered", "cancelled", "unrepairable")
    now = timezone.now()
    # En son atanan iş en üstte; atanma zamanı olmayan eski kayıtlar kayıt tarihine göre sonra gelir.
    cards = list(mine.order_by(F("assigned_at").desc(nulls_last=True), "-received_at")[:30])
    for card in cards:
        local = timezone.localtime(card.received_at)
        card.card_date = f"{local.day} {MONTHS_TR[local.month - 1]}"
        card.is_new = bool(card.assigned_at and now - card.assigned_at < timedelta(days=3)
                           and card.status == Device.Status.RECEIVED)
    context = {
        "active_devices": mine.exclude(status__in=done).order_by("-priority", "received_at")[:10],
        "active_count": mine.exclude(status__in=done).count(),
        "ready_count": mine.filter(status=Device.Status.READY).count(),
        "pending_leaves": request.user.leaves.filter(status=TechnicianLeave.Status.PENDING).count(),
        # Görünüm için ek veriler (mevcut anahtarlar değişmedi)
        "repairing_count": mine.filter(status=Device.Status.REPAIRING).count(),
        "waiting_part_count": mine.filter(status=Device.Status.WAITING_PART).count(),
        "diagnosing_count": mine.filter(status=Device.Status.DIAGNOSING).count(),
        "device_cards": cards,
    }
    return render(request, "core/technician_dashboard.html", context)


def _decorate_device(device):
    """Şablonda kolay kullanım için cihaz nesnesine görünüm alanları ekler."""
    device.tone = status_tone(device.status)
    device.note = status_note(device)
    device.show_cargo_code = device.is_cargo and device.awaiting_arrival
    device.is_new = (timezone.now() - device.received_at) < timedelta(days=7)
    return device


_NEW_STATUSES = (Device.Status.PRE_REGISTERED, Device.Status.AWAITING_CARGO, Device.Status.IN_TRANSIT)


@role_required(User.Role.CUSTOMER)
def customer_home(request):
    S = Device.Status
    mine = Device.objects.filter(customer=request.user)
    order = ("-received_at", "-id")
    # «Yeni servis kaydınız»: servise henüz ulaşmamış kayıtlar. «Mevcut servis kayıtlarınız»: serviste işlem gören kayıtlar.
    new_qs = mine.filter(status__in=_NEW_STATUSES).order_by(*order)
    current_qs = mine.exclude(status__in=_NEW_STATUSES + (S.DELIVERED, S.CANCELLED)).order_by(*order)
    context = {
        "total_count": mine.exclude(status=S.CANCELLED).count(),
        "diagnosing_count": mine.filter(status=S.DIAGNOSING).count(),
        "ready_count": mine.filter(status=S.READY).count(),
        "new_devices": [_decorate_device(d) for d in new_qs[:3]],
        "current_devices": [_decorate_device(d) for d in current_qs[:3]],
        "current_total": current_qs.count(),
        "has_devices": mine.exists(),
    }
    return render(request, "core/customer_home.html", context)


@role_required(User.Role.CUSTOMER)
def customer_devices(request):
    """Müşterinin tüm servis kayıtları; üst çubuktaki arama (takip kodu, marka, model, arıza) buraya düşer."""
    query = (request.GET.get("q") or "").strip()[:80]
    mine = Device.objects.filter(customer=request.user).order_by("-received_at", "-id")
    if query:
        mine = mine.filter(Q(tracking_id__icontains=query) | Q(brand__icontains=query) | Q(model__icontains=query)
                           | Q(issue_title__icontains=query) | Q(issue_description__icontains=query))
    return render(request, "core/customer_devices.html", {
        "devices": [_decorate_device(d) for d in mine[:100]], "query": query,
    })


@role_required(User.Role.CUSTOMER)
def customer_help(request):
    return render(request, "core/customer_help.html")


def _profile_initial(user):
    return {"first_name": user.first_name, "last_name": user.last_name, "phone": pretty_phone(user.phone),
            "city": user.city, "district": user.district, "address": user.address}


@role_required(User.Role.CUSTOMER)
def customer_profile(request):
    S = Device.Status
    mine = Device.objects.filter(customer=request.user)
    form = CustomerProfileForm(request.POST or None, initial=_profile_initial(request.user))
    if request.method == "POST" and form.is_valid():
        user = User.objects.get(pk=request.user.pk)  # taze kopya: dizin hatasında oturumdaki kullanıcı değişmez
        form.apply_to(user)
        try:
            accounts.update_account(user)  # ad / telefon LDAP'a da yansır (LDAP kapalıysa bir şey yapmaz)
        except accounts.DirectoryError:
            form.add_error(None, DIRECTORY_DOWN)
        else:
            user.save(update_fields=["first_name", "last_name", "phone", "city", "district", "address"])
            log_action(AuditLog.Action.UPDATE, user=user, request=request, target=user,
                       description=f"Müşteri profil bilgilerini güncelledi: {user.email}")
            messages.success(request, "Profil bilgileriniz güncellendi.")
            return redirect("core:customer_profile")
    user = request.user
    return render(request, "core/customer_profile.html", {
        "form": form, "profile_user": user,
        "member_since": user.created_at or user.date_joined,
        "total_count": mine.exclude(status=S.CANCELLED).count(),
        "active_count": mine.exclude(status__in=(S.DELIVERED, S.CANCELLED, S.UNREPAIRABLE)).count(),
        "delivered_count": mine.filter(status=S.DELIVERED).count(),
    })


@role_required(User.Role.CUSTOMER)
def customer_device_create(request):
    # «Kargoyla göndereceğim» seçilince açılan teslim adresi, profildeki adresle önceden dolu gelir.
    profile = request.user
    form = DeviceRegistrationForm(request.POST or None, initial={
        "return_recipient": profile.display_name, "return_phone": pretty_phone(profile.phone),
        "return_city": profile.city, "return_district": profile.district, "return_address": profile.address})
    file_errors = []
    if request.method == "POST":
        uploads = request.FILES.getlist("attachments")
        file_errors = DeviceRegistrationForm.validate_files(uploads)
        if form.is_valid() and not file_errors:
            with transaction.atomic():
                device = form.save(request.user, uploads)
            log_action(AuditLog.Action.CREATE, user=request.user, request=request, target=device,
                       description=f"Müşteri cihaz ön kaydı oluşturdu: {device.tracking_id}")
            return redirect("core:customer_device_created", tracking_id=device.tracking_id)
    return render(request, "core/customer_device_create.html", {
        "form": form, "file_errors": file_errors,
        "max_files": settings.ATTACHMENT_MAX_FILES,
        "max_photo_mb": settings.ATTACHMENT_MAX_PHOTO_MB, "max_video_mb": settings.ATTACHMENT_MAX_VIDEO_MB,
    })


def _own_device(request, tracking_id):
    return get_object_or_404(Device, tracking_id=tracking_id, customer=request.user)


@role_required(User.Role.CUSTOMER)
def customer_device_created(request, tracking_id):
    device = _own_device(request, tracking_id)
    return render(request, "core/customer_device_created.html", {
        "device": device, "attachments": device.attachments.all(),
    })


@require_POST
@role_required(User.Role.CUSTOMER)
def customer_device_cancel(request, tracking_id):
    """Müşteri, cihazı kargoya vermeden / servise teslim etmeden kaydını iptal eder.

    İptal edilen kayıt «Yeni kayıtlar» listesinden kendiliğinden düşer (new_devices_filter iptali kapsamaz)
    ve müşteri ekranında «İptal edildi» olarak görünür.
    """
    back = f"{resolve_url('core:customer_device_lookup')}?kod={tracking_id}"
    with transaction.atomic():
        # Aynı anda gelen kargo / teslim işlemiyle yarışmamak için kayıt kilitlenip durum yeniden denetlenir.
        device = get_object_or_404(Device.objects.select_for_update(), tracking_id=tracking_id, customer=request.user)
        if device.status == Device.Status.CANCELLED:
            messages.info(request, "Bu kayıt zaten iptal edilmiş.")
            return redirect(back)
        if not device.customer_can_cancel:
            messages.error(request, "Cihaz kargoya verildiği veya servise ulaştığı için bu kayıt artık iptal edilemez. "
                                    "Yardım için servisle iletişime geçebilirsiniz.")
            return redirect(back)
        device.status = Device.Status.CANCELLED
        device._actor = request.user
        device._history_note = "Müşteri kaydı iptal etti"
        device.save()
    log_action(AuditLog.Action.STATUS_CHANGE, user=request.user, request=request, target=device,
               description=f"Müşteri cihaz kaydını iptal etti: {device.tracking_id}")
    messages.success(request, f"{device.tracking_id} numaralı kaydınız iptal edildi.")
    return redirect(back)


@role_required(User.Role.CUSTOMER)
def customer_device_lookup(request):
    code = request.GET.get("kod", "").strip()
    context = {"code": code, "searched": bool(code), "device": None}
    if code:
        # Yalnızca kendi hesabına ait kayıtlar aranır; başkasının kodu «bulunamadı» ile aynı yanıtı verir.
        device = Device.objects.filter(customer=request.user, tracking_id__iexact=code).first()
        if device:
            _decorate_device(device)
            context.update({
                "device": device, "timeline": build_stages(device),
                "attachments": device.attachments.all(), "history": device.status_history.all()[:12],
            })
    return render(request, "core/customer_device_lookup.html", context)


@role_required(User.Role.CUSTOMER)
def customer_cargo(request, tracking_id):
    device = _own_device(request, tracking_id)
    if not device.is_cargo:
        messages.info(request, "Bu kayıt için kargo süreci yok; cihazı kendiniz teslim edeceksiniz.")
        return redirect(f"{resolve_url('core:customer_device_lookup')}?kod={device.tracking_id}")
    can_report = device.status in (Device.Status.AWAITING_CARGO, Device.Status.IN_TRANSIT)
    form = CargoSentForm(request.POST or None, initial={"cargo_company": device.cargo_company,
                                                        "cargo_tracking_code": device.cargo_tracking_code})
    if request.method == "POST":
        if not can_report:
            raise PermissionDenied
        if form.is_valid():
            device.cargo_company = form.cleaned_data["cargo_company"]
            device.cargo_tracking_code = form.cleaned_data["cargo_tracking_code"]
            if device.status == Device.Status.AWAITING_CARGO:
                device.status = Device.Status.IN_TRANSIT
                device.cargo_sent_at = timezone.now()
            device._actor = request.user
            device._history_note = "Müşteri cihazı kargoya verdi"
            device.save()
            log_action(AuditLog.Action.UPDATE, user=request.user, request=request, target=device,
                       description=f"Kargo bilgisi girildi: {device.tracking_id} ({device.cargo_company})")
            messages.success(request, "Kargo bilginiz kaydedildi. Cihaz servise ulaşınca size bildirim göndereceğiz.")
            return redirect("core:customer_cargo", tracking_id=device.tracking_id)
    _decorate_device(device)
    S = Device.Status
    return render(request, "core/customer_cargo.html", {
        "device": device, "timeline": build_stages(device), "form": form, "can_report": can_report,
        "can_create_code": device.status == S.AWAITING_CARGO,                      # «Kod oluştur»
        "can_dispute": device.status == S.IN_TRANSIT and bool(device.cargo_code),   # «Kod oluştu, teslim sağlanmadı»
        "can_confirm_receipt": device.status == S.RETURN_SHIPPING,                  # «Ürünü teslim aldım»
    })


def _cargo_action(request, tracking_id, action, success):
    """Müşteri kargo işlemleri (kod oluştur / teslim sağlanmadı / teslim aldım) için ortak POST akışı."""
    device = _own_device(request, tracking_id)
    try:
        action(device, request.user, request=request)
    except CargoError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, success)
    return redirect("core:customer_cargo", tracking_id=device.tracking_id)


@require_POST
@role_required(User.Role.CUSTOMER)
def customer_cargo_code(request, tracking_id):
    return _cargo_action(request, tracking_id, cargo_flow.create_customer_cargo_code,
                         "Kargo kodunuz oluşturuldu. Kodu kargo şirketine verin; servis ekibine bildirim gönderildi.")


@require_POST
@role_required(User.Role.CUSTOMER)
def customer_cargo_handover_failed(request, tracking_id):
    return _cargo_action(request, tracking_id, cargo_flow.report_handover_failed,
                         "Teslimin yapılmadığını servis ekibine bildirdik. Kargoya vermeye hazır olduğunuzda yeni kod oluşturabilirsiniz.")


@require_POST
@role_required(User.Role.CUSTOMER)
def customer_receipt_confirm(request, tracking_id):
    device = _own_device(request, tracking_id)
    try:
        cargo_flow.confirm_delivery(device, request.user, request=request, via="uygulama")
    except CargoError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Teşekkürler, ürünü teslim aldığınızı servis ekibine bildirdik.")
    return redirect("core:customer_cargo", tracking_id=device.tracking_id)


def receipt_confirm(request, token):
    """E-postadaki «Ürünü teslim aldım» bağlantısı. Bağlantıyı açmak tek başına işlem yapmaz (e-posta tarayıcıları
    bağlantıları otomatik açabilir); teslim, sayfadaki düğmeye basılınca onaylanır. Oturum açmak gerekmez."""
    device = cargo_flow.device_from_receipt_token(token)
    if device is None:
        raise Http404
    state = "ready"
    if request.method == "POST" and device.status == Device.Status.RETURN_SHIPPING:
        try:
            cargo_flow.confirm_delivery(device, None, request=request, via="e-posta bağlantısı")
            state = "confirmed"
        except CargoError:
            state = "unavailable"
        device.refresh_from_db()
    elif device.status == Device.Status.DELIVERED:
        state = "done"
    elif device.status != Device.Status.RETURN_SHIPPING:
        state = "unavailable"
    return render(request, "core/receipt_confirm.html", {
        "device": device, "state": state, "label": f"{device.brand} {device.model}".strip(),
        "login_url": reverse("core:customer_login"),
    })


# --- Teknisyen: cihaz sayfası ve kargo / durum işlemleri ---------------------------------------
def _tech_device(request, tracking_id):
    return get_object_or_404(Device.objects.select_related("customer"), tracking_id=tracking_id, technician=request.user)


@role_required(User.Role.TECHNICIAN)
def technician_device(request, tracking_id):
    device = _tech_device(request, tracking_id)
    S = Device.Status
    labels = dict(Device.Status.choices)
    place = " / ".join(x for x in (device.return_district, device.return_city) if x)
    return render(request, "core/technician_device.html", {
        "device": device, "history": _device_history(device),
        "return_address": ", ".join(x for x in (device.return_address, place) if x),
        "awaiting_customer_code": device.is_cargo and device.status == S.AWAITING_CARGO,
        "can_receive": device.is_cargo and device.status == S.IN_TRANSIT,
        "can_start": device.status == S.RECEIVED,
        "can_set_status": device.status in cargo_flow.PIPELINE,
        "status_options": [(v, labels[v]) for v in PIPELINE_TARGETS if v != device.status],
        "can_return": device.is_cargo and device.status == S.READY,
        "returning": device.status == S.RETURN_SHIPPING,
        "cargo_companies": CARGO_COMPANIES,
        "delivery_label": "Kargo ile gönderim" if device.is_cargo else "Elden teslim",
    })


@require_POST
@role_required(User.Role.TECHNICIAN)
def technician_device_action(request, tracking_id):
    device = _tech_device(request, tracking_id)
    action = request.POST.get("action", "")
    user = request.user
    try:
        if action == "receive":
            cargo_flow.technician_receive_cargo(device, user, request=request)
            messages.success(request, "Kargo teslim alındı olarak işaretlendi. Artık işleme başlayabilirsiniz.")
        elif action == "start":
            cargo_flow.technician_start_work(device, user, request=request)
            messages.success(request, "İşleme başlandı; kayıt «İnceleniyor» durumuna geçti.")
        elif action == "status":
            cargo_flow.technician_set_status(device, user, request.POST.get("status", ""), request=request)
            messages.success(request, "Durum güncellendi.")
        elif action == "return_code":
            company = request.POST.get("cargo_company", "").strip()
            tracking = request.POST.get("cargo_tracking_code", "").strip()[:60]
            if company and company not in CARGO_COMPANIES:
                raise CargoError("Geçersiz kargo firması.")
            cargo_flow.create_return_cargo_code(device, user, company=company, tracking=tracking, request=request)
            messages.success(request, "Geri kargo kodu oluşturuldu. Müşteriye bildirim ve e-posta gönderildi.")
        else:
            raise CargoError("Geçersiz işlem.")
    except CargoError as exc:
        messages.error(request, str(exc))
    return redirect("core:technician_device", tracking_id=device.tracking_id)


@login_required
def attachment_file(request, pk):
    """Cihaz fotoğraf/videosunu yalnızca sahibi müşteriye ve personele sunar."""
    attachment = get_object_or_404(DeviceAttachment.objects.select_related("device"), pk=pk)
    user = request.user
    if user.role == User.Role.CUSTOMER and attachment.device.customer_id != user.pk:
        raise Http404
    try:
        handle = attachment.file.open("rb")
    except FileNotFoundError:
        raise Http404
    content_type = mimetypes.guess_type(attachment.original_name)[0] or "application/octet-stream"
    response = FileResponse(handle, content_type=content_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = "inline"
    return response


# --- Bildirimler -----------------------------------------------------------------
NOTIFICATION_POPUP_LIMIT = 6  # zil kutusunda görünen son bildirim sayısı


def _notification_dict(n):
    return {"id": n.id, "title": n.title, "body": n.body, "read": n.is_read,
            "url": reverse("core:notification_open", args=[n.id]),
            "time": timezone.localtime(n.created_at).strftime("%d.%m.%Y %H:%M")}


@login_required
@require_GET
def notifications_feed(request):
    """notify.js bu uç noktayı yoklar: son bildirim no, okunmamış sayısı, yeni ve son bildirimler."""
    try:
        after = int(request.GET.get("after", "0"))
    except ValueError:
        after = 0
    mine = visible_notifications(request.user)
    latest = mine.order_by("-id").values_list("id", flat=True).first() or 0
    new = [_notification_dict(n) for n in mine.filter(id__gt=after).order_by("id")[:10]]
    recent = [_notification_dict(n) for n in mine.order_by("-id")[:NOTIFICATION_POPUP_LIMIT]]
    response = JsonResponse({"latest": latest, "unread": mine.filter(read_at__isnull=True).count(),
                             "new": new, "recent": recent})
    response["Cache-Control"] = "no-store"
    return response


@login_required
def notification_open(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    if request.user.role == User.Role.ADMIN:
        # Admin: zil kutusundan ya da tarayıcı bildiriminden gelen tıklama Django yönetimine değil, Bildirimler paneline gider.
        return redirect("core:notifications_list")
    target = notification.url if notification.url.startswith("/") and not notification.url.startswith("//") else ""
    return redirect(target or role_home_url(request.user))


@login_required
@require_POST
def notifications_mark_read(request):
    visible_notifications(request.user).filter(read_at__isnull=True).update(read_at=timezone.now())
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    return redirect("core:notifications_list")


@login_required
def notifications_list(request):
    if request.user.role == User.Role.ADMIN:
        return _admin_notifications(request)
    base = "core/base_customer_app.html" if request.user.role == User.Role.CUSTOMER else "core/base_app.html"
    items = request.user.notifications.all()[:100]
    return render(request, "core/notifications.html", {"base_template": base, "items": items})


# --- Admin bildirim paneli (Bildirimlerim) ----------------------------------------
# tür -> (etiket, renk, simge, bağlantı yazısı)
NOTE_KINDS = {
    Notification.Kind.PART_REQUEST: ("Parça talebi", "orange", "box", "Cihazı aç"),
    Notification.Kind.LEAVE: ("İzin talebi", "blue", "calendar", "İzin takvimini aç"),
    Notification.Kind.DELAY: ("Gecikme", "red", "clock", "Cihazı aç"),
    Notification.Kind.NOTE: ("Not", "green", "chat", "Cihazı aç"),
    Notification.Kind.STATUS: ("Durum", "green", "check", "Cihazı aç"),
    Notification.Kind.STOCK: ("Stok", "gray", "box", "Stok hareketini aç"),
    Notification.Kind.GENERAL: ("", "gray", "bell", "Aç"),
}


def _note_time(moment, today):
    local = timezone.localtime(moment)
    if local.date() == today:
        return _ago(moment)
    if local.date() == today - timedelta(days=1):
        return f"Dün {local:%H:%M}"
    return f"{local:%d.%m.%Y %H:%M}"


def _note_card(n, today):
    label, tone, icon, link_label = NOTE_KINDS.get(n.kind, NOTE_KINDS[Notification.Kind.GENERAL])
    body = n.body
    state = None  # None: işlem yok · "pending": Onayla/Reddet · diğerleri: sonuç yazısı
    approve_url = reject_url = ""
    if n.kind == Notification.Kind.PART_REQUEST and n.part_request_id:
        pr = n.part_request
        qty = f" ({pr.quantity} adet)" if pr.quantity > 1 else ""
        body = f"{pr.device.tracking_id} için “{pr.part.name}” parçası talep etti{qty}. Stokta {pr.part.quantity} adet var."
        state = pr.status
    elif n.kind == Notification.Kind.LEAVE and n.leave_id:
        state = n.leave.status
    if state == "pending":
        approve_url = reverse("core:notification_approve", args=[n.pk])
        reject_url = reverse("core:notification_reject", args=[n.pk])
    device_url = ""
    if n.kind == Notification.Kind.GENERAL and n.device_id:
        d = n.device  # yeni kayıt / kargoya verildi bildirimleri: atama yapılacak panel detayına götürür
        awaiting = d.status in (Device.Status.PRE_REGISTERED, Device.Status.AWAITING_CARGO, Device.Status.IN_TRANSIT) or (
            d.status == Device.Status.RECEIVED and d.technician_id is None)
        device_url = reverse("core:admin_new_device_detail" if awaiting else "core:admin_device_detail", args=[d.pk])
    return {
        "device_url": device_url,
        "n": n, "heading": n.actor.display_name if n.actor_id else n.title, "label": label, "tone": tone,
        "icon": icon, "body": body, "time": _note_time(n.created_at, today),
        "link": reverse("core:notification_open", args=[n.pk]) if n.url else "", "link_label": link_label,
        "pending": state == "pending", "approve_url": approve_url, "reject_url": reject_url,
        "result": {"approved": "Onaylandı", "rejected": "Reddedildi"}.get(state, ""),
    }


def _admin_notifications(request):
    user = request.user
    qs = visible_notifications(user)
    items = list(qs.select_related("actor", "device", "leave", "part_request__part", "part_request__device")[:100])
    today = timezone.localdate()
    groups = []
    for n in items:
        day = timezone.localdate(n.created_at)
        name = "Bugün" if day == today else "Dün" if day == today - timedelta(days=1) else f"{day:%d.%m.%Y}"
        if not groups or groups[-1]["label"] != name:
            groups.append({"label": name, "cards": []})
        groups[-1]["cards"].append(_note_card(n, today))
    pending_parts = PartRequest.objects.filter(status=PartRequest.Status.PENDING).count()
    pending_leaves = TechnicianLeave.objects.filter(status=TechnicianLeave.Status.PENDING).count()
    prefs = NotificationPreference.objects.filter(user=user).first() or NotificationPreference(user=user)
    toggles = [(field, prefs._meta.get_field(field).verbose_name, getattr(prefs, field))
               for field in NotificationPreference.KIND_FIELDS.values()]
    return render(request, "core/admin_notifications.html", {
        "groups": groups, "has_items": bool(items), "latest_id": qs.order_by("-id").values_list("id", flat=True).first() or 0,
        "pending_total": pending_parts + pending_leaves, "pending_parts": pending_parts,
        "pending_leaves": pending_leaves, "toggles": toggles,
    })


@require_POST
@role_required(User.Role.ADMIN)
def notification_decide(request, pk, decision):
    note = get_object_or_404(Notification, pk=pk, recipient=request.user)
    approve = decision == "approve"
    try:
        if note.kind == Notification.Kind.PART_REQUEST and note.part_request_id:
            decide_part_request(note.part_request, request.user, approve, request=request)
            messages.success(request, "Parça talebi " + ("onaylandı; parça stoktan düşüldü." if approve else "reddedildi."))
        elif note.kind == Notification.Kind.LEAVE and note.leave_id:
            decide_leave(note.leave, request.user, approve, request=request)
            messages.success(request, "İzin talebi " + ("onaylandı." if approve else "reddedildi."))
        else:
            messages.error(request, "Bu bildirim için onay işlemi yok.")
    except StockError as exc:
        messages.error(request, str(exc))
    if note.read_at is None:
        note.read_at = timezone.now()
        note.save(update_fields=["read_at"])
    return redirect("core:notifications_list")


@require_POST
@role_required(User.Role.ADMIN)
def notification_pref(request):
    field = request.POST.get("name", "")
    if field not in NotificationPreference.KIND_FIELDS.values():
        return JsonResponse({"ok": False}, status=400)
    pref, _ = NotificationPreference.objects.get_or_create(user=request.user)
    setattr(pref, field, request.POST.get("value") in ("1", "true", "on"))
    pref.save(update_fields=[field])
    return JsonResponse({"ok": True})


# --- Admin › Stok Depo ------------------------------------------------------------
def _week_start():
    now = timezone.localtime()
    return (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


@role_required(User.Role.ADMIN)
def stock_list(request):
    parts = list(StockPart.objects.filter(is_active=True).order_by("id"))
    week_in = (StockMovement.objects.filter(part__is_active=True, movement_type=StockMovement.MovementType.IN,
                                            created_at__gte=_week_start()).aggregate(total=Sum("quantity"))["total"] or 0)
    return render(request, "core/stock_list.html", {
        "parts": parts, "part_count": len(parts), "critical_count": sum(1 for p in parts if p.is_critical),
        "week_in": week_in,
    })


@role_required(User.Role.ADMIN)
def stock_detail(request, pk):
    part = get_object_or_404(StockPart, pk=pk, is_active=True)
    form = StockEntryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        qty = form.cleaned_data["quantity"]
        add_stock(part, qty, request.user, form.cleaned_data["note"], request=request)
        messages.success(request, f"«{part.name}» için {qty} adet stok girişi yapıldı. Yeni stok: {part.quantity} adet.")
        return redirect("core:stock_detail", pk=part.pk)
    movements = list(part.movements.select_related("created_by", "device")[:100])
    return render(request, "core/stock_detail.html", {
        "part": part, "form": form, "movements": movements, "last_move": movements[0] if movements else None,
    })


@role_required(User.Role.ADMIN)
def stock_create(request):
    form = StockPartForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        part = StockPart.objects.create(code=data["code"], name=data["name"], critical_level=data["critical_level"],
                                        unit_price=data["unit_price"])
        if data["quantity"]:
            add_stock(part, data["quantity"], request.user, "Başlangıç stoğu", request=request)
        log_action(AuditLog.Action.CREATE, user=request.user, request=request, target=part,
                   description=f"Stok parçası eklendi: {part.code} - {part.name}")
        messages.success(request, f"«{part.name}» stok depoya eklendi.")
        return redirect("core:stock_list")
    return render(request, "core/stock_form.html", {"form": form, "is_edit": False, "part": None})


@role_required(User.Role.ADMIN)
def stock_edit(request, pk):
    part = get_object_or_404(StockPart, pk=pk, is_active=True)
    form = StockPartForm(request.POST or None, instance=part)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        part.code, part.name = data["code"], data["name"]
        part.critical_level, part.unit_price = data["critical_level"], data["unit_price"]
        part.save(update_fields=["code", "name", "critical_level", "unit_price", "updated_at"])
        adjust_stock(part, data["quantity"], request.user, request=request)
        log_action(AuditLog.Action.UPDATE, user=request.user, request=request, target=part,
                   description=f"Stok parçası güncellendi: {part.code} - {part.name}")
        messages.success(request, f"«{part.name}» güncellendi.")
        return redirect("core:stock_detail", pk=part.pk)
    return render(request, "core/stock_form.html", {"form": form, "is_edit": True, "part": part})


@require_POST
@role_required(User.Role.ADMIN)
def stock_delete(request, pk):
    part = get_object_or_404(StockPart, pk=pk, is_active=True)
    name = part.name
    if delete_or_archive(part, request.user, request=request) == "archived":
        messages.success(request, f"«{name}» cihaz/talep geçmişinde kullanıldığı için silinmek yerine arşivlendi; listeden kaldırıldı.")
    else:
        messages.success(request, f"«{name}» stok depodan silindi.")
    return redirect("core:stock_list")


# --- Personel (admin + teknisyen) yönetimi -----------------------------------
STAFF_ROLES = (User.Role.ADMIN, User.Role.TECHNICIAN)


@role_required(User.Role.ADMIN)
def staff_list(request):
    """Personel Yönetimi: teknisyen tablosu (iş yükü, durum, izin) + izin takvimi."""
    today = timezone.localdate()
    rows = technician_rows(today)
    return render(request, "core/staff_list.html", {
        "rows": rows, "leaves": leave_calendar(today), "technician_count": len(rows),
    })


def _technician_or_404(pk):
    return get_object_or_404(User, pk=pk, role=User.Role.TECHNICIAN)


def _editable_leave(pk):
    """Düzenlenebilir / iptal edilebilir izin: onaylı ve henüz bitmemiş."""
    leave = get_object_or_404(TechnicianLeave.objects.select_related("technician"), pk=pk,
                              status=TechnicianLeave.Status.APPROVED, technician__role=User.Role.TECHNICIAN)
    if leave.end_date < timezone.localdate():
        raise Http404
    return leave


@role_required(User.Role.ADMIN)
def staff_leave_create(request, pk):
    person = _technician_or_404(pk)
    if not person.is_active:
        messages.error(request, f"{person.display_name} pasif; önce hesabı aktifleştirin.")
        return redirect("core:staff_list")
    form = LeaveForm(request.POST or None, technician=person)
    if request.method == "POST" and form.is_valid():
        leave = grant_leave(person, request.user, form.cleaned_data, request=request)
        messages.success(request, f"{person.display_name} için {leave.get_leave_type_display().lower()} tanımlandı "
                                  f"({leave.start_date:%d.%m.%Y} – {leave.end_date:%d.%m.%Y}).")
        jobs = open_job_count(person)
        if jobs:
            messages.warning(request, f"{person.display_name} adlı teknisyenin {jobs} açık işi var.")
        return redirect("core:staff_list")
    return render(request, "core/staff_leave_form.html", {"form": form, "person": person, "leave": None})


@role_required(User.Role.ADMIN)
def staff_leave_edit(request, pk):
    leave = _editable_leave(pk)
    person = leave.technician
    initial = {"leave_type": leave.leave_type, "start_date": leave.start_date, "end_date": leave.end_date,
               "description": leave.description}
    form = LeaveForm(request.POST or None, initial=initial, technician=person, exclude_pk=leave.pk)
    if request.method == "POST" and form.is_valid():
        update_leave(leave, request.user, form.cleaned_data, request=request)
        messages.success(request, f"{person.display_name} için izin güncellendi.")
        return redirect("core:staff_list")
    return render(request, "core/staff_leave_form.html", {"form": form, "person": person, "leave": leave})


@require_POST
@role_required(User.Role.ADMIN)
def staff_leave_cancel(request, pk):
    leave = _editable_leave(pk)
    cancel_leave(leave, request.user, request=request)
    messages.success(request, f"{leave.technician.display_name} için {leave.start_date:%d.%m.%Y} – "
                              f"{leave.end_date:%d.%m.%Y} izni iptal edildi.")
    return redirect("core:staff_list")


@require_POST
@role_required(User.Role.ADMIN)
def staff_delete(request, pk):
    person = _technician_or_404(pk)
    name = person.display_name
    jobs = open_job_count(person)
    try:
        result = delete_or_archive_staff(person, request.user, request=request)
    except accounts.DirectoryError:
        messages.error(request, DIRECTORY_DOWN)
        return redirect("core:staff_list")
    if result == "deleted":
        messages.success(request, f"{name} silindi.")
    elif result == "archived":
        messages.success(request, f"{name} geçmiş kayıtları olduğu için silinmek yerine pasife alındı; geçmiş kayıtları korunur.")
        if jobs:
            messages.warning(request, f"{name} adlı teknisyenin {jobs} açık işi var.")
    else:
        messages.info(request, f"{name} geçmiş kayıtları olduğu için kalıcı olarak silinemez; hesap zaten pasif.")
    return redirect("core:staff_list")


@role_required(User.Role.ADMIN)
def staff_create(request):
    form = StaffCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        person = form.save(commit=False)  # role formdan gelir (admin / teknisyen)
        if person.role == User.Role.ADMIN:
            person.is_staff = True        # varsayılan admin gibi tam yetkili
            person.is_superuser = True
        try:
            sent = provision_user(person, created_by=request.user, request=request)
        except accounts.DirectoryConflict as exc:
            form.add_error("email" if exc.field == "mail" else None, str(exc))
            return render(request, "core/staff_form.html", {"form": form})
        except accounts.DirectoryError as exc:
            form.add_error(None, f"Hesap oluşturulamadı: {exc}")
            return render(request, "core/staff_form.html", {"form": form})
        label = person.get_role_display()
        if sent:
            messages.success(request, f"{person.display_name} ({label}) eklendi. Kullanıcı adı ({person.username}) "
                                      f"ve geçici şifre {person.email} adresine gönderildi.")
        else:
            messages.warning(request, f"{person.display_name} ({label}) eklendi ancak e-posta gönderilemedi. "
                                      "Listeden «Bilgileri yeniden gönder» ile tekrar deneyin.")
        return redirect("core:staff_list")
    return render(request, "core/staff_form.html", {"form": form})


@require_POST
@role_required(User.Role.ADMIN)
def staff_resend(request, pk):
    person = get_object_or_404(User, pk=pk, role__in=STAFF_ROLES)
    if person.pk == request.user.pk:
        messages.error(request, "Kendi hesabınız için geçici şifre gönderemezsiniz.")
        return redirect("core:staff_list")
    try:
        sent = reset_credentials(person, by=request.user, request=request)
    except accounts.DirectoryError:
        messages.error(request, DIRECTORY_DOWN)
        return redirect("core:staff_list")
    if sent:
        messages.success(request, f"{person.display_name} için yeni geçici şifre e-postayla gönderildi.")
    else:
        messages.error(request, "E-posta gönderilemedi. E-posta ayarlarını kontrol edip tekrar deneyin.")
    return redirect("core:staff_list")


@require_POST
@role_required(User.Role.ADMIN)
def staff_toggle(request, pk):
    person = get_object_or_404(User, pk=pk, role__in=STAFF_ROLES)
    if person.pk == request.user.pk:
        messages.error(request, "Kendi hesabınızı pasifleştiremezsiniz.")
        return redirect("core:staff_list")
    person.is_active = not person.is_active
    person.save(update_fields=["is_active"])
    log_action(AuditLog.Action.UPDATE, user=request.user, request=request, target=person,
               description=f"Hesap {'aktifleştirildi' if person.is_active else 'pasifleştirildi'}: {person.username}")
    messages.success(request, f"{person.display_name} hesabı {'aktif' if person.is_active else 'pasif'} yapıldı.")
    return redirect("core:staff_list")


# --- Yasal sayfalar (giriş gerektirmez) -------------------------------------
def privacy(request):
    return render(request, "core/privacy.html")


def terms(request):
    return render(request, "core/terms.html")


# --- Müşteri yasal sayfaları (giriş gerektirmez) ------------------------------
def customer_privacy(request):
    return render(request, "core/customer_privacy.html")


def customer_terms(request):
    return render(request, "core/customer_terms.html")
