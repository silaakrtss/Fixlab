from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("giris/", views.FixlabLoginView.as_view(), name="login"),
    path("cikis/", LogoutView.as_view(), name="logout"),
    path("sifre-degistir/", views.force_password_change, name="force_password_change"),
    path("panel/", views.admin_dashboard, name="admin_dashboard"),
    path("panel/personel/", views.staff_list, name="staff_list"),
    path("panel/personel/yeni/", views.staff_create, name="staff_create"),
    path("panel/personel/<int:pk>/bilgileri-gonder/", views.staff_resend, name="staff_resend"),
    path("panel/personel/<int:pk>/durum/", views.staff_toggle, name="staff_toggle"),
    path("teknisyen/", views.technician_dashboard, name="technician_dashboard"),
    path("musteri/", views.customer_home, name="customer_home"),
    path("gizlilik/", views.privacy, name="privacy"),
    path("sartlar/", views.terms, name="terms"),
]
