from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (AuditLog, Device, DeviceAttachment, DevicePartUsage, DeviceStatusHistory, Notification,
                     NotificationPreference, PartRequest, StockMovement, StockPart, TechnicianLeave, User)
from . import accounts
from .services import provision_user


class UserProvisionForm(forms.ModelForm):
    """Django admin'de yeni kullanıcı: kullanıcı adı/şifre otomatik üretilir ve e-postalanır."""

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "phone", "role")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("first_name", "last_name", "email"):
            self.fields[name].required = True


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    add_form = UserProvisionForm
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("first_name", "last_name", "email", "phone", "role")}),)
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("FIXLAB", {"fields": ("role", "is_temporary_password", "phone", "specialization", "hired_at")}),
    )
    list_display = ("username", "get_full_name", "email", "role", "is_temporary_password", "is_active")
    list_filter = ("role", "is_active", "is_temporary_password")
    search_fields = ("username", "first_name", "last_name", "email")

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            try:
                accounts.update_account(obj)  # LDAP açıkken ad/e-posta/telefon/rol dizine de yazılır
            except accounts.DirectoryError as exc:
                self.message_user(request, f"Kayıt güncellendi ancak LDAP dizinine yazılamadı: {exc}", messages.ERROR)
            return
        try:
            sent = provision_user(obj, created_by=request.user, request=request)
        except accounts.DirectoryError as exc:
            self.message_user(request, f"Kullanıcı oluşturulamadı: {exc}", messages.ERROR)
            return
        level = messages.SUCCESS if sent else messages.WARNING
        self.message_user(request, f"Kullanıcı adı: {obj.username}. Giriş bilgisi e-postası "
                                   f"{'gönderildi' if sent else 'GÖNDERİLEMEDİ'}.", level)


class DevicePartUsageInline(admin.TabularInline):
    model = DevicePartUsage
    extra = 0


class DeviceAttachmentInline(admin.TabularInline):
    model = DeviceAttachment
    extra = 0
    fields = ("original_name", "kind", "size", "uploaded_at")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("tracking_id", "customer_name", "brand", "model", "status", "delivery_method", "technician", "received_at")
    list_filter = ("status", "delivery_method", "device_type", "priority", "technician")
    search_fields = ("tracking_id", "customer_name", "customer_phone", "serial_number", "brand", "model",
                     "cargo_tracking_code", "return_cargo_tracking_code")
    readonly_fields = ("tracking_id",)
    inlines = [DevicePartUsageInline, DeviceAttachmentInline]

    def save_model(self, request, obj, form, change):
        # Durum geçmişi ve müşteri bildirimi, değişikliği kimin yaptığını bilsin.
        obj._actor = request.user
        super().save_model(request, obj, form, change)


@admin.register(StockPart)
class StockPartAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "quantity", "critical_level", "unit_price", "is_active")
    search_fields = ("code", "name")
    list_filter = ("is_active", "category")


admin.site.register(StockMovement)
admin.site.register(DeviceStatusHistory)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "recipient", "title", "read_at")
    list_filter = ("read_at",)
    search_fields = ("title", "body", "recipient__username", "recipient__email")


@admin.register(TechnicianLeave)
class TechnicianLeaveAdmin(admin.ModelAdmin):
    list_display = ("technician", "leave_type", "start_date", "end_date", "status")
    list_filter = ("status", "leave_type")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "username_snapshot", "action", "target_type", "description", "ip_address")
    list_filter = ("action",)
    search_fields = ("username_snapshot", "description")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PartRequest)
class PartRequestAdmin(admin.ModelAdmin):
    """Teknisyen arayüzü hazır olana dek talepler buradan açılır; onay/ret yalnızca admin panelindeki
    Bildirimlerim ekranından yapılır (stok düşümü orada tutarlı yürür)."""
    list_display = ("created_at", "technician", "device", "part", "quantity", "status")
    list_filter = ("status",)
    readonly_fields = ("status", "reviewed_by", "reviewed_at")


admin.site.register(NotificationPreference)
