from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.core.exceptions import ValidationError

from .models import User


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label="Kullanıcı adı",
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username", "placeholder": "Kullanıcı Adı", "required": True}),
    )
    password = forms.CharField(
        label="Şifre", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "placeholder": "Şifre", "required": True}),
    )
    error_messages = {
        "invalid_login": "Kullanıcı adı veya şifre hatalı.",
        "inactive": "Bu hesap pasif durumda. Lütfen yöneticinizle iletişime geçin.",
    }


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
            "first_name": forms.TextInput(attrs={"maxlength": 60, "autocomplete": "off"}),
            "last_name": forms.TextInput(attrs={"maxlength": 60, "autocomplete": "off"}),
            "email": forms.EmailInput(attrs={"placeholder": "kisi@sirket.com"}),
            "phone": forms.TextInput(attrs={"placeholder": "0532 123 45 67", "inputmode": "tel",
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
        return self.cleaned_data["first_name"].strip()

    def clean_last_name(self):
        return self.cleaned_data["last_name"].strip()

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

    def clean_new_password1(self):
        password = self.cleaned_data.get("new_password1")
        old = self.data.get("old_password", "")
        if password and old and password == old:
            raise ValidationError("Yeni şifre geçici şifreyle aynı olamaz.")
        return password
