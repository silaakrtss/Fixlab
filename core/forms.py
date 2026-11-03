import re
from decimal import Decimal, InvalidOperation

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.signals import user_login_failed
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import Q

from django.utils import timezone

from . import accounts
from .models import StockPart, TechnicianLeave, User, phone_validator
from .phone import PHONE_ERROR, PhoneField, normalize_phone


def clean_person_name(value, label):
    """Ad / soyad alanı: yalnızca harf (Türkçe harfler dahil) ve kelimeler arası boşluk kabul edilir.
    Rakam, sembol, nokta, tire vb. reddedilir; art arda boşluklar teke indirilir."""
    value = " ".join((value or "").split())
    if value and not all(ch.isalpha() or ch == " " for ch in value):
        raise ValidationError(f"{label} yalnızca harflerden oluşmalıdır (rakam ve özel karakter kullanılamaz).")
    return value


class DirectoryAwareLoginMixin:
    """LDAP sunucusuna ulaşılamadığında «şifre hatalı» yerine gerçek nedeni gösterir."""

    def get_invalid_login_error(self):
        if getattr(self.request, "directory_unavailable", False):
            return ValidationError("Kimlik doğrulama sunucusuna şu anda ulaşılamıyor. Lütfen biraz sonra tekrar deneyin.",
                                   code="directory_unavailable")
        return super().get_invalid_login_error()


class LoginForm(DirectoryAwareLoginMixin, AuthenticationForm):
    """Personel girişi: e-posta + şifre. Kullanıcı adıyla giriş yalnızca ilk (varsayılan) admin içindir."""

    username = forms.CharField(
        label="E-posta",
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username",
                                      "placeholder": "E-posta", "required": True}),
    )
    password = forms.CharField(
        label="Şifre", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "placeholder": "Şifre", "required": True}),
    )
    error_messages = {
        "invalid_login": "E-posta veya şifre hatalı.",
        "inactive": "Bu hesap pasif durumda. Lütfen yöneticinizle iletişime geçin.",
        "not_staff": "Bu giriş personel içindir. Müşteri girişini kullanın.",
    }

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.role == User.Role.CUSTOMER:
            # Şifre doğru olsa bile müşteri hesabı personel ekranından oturum açamaz.
            raise ValidationError(self.error_messages["not_staff"], code="not_staff")

    def clean_username(self):
        """E-posta girildiyse hesabın kullanıcı adına çevrilir; böylece LDAP açık/kapalı her iki modda da çalışır.

        Hesap Django'da bulunamazsa değer olduğu gibi bırakılır (LDAP kaydı doğrudan e-postayla da aranır).
        Kullanıcı adıyla giriş yalnızca ilk (varsayılan) admin için açıktır; diğer herkes e-posta kullanır."""
        value = (self.cleaned_data.get("username") or "").strip()
        self._identifier_allowed = "@" in value or value.lower() == settings.DEFAULT_ADMIN_USERNAME.lower()
        if "@" in value:
            found = User.objects.filter(email__iexact=value).values_list("username", flat=True).first()
            if found:
                return found
        return value

    def clean(self):
        if getattr(self, "_identifier_allowed", True) is False:
            # E-posta olmayan (ve ilk admin dışında) bir kullanıcı adı: şifre denenmeden reddedilir.
            user_login_failed.send(sender=__name__, credentials={"username": self.cleaned_data.get("username", "")},
                                   request=self.request)
            raise self.get_invalid_login_error()
        return super().clean()


class StaffCreateForm(forms.ModelForm):
    """Admin panelinden admin veya teknisyen oluşturma.
    Kullanıcı adı ve geçici şifre sistem tarafından üretilir."""

    role = forms.ChoiceField(
        label="Hesap türü",
        choices=[(User.Role.TECHNICIAN, "Teknisyen"), (User.Role.ADMIN, "Admin")],
        initial=User.Role.TECHNICIAN,
        help_text="Admin tüm yönetim yetkisine sahiptir. Teknisyen yalnızca kendi panelini görür.",
    )

    class Meta:
        model = User
        fields = ["role", "first_name", "last_name", "email", "phone", "specialization"]
        labels = {"first_name": "Ad", "last_name": "Soyad", "email": "E-posta", "phone": "Telefon",
                  "specialization": "Uzmanlık alanı"}
        widgets = {
            "first_name": forms.TextInput(attrs={"maxlength": 60, "autocomplete": "off", "data-name-only": "1"}),
            "last_name": forms.TextInput(attrs={"maxlength": 60, "autocomplete": "off", "data-name-only": "1"}),
            "email": forms.EmailInput(attrs={"placeholder": "kisi@sirket.com"}),
            "phone": forms.TextInput(attrs={"placeholder": "(5XX) XXX XX XX", "inputmode": "tel",
                                            "pattern": r"\+?[0-9\s\-()]{10,20}", "maxlength": 20}),
            "specialization": forms.TextInput(attrs={"placeholder": "Örn. Anakart onarımı", "maxlength": 100}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("first_name", "last_name", "email"):
            self.fields[name].required = True
            self.fields[name].widget.attrs["required"] = True
        self.fields["phone"].required = False

    def clean_first_name(self):
        return clean_person_name(self.cleaned_data["first_name"], "Ad")

    def clean_last_name(self):
        return clean_person_name(self.cleaned_data["last_name"], "Soyad")

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if not phone:
            return ""
        formatted = normalize_phone(phone)
        if formatted is None:
            raise ValidationError(PHONE_ERROR, code="invalid")
        return formatted

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("Bu e-posta adresiyle kayıtlı bir kullanıcı zaten var.")
        return email

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("role") == User.Role.ADMIN:
            cleaned["specialization"] = ""  # uzmanlık yalnızca teknisyen için
        return cleaned


class LeaveForm(forms.Form):
    """Admin: teknisyene izin ver / mevcut izni düzenle."""

    leave_type = forms.ChoiceField(label="İzin türü", choices=TechnicianLeave.LeaveType.choices,
                                   initial=TechnicianLeave.LeaveType.ANNUAL)
    start_date = forms.DateField(label="Başlangıç tarihi", input_formats=["%Y-%m-%d"],
                                 widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                                 error_messages={"required": "Başlangıç tarihini seçin.", "invalid": "Geçerli bir tarih girin."})
    end_date = forms.DateField(label="Bitiş tarihi", input_formats=["%Y-%m-%d"],
                               widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                               error_messages={"required": "Bitiş tarihini seçin.", "invalid": "Geçerli bir tarih girin."})
    description = forms.CharField(label="Açıklama", required=False, max_length=500,
                                  widget=forms.Textarea(attrs={"rows": 3, "maxlength": 500}))

    def __init__(self, *args, technician=None, exclude_pk=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.technician = technician
        self.exclude_pk = exclude_pk

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end:
            if end < start:
                self.add_error("end_date", "Bitiş tarihi başlangıç tarihinden önce olamaz.")
            elif end < timezone.localdate():
                self.add_error("end_date", "Geçmiş tarihli izin eklenemez.")
            elif self.technician is not None:
                from .staffing import find_overlap
                clash = find_overlap(self.technician, start, end, exclude_pk=self.exclude_pk)
                if clash:
                    raise ValidationError(
                        f"{self.technician.display_name} için bu tarihlerle çakışan bir izin kaydı var "
                        f"({clash.start_date:%d.%m.%Y} – {clash.end_date:%d.%m.%Y}).")
        return cleaned


class ForcedPasswordChangeForm(PasswordChangeForm):
    """İlk giriş şifre değiştirme: geçici şifre + yeni şifre (Django doğrulayıcılarıyla)."""

    field_order = ["old_password", "new_password1", "new_password2"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["old_password"].label = "Geçici şifre"
        self.fields["new_password1"].label = "Yeni şifre"
        self.fields["new_password2"].label = "Yeni şifre (tekrar)"
        self.error_messages = {**self.error_messages,
                               "password_incorrect": "Geçici şifre hatalı. E-postanızdaki şifreyi girin.",
                               "password_mismatch": "Girilen şifreler eşleşmiyor."}
        for field in self.fields.values():
            field.widget.attrs["required"] = True
        self.fields["new_password1"].help_text = ""

    def clean_old_password(self):
        """Eski şifre LDAP açıkken dizinde doğrulanır (Django'daki şifre alanı kullanılamazdır)."""
        old_password = self.cleaned_data["old_password"]
        try:
            valid = accounts.verify_password(self.user, old_password)
        except accounts.DirectoryError:
            raise ValidationError("Kimlik doğrulama sunucusuna şu anda ulaşılamıyor. Lütfen biraz sonra tekrar deneyin.",
                                  code="directory_unavailable")
        if not valid:
            raise ValidationError(self.error_messages["password_incorrect"], code="password_incorrect")
        return old_password

    def save(self, commit=True):
        accounts.apply_password(self.user, self.cleaned_data["new_password1"])
        if commit:
            self.user.save()
        return self.user

    def clean_new_password1(self):
        password = self.cleaned_data.get("new_password1")
        old = self.data.get("old_password", "")
        if password and old and password == old:
            raise ValidationError("Yeni şifre geçici şifreyle aynı olamaz.")
        return password


class CustomerLoginForm(DirectoryAwareLoginMixin, AuthenticationForm):
    """Müşteri girişi: e-posta + şifre. Müşterinin kullanıcı adı e-posta adresidir."""

    username = forms.CharField(
        label="E-posta",
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email",
                                       "placeholder": "E-posta adresiniz", "required": True}),
    )
    password = forms.CharField(
        label="Şifre", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password",
                                          "placeholder": "Şifreniz", "required": True}),
    )
    error_messages = {
        "invalid_login": "E-posta veya şifre hatalı.",
        "inactive": "Bu hesap pasif durumda. Lütfen servisle iletişime geçin.",
        "not_customer": "Bu giriş müşteriler içindir. Personel girişini kullanın.",
        "unverified": "E-posta adresiniz henüz doğrulanmadı. Size gönderilen kodu girerek üyeliğinizi tamamlayın.",
    }

    def clean_username(self):
        return self.cleaned_data["username"].strip().lower()

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.role != User.Role.CUSTOMER:
            raise ValidationError(self.error_messages["not_customer"], code="not_customer")
        if not user.email_verified:
            # Şifre doğru olsa bile üyelik, e-postadaki kod girilene kadar tamamlanmış sayılmaz.
            raise ValidationError(self.error_messages["unverified"], code="unverified")


def stale_unverified_customer(email):
    """Aynı e-postayla başlatılmış ama hiç doğrulanmamış (ve hiç cihaz kaydı olmayan) müşteri hesabı.
    Böyle bir hesap, e-postanın gerçek sahibi yeniden üye olmak istediğinde yenisiyle değiştirilir."""
    return (User.objects.filter(role=User.Role.CUSTOMER, email_verified=False)
            .filter(Q(email__iexact=email) | Q(username__iexact=email))
            .exclude(devices__isnull=False).first())


class CustomerRegisterForm(forms.Form):
    first_name = forms.CharField(label="Ad", max_length=60, error_messages={"required": "Adınızı girin."})
    last_name = forms.CharField(label="Soyad", max_length=60, error_messages={"required": "Soyadınızı girin."})
    email = forms.EmailField(label="E-posta", error_messages={"required": "E-posta adresinizi girin.",
                                                              "invalid": "Geçerli bir e-posta adresi girin."})
    phone = PhoneField(label="Telefon", required=False)
    password1 = forms.CharField(label="Şifre", strip=False, error_messages={"required": "Bir şifre belirleyin."})
    password2 = forms.CharField(label="Şifre tekrar", strip=False,
                                error_messages={"required": "Şifrenizi tekrar girin."})
    accept_terms = forms.BooleanField(
        label="Kullanım Şartları",
        error_messages={"required": "Üye olmak için Kullanım Şartları'nı kabul etmelisiniz."},
    )

    def clean_first_name(self):
        return clean_person_name(self.cleaned_data["first_name"], "Ad")

    def clean_last_name(self):
        return clean_person_name(self.cleaned_data["last_name"], "Soyad")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if stale_unverified_customer(email) is not None:
            return email  # doğrulanmamış eski deneme: kayıt sırasında yenisiyle değiştirilir
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
            raise ValidationError("Bu e-posta adresiyle kayıtlı bir hesap zaten var. Giriş yapmayı deneyin.")
        if accounts.ldap_enabled():
            try:
                if accounts.get_directory().find(email) is not None:
                    raise ValidationError("Bu e-posta adresiyle kayıtlı bir hesap zaten var. Giriş yapmayı deneyin.")
            except accounts.DirectoryError:
                pass  # dizine ulaşılamıyorsa kayıt sırasında anlaşılır bir hata gösterilir
        return email

    def clean_password1(self):
        password = self.cleaned_data.get("password1", "")
        if len(password) < 12:
            raise ValidationError("Şifre en az 12 karakter olmalıdır.")
        return password

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Girilen şifreler eşleşmiyor.")
        elif p1 and "password1" not in self.errors:
            candidate = User(username=cleaned.get("email", ""), email=cleaned.get("email", ""),
                             first_name=cleaned.get("first_name", ""), last_name=cleaned.get("last_name", ""))
            try:
                validate_password(p1, candidate)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned

    def save(self):
        data = self.cleaned_data
        email = User.objects.normalize_email(data["email"])
        stale = stale_unverified_customer(email)
        if stale is not None:
            accounts.delete_account(stale)  # LDAP açıkken dizin kaydı da silinir
            stale.delete()
        # Üyelik, e-postaya gelen kod girilene kadar tamamlanmaz (email_verified=False → giriş yapılamaz).
        customer = User(username=email, email=email, first_name=data["first_name"], last_name=data["last_name"],
                        phone=data["phone"].strip(), role=User.Role.CUSTOMER, is_temporary_password=False,
                        email_verified=False)
        return accounts.create_account(customer, data["password1"])  # LDAP açıkken şifre dizine yazılır


class CustomerProfileForm(forms.Form):
    """Müşteri profili: ad, soyad, telefon ve adres düzenlenir. Adres alanları zorunlu değildir.
    E-posta giriş kimliği olduğu için buradan değiştirilemez."""

    first_name = forms.CharField(label="Ad", max_length=60, error_messages={"required": "Adınızı girin."})
    last_name = forms.CharField(label="Soyad", max_length=60, error_messages={"required": "Soyadınızı girin."})
    phone = PhoneField(label="Telefon", required=False)
    city = forms.CharField(label="İl", max_length=60, required=False)
    district = forms.CharField(label="İlçe", max_length=60, required=False)
    address = forms.CharField(label="Açık adres", max_length=500, required=False, widget=forms.Textarea)

    def clean_first_name(self):
        return clean_person_name(self.cleaned_data["first_name"], "Ad")

    def clean_last_name(self):
        return clean_person_name(self.cleaned_data["last_name"], "Soyad")

    def clean_city(self):
        return " ".join((self.cleaned_data.get("city") or "").split())

    def clean_district(self):
        return " ".join((self.cleaned_data.get("district") or "").split())

    def clean_address(self):
        return (self.cleaned_data.get("address") or "").strip()

    def apply_to(self, user):
        for name in ("first_name", "last_name", "phone", "city", "district", "address"):
            setattr(user, name, self.cleaned_data[name])
        return user


class ForgotForm(forms.Form):
    """Şifre sıfırlama 1. adım: hesaba kayıtlı e-posta (müşteri ve personel akışında ortak)."""

    email = forms.EmailField(label="E-posta adresi", error_messages={
        "required": "E-posta adresinizi girin.", "invalid": "Geçerli bir e-posta adresi girin."})

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


CustomerForgotForm = ForgotForm  # eski ad


class ResetCodeForm(forms.Form):
    """Şifre sıfırlama 2. adım: yalnızca 6 haneli kod. Doğruluğu view içinde (hesap bilgisiyle) denetlenir."""

    code = forms.RegexField(regex=r"^\d{6}$", label="6 haneli e-posta kodu", error_messages={
        "required": "6 haneli kodu girin.", "invalid": "Kod 6 haneli bir sayı olmalıdır."})

    def clean_code(self):
        return self.cleaned_data["code"].strip()


class ResetPasswordForm(forms.Form):
    """Şifre sıfırlama 3. adım: kod doğrulandıktan sonra yeni şifre."""

    password1 = forms.CharField(label="Yeni şifre", strip=False, error_messages={"required": "Yeni şifrenizi girin."})
    password2 = forms.CharField(label="Yeni şifre tekrar", strip=False,
                                error_messages={"required": "Yeni şifrenizi tekrar girin."})

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password1(self):
        password = self.cleaned_data.get("password1", "")
        if len(password) < 12:
            raise ValidationError("Şifre en az 12 karakter olmalıdır.")
        return password

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Girilen şifreler eşleşmiyor.")
        elif p1 and "password1" not in self.errors and self.user is not None:
            try:
                validate_password(p1, self.user)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned


# --------------------------------------------------------------------------
# Müşteri portalı: cihaz ön kaydı
# --------------------------------------------------------------------------
from django.conf import settings  # noqa: E402

from .models import Device, DeviceAttachment  # noqa: E402

BRAND_CHOICES = [(b, b) for b in ("ASUS", "Acer", "Apple", "Casper", "Dell", "HP", "Huawei", "Lenovo",
                                  "Monster", "MSI", "Samsung", "Xiaomi")] + [("__other__", "Diğer")]
CARGO_COMPANIES = ["Yurtiçi Kargo", "Aras Kargo", "MNG Kargo", "PTT Kargo", "Sürat Kargo", "UPS", "HepsiJet", "Diğer"]
PHOTO_EXT = {"jpg", "jpeg", "png"}
VIDEO_EXT = {"mp4", "mov"}


def classify_upload(upload):
    """Dosya uzantısına göre 'photo' / 'video' döndürür; desteklenmiyorsa None."""
    ext = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else ""
    if ext in PHOTO_EXT:
        return DeviceAttachment.Kind.PHOTO
    if ext in VIDEO_EXT:
        return DeviceAttachment.Kind.VIDEO
    return None


class DeviceRegistrationForm(forms.Form):
    device_type = forms.ChoiceField(label="Cihaz türü", choices=Device.DeviceType.choices,
                                    initial=Device.DeviceType.LAPTOP,
                                    error_messages={"required": "Cihaz türünü seçin."})
    brand = forms.ChoiceField(label="Marka", choices=[("", "Marka seçin")] + BRAND_CHOICES,
                              error_messages={"required": "Markayı seçin."})
    brand_other = forms.CharField(label="Marka adı", max_length=80, required=False)
    model = forms.CharField(label="Model", max_length=120, error_messages={"required": "Cihaz modelini girin."})
    serial_number = forms.CharField(label="Seri numarası", max_length=100, required=False)

    issue_title = forms.CharField(label="Arıza başlığı", max_length=150,
                                  error_messages={"required": "Arıza başlığını girin."})
    issue_description = forms.CharField(label="Ayrıntılı arıza açıklaması", widget=forms.Textarea,
                                        error_messages={"required": "Arızayı ayrıntılı açıklayın."})

    delivery_method = forms.ChoiceField(label="Teslim yöntemi", choices=Device.DeliveryMethod.choices,
                                        initial=Device.DeliveryMethod.SELF, widget=forms.RadioSelect,
                                        error_messages={"required": "Teslim yöntemini seçin."})
    contact_phone = PhoneField(label="İletişim telefonu", required=False)

    return_recipient = forms.CharField(label="Alıcı", max_length=150, required=False)
    return_phone = PhoneField(label="Telefon", required=False)
    return_city = forms.CharField(label="İl", max_length=60, required=False)
    return_district = forms.CharField(label="İlçe", max_length=60, required=False)
    return_address = forms.CharField(label="Açık adres", required=False, widget=forms.Textarea)

    def clean_brand_other(self):
        return self.cleaned_data["brand_other"].strip()

    def clean_return_recipient(self):
        return clean_person_name(self.cleaned_data.get("return_recipient", ""), "Alıcı adı")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("brand") == "__other__" and not cleaned.get("brand_other"):
            self.add_error("brand_other", "Marka adını girin.")
        if cleaned.get("delivery_method") == Device.DeliveryMethod.CARGO:
            labels = {"return_recipient": "Alıcı adını", "return_phone": "Alıcı telefonunu", "return_city": "İli",
                      "return_district": "İlçeyi", "return_address": "Açık adresi"}
            for name, label in labels.items():
                if not (cleaned.get(name) or "").strip():
                    self.add_error(name, f"{label} girin.")
        return cleaned

    @staticmethod
    def validate_files(uploads):
        """Yüklenen dosyaların listesini denetler; hata metinlerinin listesini döndürür."""
        errors = []
        if len(uploads) > settings.ATTACHMENT_MAX_FILES:
            errors.append(f"En fazla {settings.ATTACHMENT_MAX_FILES} dosya ekleyebilirsiniz.")
        for f in uploads:
            kind = classify_upload(f)
            if kind is None:
                errors.append(f"«{f.name}» desteklenmiyor. JPG, PNG, MP4 veya MOV yükleyin.")
                continue
            limit_mb = (settings.ATTACHMENT_MAX_PHOTO_MB if kind == DeviceAttachment.Kind.PHOTO
                        else settings.ATTACHMENT_MAX_VIDEO_MB)
            if f.size > limit_mb * 1024 * 1024:
                errors.append(f"«{f.name}» çok büyük (en fazla {limit_mb} MB).")
        return errors

    def save(self, user, uploads=(), actor=None):
        d = self.cleaned_data
        cargo = d["delivery_method"] == Device.DeliveryMethod.CARGO
        brand = d["brand_other"] if d["brand"] == "__other__" else d["brand"]
        phone = d["contact_phone"].strip() or user.phone or (d["return_phone"].strip() if cargo else "")
        device = Device(
            customer=user, customer_name=user.display_name, customer_phone=phone, customer_email=user.email,
            device_type=d["device_type"], brand=brand, model=d["model"].strip(),
            serial_number=d["serial_number"].strip(),
            issue_title=d["issue_title"].strip(), issue_description=d["issue_description"].strip(),
            delivery_method=d["delivery_method"],
            status=Device.Status.AWAITING_CARGO if cargo else Device.Status.PRE_REGISTERED,
            created_by=user,
        )
        if cargo:
            device.return_recipient = d["return_recipient"].strip()
            device.return_phone = d["return_phone"].strip()
            device.return_city = d["return_city"].strip()
            device.return_district = d["return_district"].strip()
            device.return_address = d["return_address"].strip()
        device._actor = actor or user
        device.save()
        for f in uploads:
            DeviceAttachment.objects.create(device=device, file=f, original_name=f.name[:255],
                                            kind=classify_upload(f), size=f.size)
        return device


class AdminDeviceRegistrationForm(forms.Form):
    """Admin › Cihazlar › Yeni cihaz kaydı: servise getirilen cihazı yönetici müşteri adına kaydeder.

    Kayıt «Teslim alındı» durumunda açılır; teknisyen seçilirse iş doğrudan teknisyenin paneline düşer,
    seçilmezse «Yeni kayıtlar» listesinde atama bekler. Takip kodu otomatik üretilir ve müşteriye e-postalanır.
    """

    customer_name = forms.CharField(label="Ad soyad", max_length=150,
                                    error_messages={"required": "Müşterinin ad soyadını girin."})
    customer_phone = PhoneField(label="Telefon", error_messages={"required": "Müşterinin telefonunu girin."})
    customer_email = forms.EmailField(label="E-posta", max_length=254,
                                      error_messages={"required": "Takip kodunun gönderileceği e-posta adresini girin.",
                                                      "invalid": "Geçerli bir e-posta adresi girin."})

    device_type = forms.ChoiceField(label="Kategori", choices=Device.DeviceType.choices,
                                    initial=Device.DeviceType.LAPTOP,
                                    error_messages={"required": "Cihaz kategorisini seçin."})
    brand = forms.CharField(label="Marka", max_length=80, error_messages={"required": "Markayı girin."})
    model = forms.CharField(label="Model", max_length=120, error_messages={"required": "Cihaz modelini girin."})
    serial_number = forms.CharField(label="Seri no", max_length=100, required=False)
    issue_description = forms.CharField(label="Müşterinin bildirdiği arıza", widget=forms.Textarea,
                                        error_messages={"required": "Müşterinin bildirdiği arızayı yazın."})
    technician = forms.CharField(label="Teknisyen ata", required=False)
    technician_note = forms.CharField(label="Teknisyene not", required=False, max_length=200,
                                      error_messages={"max_length": "Not en fazla 200 karakter olabilir."})

    def clean_customer_name(self):
        value = clean_person_name(self.cleaned_data.get("customer_name", ""), "Ad soyad")
        if not value:
            raise ValidationError("Müşterinin ad soyadını girin.")
        return value

    def clean_customer_email(self):
        return self.cleaned_data["customer_email"].strip().lower()

    def clean_brand(self):
        value = " ".join(self.cleaned_data.get("brand", "").split())
        if not value:
            raise ValidationError("Markayı girin.")
        return value

    def clean_model(self):
        value = " ".join(self.cleaned_data.get("model", "").split())
        if not value:
            raise ValidationError("Cihaz modelini girin.")
        return value

    def clean_issue_description(self):
        value = self.cleaned_data.get("issue_description", "").strip()
        if not value:
            raise ValidationError("Müşterinin bildirdiği arızayı yazın.")
        return value

    def clean_technician(self):
        """Boş = şimdilik atama yok. Dolu ise yalnızca atanabilir (aktif, izinde olmayan) bir teknisyen olabilir."""
        raw = (self.cleaned_data.get("technician") or "").strip()
        if not raw:
            return None
        from .staffing import assignable_technicians  # döngüsel içe aktarmayı önlemek için burada
        row = next((r for r in assignable_technicians() if str(r["tech"].pk) == raw), None)
        if row is None or not row["available"]:
            raise ValidationError("Seçilen teknisyene şu an iş atanamıyor. Listeden başka bir teknisyen seçin.")
        return row["tech"]

    validate_files = staticmethod(DeviceRegistrationForm.validate_files)

    def save(self, admin, uploads=()):
        """Cihaz kaydını oluşturur (takip kodu otomatik). E-posta gönderimini çağıran taraf yapar (_skip_email)."""
        d = self.cleaned_data
        # Aynı e-postayla kayıtlı bir müşteri hesabı varsa kayıt o hesaba bağlanır; müşteri kodu portalında da görür.
        account = User.objects.filter(role=User.Role.CUSTOMER, email__iexact=d["customer_email"]).first()
        device = Device(
            customer=account, customer_name=d["customer_name"], customer_phone=d["customer_phone"],
            customer_email=d["customer_email"], device_type=d["device_type"], brand=d["brand"], model=d["model"],
            serial_number=d["serial_number"].strip(), issue_description=d["issue_description"],
            delivery_method=Device.DeliveryMethod.SELF, status=Device.Status.RECEIVED, created_by=admin,
        )
        technician = d.get("technician")
        if technician is not None:
            device.technician = technician
            device._assign_note = (d.get("technician_note") or "").strip()  # teknisyenin bildirimine ve işlem kaydına eklenir
            device.clean()  # pasif / izinli teknisyen denetimi (Device.clean)
        device._actor = admin
        device._skip_email = True
        device._history_note = f"Kayıt yönetici tarafından servis kabulünde oluşturuldu ({admin.display_name})"
        device.save()
        for f in uploads:
            DeviceAttachment.objects.create(device=device, file=f, original_name=f.name[:255],
                                            kind=classify_upload(f), size=f.size)
        return device


class CargoSentForm(forms.Form):
    """Müşterinin «Kargoya verdim» bildirimi."""

    cargo_company = forms.ChoiceField(label="Kargo firması", choices=[(c, c) for c in CARGO_COMPANIES],
                                      error_messages={"required": "Kargo firmasını seçin."})
    cargo_tracking_code = forms.CharField(label="Kargo takip numarası", max_length=60,
                                          error_messages={"required": "Kargo takip numarasını girin."})

    def clean_cargo_tracking_code(self):
        return self.cleaned_data["cargo_tracking_code"].strip()


# --------------------------------------------------------------------------
# Admin › Stok Depo
# --------------------------------------------------------------------------
def parse_tr_money(raw):
    """«1.450,50», «1450,5», «1450.50», «180 ₺» gibi girişleri Decimal'e çevirir. Geçersizse ValueError."""
    text = re.sub(r"[₺\s]|TL", "", (raw or "").strip(), flags=re.I)
    if not text:
        return Decimal("0.00")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", text):  # 1.450 -> binlik ayracı
        text = text.replace(".", "")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError(text)
    if value < 0 or value >= Decimal("100000000") or value.as_tuple().exponent < -2:
        raise ValueError(text)
    return value.quantize(Decimal("0.01"))


class StockPartForm(forms.Form):
    """Yeni parça ve parça düzenleme formu (aynı sayfa)."""
    code = forms.CharField(label="Stok kodu", max_length=50,
                           widget=forms.TextInput(attrs={"placeholder": "Örn. USB-C-SKT", "autocomplete": "off"}),
                           error_messages={"required": "Stok kodu zorunlu."})
    name = forms.CharField(label="Parça adı", max_length=150,
                           widget=forms.TextInput(attrs={"placeholder": "Örn. USB-C şarj soketi", "autocomplete": "off"}),
                           error_messages={"required": "Parça adı zorunlu."})
    quantity = forms.IntegerField(label="Başlangıç adedi", required=False, min_value=0, max_value=1000000,
                                  widget=forms.NumberInput(attrs={"placeholder": "0", "min": "0"}),
                                  error_messages={"invalid": "Geçerli bir adet girin.", "min_value": "Adet eksi olamaz."})
    critical_level = forms.IntegerField(label="Minimum stok seviyesi", required=False, min_value=0, max_value=1000000,
                                        widget=forms.NumberInput(attrs={"placeholder": "1", "min": "0"}),
                                        error_messages={"invalid": "Geçerli bir seviye girin.", "min_value": "Seviye eksi olamaz."})
    unit_price = forms.CharField(label="Birim fiyat", required=False, max_length=20,
                                 widget=forms.TextInput(attrs={"placeholder": "0,00 ₺", "inputmode": "decimal", "autocomplete": "off"}))

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        if instance is not None:
            self.fields["quantity"].label = "Mevcut adet"
            if not self.is_bound:
                self.initial = {"code": instance.code, "name": instance.name, "quantity": instance.quantity,
                                "critical_level": instance.critical_level,
                                "unit_price": f"{instance.unit_price:.2f}".replace(".", ",")}

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()
        if not code:
            raise ValidationError("Stok kodu zorunlu.")
        clash = StockPart.objects.filter(code__iexact=code)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        other = clash.first()
        if other:
            raise ValidationError("Bu stok kodu arşivlenmiş bir parçaya ait. Farklı bir kod girin." if not other.is_active
                                  else "Bu stok koduyla kayıtlı bir parça zaten var.")
        return code

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise ValidationError("Parça adı zorunlu.")
        return name

    def clean_quantity(self):
        value = self.cleaned_data.get("quantity")
        if value is None:
            if self.instance is not None:
                raise ValidationError("Mevcut adedi girin.")
            return 0
        return value

    def clean_critical_level(self):
        value = self.cleaned_data.get("critical_level")
        return 1 if value is None else value

    def clean_unit_price(self):
        try:
            return parse_tr_money(self.cleaned_data.get("unit_price"))
        except ValueError:
            raise ValidationError("Geçerli bir fiyat girin (örn. 1.450,00).")


class StockEntryForm(forms.Form):
    """Parça detay sayfasındaki «Stok girişi» kutusu."""
    quantity = forms.IntegerField(label="Adet", min_value=1, max_value=1000000,
                                  widget=forms.NumberInput(attrs={"placeholder": "10", "min": "1"}),
                                  error_messages={"required": "Giriş adedini yazın.", "invalid": "Geçerli bir adet girin.",
                                                  "min_value": "Adet en az 1 olmalı."})
    note = forms.CharField(label="Not (isteğe bağlı)", required=False, max_length=255,
                           widget=forms.TextInput(attrs={"placeholder": "Tedarikçi siparişi"}))
