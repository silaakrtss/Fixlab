"""FIXLAB veritabanı modelleri.

Bu aşamada yalnızca kullanıcı/giriş akışı arayüze bağlıdır; cihaz, stok, izin ve
log modelleri ileride eklenecek modüller için şimdiden eksiksiz tanımlanmıştır.
"""
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

phone_validator = RegexValidator(
    regex=r"^\+?[0-9\s\-()]{10,20}$",
    message="Geçerli bir telefon numarası girin (örn. 0532 123 45 67).",
)


# --------------------------------------------------------------------------
# Kullanıcı
# --------------------------------------------------------------------------
class UserManager(DjangoUserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", User.Role.ADMIN)
        extra_fields.setdefault("is_temporary_password", False)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        TECHNICIAN = "technician", "Teknisyen"
        CUSTOMER = "customer", "Müşteri"

    email = models.EmailField("E-posta", unique=True)
    role = models.CharField("Rol", max_length=20, choices=Role.choices, default=Role.CUSTOMER)
    is_temporary_password = models.BooleanField(
        "Geçici şifre kullanıyor", default=True,
        help_text="Açıksa kullanıcı şifresini değiştirmeden başka sayfaya erişemez.",
    )
    phone = models.CharField("Telefon", max_length=20, blank=True, validators=[phone_validator])
    specialization = models.CharField("Uzmanlık alanı", max_length=100, blank=True)
    hired_at = models.DateField("İşe başlama tarihi", null=True, blank=True)
    created_at = models.DateTimeField("Oluşturulma", default=timezone.now, editable=False)

    objects = UserManager()

    class Meta:
        verbose_name = "Kullanıcı"
        verbose_name_plural = "Kullanıcılar"

    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.role = self.Role.ADMIN
        super().save(*args, **kwargs)

    @property
    def is_admin_role(self):
        return self.role == self.Role.ADMIN

    @property
    def is_technician(self):
        return self.role == self.Role.TECHNICIAN

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def avatar_letter(self):
        """Profil kartındaki baş harf (Türkçe i/ı doğru büyütülür)."""
        first = (self.display_name or "?")[:1]
        return {"i": "İ", "ı": "I"}.get(first, first.upper())

    def __str__(self):
        return f"{self.display_name} ({self.get_role_display()})"


# --------------------------------------------------------------------------
# Stok
# --------------------------------------------------------------------------
class StockPart(models.Model):
    name = models.CharField("Parça adı", max_length=150)
    code = models.CharField("Parça kodu", max_length=50, unique=True)
    category = models.CharField("Kategori", max_length=80, blank=True)
    quantity = models.PositiveIntegerField("Stok adedi", default=0)
    critical_level = models.PositiveIntegerField("Kritik seviye", default=5)
    unit_price = models.DecimalField(
        "Birim fiyat", max_digits=10, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    supplier = models.CharField("Tedarikçi", max_length=150, blank=True)
    shelf_location = models.CharField("Raf konumu", max_length=50, blank=True)
    is_active = models.BooleanField("Aktif", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Stok parçası"
        verbose_name_plural = "Stok parçaları"
        ordering = ["name"]

    @property
    def is_critical(self):
        return self.quantity <= self.critical_level

    def __str__(self):
        return f"{self.code} - {self.name}"


class StockMovement(models.Model):
    class MovementType(models.TextChoices):
        IN = "in", "Stok girişi"
        OUT = "out", "Stok çıkışı (cihaza kullanıldı)"
        RETURN = "return", "İade"
        ADJUST = "adjust", "Sayım düzeltmesi"

    part = models.ForeignKey(StockPart, on_delete=models.CASCADE, related_name="movements")
    movement_type = models.CharField("Hareket türü", max_length=10, choices=MovementType.choices)
    quantity = models.IntegerField("Adet", help_text="Düzeltmelerde eksi değer girilebilir.")
    device = models.ForeignKey("Device", null=True, blank=True, on_delete=models.SET_NULL, related_name="stock_movements")
    note = models.CharField("Not", max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Stok hareketi"
        verbose_name_plural = "Stok hareketleri"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.part.code} {self.get_movement_type_display()} {self.quantity}"


# --------------------------------------------------------------------------
# Cihaz kayıtları
# --------------------------------------------------------------------------
class Device(models.Model):
    class DeviceType(models.TextChoices):
        LAPTOP = "laptop", "Dizüstü bilgisayar"
        DESKTOP = "desktop", "Masaüstü bilgisayar"
        PHONE = "phone", "Telefon"
        TABLET = "tablet", "Tablet"
        PRINTER = "printer", "Yazıcı"
        TV = "tv", "Televizyon"
        CONSOLE = "console", "Oyun konsolu"
        OTHER = "other", "Diğer"

    class Status(models.TextChoices):
        RECEIVED = "received", "Teslim alındı"
        DIAGNOSING = "diagnosing", "İnceleniyor"
        WAITING_APPROVAL = "waiting_approval", "Müşteri onayı bekliyor"
        WAITING_PART = "waiting_part", "Parça bekliyor"
        REPAIRING = "repairing", "Tamirde"
        TESTING = "testing", "Test aşamasında"
        READY = "ready", "Teslime hazır"
        DELIVERED = "delivered", "Teslim edildi"
        UNREPAIRABLE = "unrepairable", "Onarılamaz"
        CANCELLED = "cancelled", "İptal edildi"

    class Priority(models.TextChoices):
        LOW = "low", "Düşük"
        NORMAL = "normal", "Normal"
        HIGH = "high", "Yüksek"
        URGENT = "urgent", "Acil"

    tracking_id = models.CharField("Takip no", max_length=20, unique=True, editable=False, blank=True)

    # Müşteri bilgileri (hesabı olmayan müşteriler için de tutulur)
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="devices", limit_choices_to={"role": "customer"},
    )
    customer_name = models.CharField("Müşteri adı soyadı", max_length=150)
    customer_phone = models.CharField("Müşteri telefonu", max_length=20, validators=[phone_validator])
    customer_email = models.EmailField("Müşteri e-postası", blank=True)

    # Cihaz bilgileri
    device_type = models.CharField("Cihaz tipi", max_length=20, choices=DeviceType.choices, default=DeviceType.OTHER)
    brand = models.CharField("Marka", max_length=80)
    model = models.CharField("Model", max_length=120)
    serial_number = models.CharField("Seri no", max_length=100, blank=True)
    accessories = models.CharField("Teslim alınan aksesuarlar", max_length=255, blank=True)
    issue_description = models.TextField("Arıza açıklaması")
    diagnosis_notes = models.TextField("Teknisyen tespit notları", blank=True)

    status = models.CharField("Durum", max_length=20, choices=Status.choices, default=Status.RECEIVED, db_index=True)
    priority = models.CharField("Öncelik", max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="assigned_devices", limit_choices_to={"role": "technician"},
        verbose_name="Atanan teknisyen",
    )

    # Maliyet bilgileri
    estimated_cost = models.DecimalField("Tahmini ücret", max_digits=10, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(0)])
    labor_cost = models.DecimalField("İşçilik ücreti", max_digits=10, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(0)])
    parts_cost = models.DecimalField("Parça ücreti", max_digits=10, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(0)])
    is_paid = models.BooleanField("Ödendi", default=False)
    warranty_months = models.PositiveSmallIntegerField("Garanti (ay)", default=0)

    received_at = models.DateTimeField("Kabul tarihi", default=timezone.now)
    estimated_delivery = models.DateField("Tahmini teslim", null=True, blank=True)
    delivered_at = models.DateTimeField("Teslim tarihi", null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cihaz"
        verbose_name_plural = "Cihazlar"
        ordering = ["-received_at"]

    @property
    def total_cost(self):
        return self.labor_cost + self.parts_cost

    def recalculate_parts_cost(self):
        total = sum((u.line_total for u in self.part_usages.all()), Decimal("0.00"))
        self.parts_cost = total
        self.save(update_fields=["parts_cost", "updated_at"])

    @classmethod
    def _next_tracking_id(cls):
        prefix = f"SRV-{timezone.now().year}-"
        last = cls.objects.filter(tracking_id__startswith=prefix).order_by("-tracking_id").first()
        number = int(last.tracking_id.rsplit("-", 1)[1]) + 1 if last else 1001
        return f"{prefix}{number}"

    def save(self, *args, **kwargs):
        if not self.tracking_id:
            self.tracking_id = self._next_tracking_id()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tracking_id} - {self.brand} {self.model}"


class DevicePartUsage(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="part_usages")
    part = models.ForeignKey(StockPart, on_delete=models.PROTECT, related_name="usages")
    quantity = models.PositiveIntegerField("Adet", default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField("Birim fiyat (kullanım anı)", max_digits=10, decimal_places=2, blank=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Cihazda kullanılan parça"
        verbose_name_plural = "Cihazda kullanılan parçalar"

    @property
    def line_total(self):
        return self.unit_price * self.quantity

    def save(self, *args, **kwargs):
        if self.unit_price is None:
            self.unit_price = self.part.unit_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.device.tracking_id}: {self.part.code} x{self.quantity}"


class DeviceStatusHistory(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="status_history")
    old_status = models.CharField(max_length=20, choices=Device.Status.choices, blank=True)
    new_status = models.CharField(max_length=20, choices=Device.Status.choices)
    note = models.CharField("Not", max_length=255, blank=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Durum geçmişi"
        verbose_name_plural = "Durum geçmişi"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.device.tracking_id}: {self.old_status} → {self.new_status}"


# --------------------------------------------------------------------------
# İzinler
# --------------------------------------------------------------------------
class TechnicianLeave(models.Model):
    class LeaveType(models.TextChoices):
        ANNUAL = "annual", "Yıllık izin"
        SICK = "sick", "Hastalık izni (rapor)"
        EXCUSE = "excuse", "Mazeret izni"
        UNPAID = "unpaid", "Ücretsiz izin"
        OTHER = "other", "Diğer"

    class Status(models.TextChoices):
        PENDING = "pending", "Onay bekliyor"
        APPROVED = "approved", "Onaylandı"
        REJECTED = "rejected", "Reddedildi"
        CANCELLED = "cancelled", "İptal edildi"

    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="leaves",
        limit_choices_to={"role": "technician"}, verbose_name="Teknisyen",
    )
    leave_type = models.CharField("İzin türü", max_length=10, choices=LeaveType.choices, default=LeaveType.ANNUAL)
    start_date = models.DateField("Başlangıç tarihi")
    end_date = models.DateField("Bitiş tarihi")
    description = models.TextField("Açıklama", blank=True)
    status = models.CharField("Durum", max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_note = models.CharField("Yönetici notu", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "İzin talebi"
        verbose_name_plural = "İzin talepleri"
        ordering = ["-created_at"]

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "Bitiş tarihi başlangıç tarihinden önce olamaz."})

    @property
    def duration_days(self):
        return (self.end_date - self.start_date).days + 1

    def __str__(self):
        return f"{self.technician.display_name}: {self.start_date} - {self.end_date}"


# --------------------------------------------------------------------------
# İşlem geçmişi
# --------------------------------------------------------------------------
class AuditLog(models.Model):
    class Action(models.TextChoices):
        LOGIN = "login", "Giriş"
        LOGIN_FAILED = "login_failed", "Başarısız giriş"
        LOGOUT = "logout", "Çıkış"
        CREATE = "create", "Oluşturma"
        UPDATE = "update", "Güncelleme"
        DELETE = "delete", "Silme"
        PASSWORD_CHANGE = "password_change", "Şifre değiştirme"
        CREDENTIALS_SENT = "credentials_sent", "Giriş bilgisi e-postası"
        STATUS_CHANGE = "status_change", "Durum değişikliği"
        STOCK_CHANGE = "stock_change", "Stok değişikliği"
        LEAVE = "leave", "İzin işlemi"
        OTHER = "other", "Diğer"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs")
    username_snapshot = models.CharField("Kullanıcı adı", max_length=150, blank=True)
    action = models.CharField("İşlem", max_length=20, choices=Action.choices, default=Action.OTHER, db_index=True)
    target_type = models.CharField("Hedef türü", max_length=50, blank=True)
    target_id = models.CharField("Hedef ID", max_length=50, blank=True)
    description = models.TextField("Açıklama", blank=True)
    ip_address = models.GenericIPAddressField("IP adresi", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "İşlem kaydı"
        verbose_name_plural = "İşlem kayıtları"
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.user and not self.username_snapshot:
            self.username_snapshot = self.user.username
        super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.created_at:%d.%m.%Y %H:%M}] {self.username_snapshot} - {self.get_action_display()}"
