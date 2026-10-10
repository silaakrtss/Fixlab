from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import AuditLog
from .utils import log_action


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log_action(AuditLog.Action.LOGIN, user=user, request=request, target=user, description="Sisteme giriş yapıldı")


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user:
        log_action(AuditLog.Action.LOGOUT, user=user, request=request, target=user, description="Çıkış yapıldı")


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    log_action(AuditLog.Action.LOGIN_FAILED, request=request,
               username=str(credentials.get("username", ""))[:150], description="Başarısız giriş denemesi")
