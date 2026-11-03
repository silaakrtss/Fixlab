import re
from decimal import Decimal

from django import template
from django.utils import timezone
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

register = template.Library()
MONTHS = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
_ID = re.compile(r"\b(?:TS-\d{4}-\d+|SRV-[A-Z0-9]+)\b")


@register.filter
def money(value):
    """1450 -> 1.450 · 1450.5 -> 1.450,50 (₺ işaretini şablon ekler)."""
    try:
        d = Decimal(value)
    except Exception:
        return value
    text = f"{d:,.0f}" if d == d.to_integral_value() else f"{d:,.2f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


@register.filter
def tr_day(value):
    """30 Eyl"""
    if not value:
        return "—"
    v = timezone.localtime(value)
    return f"{v.day} {MONTHS[v.month - 1]}"


@register.filter
def tr_stamp(value):
    """30 Eyl 10:05"""
    if not value:
        return "—"
    v = timezone.localtime(value)
    return f"{v.day} {MONTHS[v.month - 1]} {v:%H:%M}"


@register.filter
def hl_ids(text):
    """Metindeki takip kodlarını (SRV-…, TS-2026-…) vurgular; metin önce kaçışlanır."""
    safe = conditional_escape(text or "")
    return mark_safe(_ID.sub(lambda m: f'<b class="an-id">{m.group(0)}</b>', str(safe)))
