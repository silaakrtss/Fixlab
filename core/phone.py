"""Telefon numarası biçimi: her yerde «(533) 305 54 24».

Kullanıcı numarayı nasıl yazarsa yazsın (0533 305 54 24, +90 533 305 5424, 5333055424 ...) kaydedilirken bu
biçime çevrilir. Tarayıcıda aynı kural static/core/js/phone.js ile yazarken uygulanır; asıl güvence burasıdır."""
import re

from django import forms
from django.core.exceptions import ValidationError

PHONE_EXAMPLE = "(533) 305 54 24"
PHONE_ERROR = f"Geçerli bir telefon numarası girin (örn. {PHONE_EXAMPLE})."


def normalize_phone(value):
    """Geçerli bir 10 haneli ulusal numara ise «(533) 305 54 24» döndürür, değilse None."""
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("00"):          # 0090 533 ...
        digits = digits[2:]
    if digits.startswith("90") and len(digits) > 10:   # +90 533 ... / 90 533 ...
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11:   # 0533 ...
        digits = digits[1:]
    if len(digits) != 10 or digits.startswith("0"):
        return None
    return f"({digits[:3]}) {digits[3:6]} {digits[6:8]} {digits[8:]}"


def pretty_phone(value):
    """Ekranda göstermek için: geçerliyse yeni biçim, değilse değer olduğu gibi (eski kayıtlar bozulmaz)."""
    return normalize_phone(value) or (value or "")


class PhoneField(forms.CharField):
    """Telefon alanı: girilen numarayı «(533) 305 54 24» biçimine çevirir; çevrilemiyorsa hata verir."""

    default_error_messages = {"invalid": PHONE_ERROR}

    def __init__(self, **kwargs):
        kwargs.setdefault("max_length", 20)
        super().__init__(**kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        if value in self.empty_values:
            return ""
        formatted = normalize_phone(value)
        if formatted is None:
            raise ValidationError(self.error_messages["invalid"], code="invalid")
        return formatted
