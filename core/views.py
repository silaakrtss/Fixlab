from functools import wraps

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import ForcedPasswordChangeForm, LoginForm, StaffCreateForm
from .models import AuditLog, Device, StockPart, TechnicianLeave, User
from .services import provision_user, reset_credentials
from .utils import log_action


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


@login_required
def home(request):
    return redirect(role_home_url(request.user))


@login_required
def force_password_change(request):
    user = request.user
    if not user.is_temporary_password:
        return redirect(role_home_url(user))

    form = ForcedPasswordChangeForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
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


@role_required(User.Role.TECHNICIAN)
def technician_dashboard(request):
    mine = Device.objects.filter(technician=request.user)
    done = ("delivered", "cancelled", "unrepairable")
    context = {
        "active_devices": mine.exclude(status__in=done).order_by("-priority", "received_at")[:10],
        "active_count": mine.exclude(status__in=done).count(),
        "ready_count": mine.filter(status=Device.Status.READY).count(),
        "pending_leaves": request.user.leaves.filter(status=TechnicianLeave.Status.PENDING).count(),
        # Görünüm için ek veriler (mevcut anahtarlar değişmedi)
        "repairing_count": mine.filter(status=Device.Status.REPAIRING).count(),
        "waiting_part_count": mine.filter(status=Device.Status.WAITING_PART).count(),
        "diagnosing_count": mine.filter(status=Device.Status.DIAGNOSING).count(),
        "device_cards": mine.order_by("-received_at")[:30],
    }
    return render(request, "core/technician_dashboard.html", context)


@role_required(User.Role.CUSTOMER)
def customer_home(request):
    return render(request, "core/customer_home.html")


# --- Personel (admin + teknisyen) yönetimi -----------------------------------
STAFF_ROLES = (User.Role.ADMIN, User.Role.TECHNICIAN)


@role_required(User.Role.ADMIN)
def staff_list(request):
    query = request.GET.get("q", "").strip()
    role_filter = request.GET.get("rol", "")
    base = User.objects.filter(role__in=STAFF_ROLES)
    staff = base.order_by("-created_at")
    if role_filter in STAFF_ROLES:
        staff = staff.filter(role=role_filter)
    if query:
        staff = staff.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query)
                             | Q(username__icontains=query) | Q(email__icontains=query))
    context = {
        "staff": staff, "query": query, "role_filter": role_filter,
        "all_count": base.count(),
        "admin_count": base.filter(role=User.Role.ADMIN).count(),
        "technician_count": base.filter(role=User.Role.TECHNICIAN).count(),
    }
    return render(request, "core/staff_list.html", context)


@role_required(User.Role.ADMIN)
def staff_create(request):
    form = StaffCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        person = form.save(commit=False)  # role formdan gelir (admin / teknisyen)
        if person.role == User.Role.ADMIN:
            person.is_staff = True        # varsayılan admin gibi tam yetkili
            person.is_superuser = True
        sent = provision_user(person, created_by=request.user, request=request)
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
    if reset_credentials(person, by=request.user, request=request):
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
