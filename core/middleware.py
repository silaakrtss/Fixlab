from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    """Geçici şifreli kullanıcı, kalıcı şifresini belirlemeden hiçbir sayfaya giremez."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_temporary_password:
            allowed = {reverse("core:force_password_change"), reverse("core:logout"),
                       reverse("core:privacy"), reverse("core:terms")}
            if request.path not in allowed and not request.path.startswith("/" + settings.STATIC_URL.lstrip("/")):
                return redirect("core:force_password_change")
        return self.get_response(request)
