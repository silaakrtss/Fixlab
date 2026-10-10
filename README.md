# FIXLAB — Teknik Servis Yönetim Sistemi

Django tabanlı teknik servis yönetim sistemi. Bu aşamada şunlar hazırdır:

- Tüm veritabanı modelleri (kullanıcı, cihaz, stok, izin, işlem kaydı)
- Otomatik oluşturulan varsayılan admin
- Admin panelinden **admin veya teknisyen** oluşturma (otomatik kullanıcı adı + geçici şifre + e-posta)
- İlk girişte **zorunlu şifre değiştirme** akışı (admin ve teknisyen için)
- Panelde sol altta kullanıcı kartı (Profil / Çıkış Yap; profil sayfası henüz yazılmadı)
- Giriş ve panel sayfalarında **Gizlilik** ve **Şartlar** (KVKK Aydınlatma Metni, Kullanım ve Güvenlik Şartları)

## Varsayılan admin hesabı

İlk `migrate` komutuyla otomatik oluşturulur:

| Alan | Değer |
|------|-------|
| Kullanıcı adı | `admin` |
| Şifre | `FixLab#Admin2025` |
| E-posta | `admin@fixlab.local` |

> **Önemli:** Canlı ortamda bu şifreyi hemen değiştirin ya da kurulumdan önce
> `FIXLAB_ADMIN_USERNAME`, `FIXLAB_ADMIN_PASSWORD`, `FIXLAB_ADMIN_EMAIL`
> ortam değişkenleriyle kendi değerlerinizi verin. Şifreyi sıfırlamak için:
> `python manage.py create_default_admin --reset-password`

## Kurulum

```bash
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate            # tabloları ve varsayılan admini oluşturur
python manage.py runserver
```

Tarayıcıda `http://127.0.0.1:8000/` adresini açın ve yukarıdaki admin bilgileriyle giriş yapın.

Testleri çalıştırmak için: `python manage.py test`

## E-posta (geçici şifre bildirimi)

İki çalışma modu vardır, `.env` dosyası yoksa otomatik olarak birincisi kullanılır:

1. **Konsol modu (varsayılan):** `settings.py` içinde `EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'`
   kullanılır. E-postalar gerçekten gönderilmez, `runserver` çalışan terminale yazdırılır.
2. **Gmail (gerçek gönderim):** Proje klasöründe `.env` dosyası varsa ve içinde `EMAIL_HOST_USER` ile
   `EMAIL_HOST_PASSWORD` tanımlıysa e-postalar Gmail SMTP ile gönderilir.

### Gmail ile gerçek gönderim kurulumu

1. Gönderen Gmail hesabında **2 Adımlı Doğrulama**'yı açın: https://myaccount.google.com/security
2. https://myaccount.google.com/apppasswords adresinden "FIXLAB" adıyla bir **Uygulama şifresi** oluşturun
   (16 karakter; Gmail hesap şifreniz değil).
3. Proje klasöründe `.env.example` dosyasını `.env` olarak kopyalayıp doldurun:
   ```
   EMAIL_HOST_USER=sizin.adresiniz@gmail.com
   EMAIL_HOST_PASSWORD=abcd efgh ijkl mnop
   ```
4. Sunucuyu yeniden başlatın (`Ctrl+C`, sonra `python manage.py runserver`).
5. Denemek için: `python manage.py send_test_email alici@gmail.com`

Gönderen adres her zaman `EMAIL_HOST_USER` hesabıdır. E-posta Gelen Kutusu'nda yoksa Spam klasörüne bakın.
`.env` dosyasını kimseyle paylaşmayın (`.gitignore`'a eklidir).

Diğer SMTP sağlayıcıları için `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS` değişkenlerini `.env` içinde değiştirin.

## Kullanıcı oluşturma ve ilk giriş akışı (admin ve teknisyen)

1. Admin → **Personel → Kullanıcı ekle**. Hesap türü olarak **Admin** veya **Teknisyen** seçilir
   (ad, soyad, e-posta, telefon). Yeni admin, varsayılan admin gibi tam yetkili olur.
2. Sistem `ad.soyad` biçiminde benzersiz kullanıcı adı (Türkçe karakterler çevrilir; çakışmada `ad.soyad2`) ve
   12 karakterlik güvenli geçici şifre üretir, `send_mail` ile kişiye gönderir.
   E-posta gönderilemezse hesap yine oluşur; listeden **Bilgileri yeniden gönder** ile yeni geçici şifre yollanır.
3. Kullanıcı e-postadaki bilgilerle `/giris/` adresinden girer.
4. `is_temporary_password=True` olduğu için `ForcePasswordChangeMiddleware` onu her istekte
   `/sifre-degistir/` ekranına yönlendirir (yalnızca çıkış yapabilir ve Gizlilik/Şartlar sayfalarını okuyabilir).
5. Yeni şifre kaydedilince `is_temporary_password=False` olur; admin **admin paneline**, teknisyen **teknisyen paneline** yönlendirilir.

Güvenlik kuralları: kimse kendi hesabını pasifleştiremez veya kendine geçici şifre gönderemez;
kullanıcı oluşturma ve yönetim sayfalarına yalnızca admin erişir.

Django'nun kendi admin sitesi (`/django-admin/`) üzerinden eklenen kullanıcılar için de aynı
otomatik kullanıcı adı/şifre/e-posta akışı çalışır.

## Kullanıcı kartı, Gizlilik ve Şartlar

- Panel sol altındaki kart kullanıcının adını ve e-postasını gösterir; tıklayınca **Profil** ve **Çıkış Yap** açılır.
  **Çıkış Yap** oturumu kapatır (POST). **Profil** henüz bir sayfaya gitmez, sonraki aşamada eklenecek.
- `/gizlilik/` (Gizlilik Politikası ve KVKK Aydınlatma Metni) ve `/sartlar/` (Kullanım ve Güvenlik Şartları)
  giriş yapmadan okunabilir; giriş sayfasında ve tüm panel sayfalarının altında bağlantıları vardır.
- Metinlerdeki kurum bilgileri `.env` ile değiştirilir:
  ```
  COMPANY_NAME=Sirket Unvaniniz
  COMPANY_ADDRESS=Acik adresiniz
  KVKK_EMAIL=kvkk@sirketiniz.com
  ```
- Metinler genel bir şablondur; canlıya almadan önce hukuk danışmanınızla gözden geçirin.

## Proje yapısı

```
fixlab/
├── manage.py
├── .env.example            Gmail e-posta ayarı örneği (.env olarak kopyalayın)
├── requirements.txt
├── config/                 settings.py, urls.py, wsgi.py, asgi.py
├── templates/403.html
└── core/
    ├── models.py           User, Device, DevicePartUsage, DeviceStatusHistory,
    │                       StockPart, StockMovement, TechnicianLeave, AuditLog
    ├── migrations/         0001_initial, 0002_default_admin (varsayılan admin)
    ├── forms.py            Giriş, teknisyen oluşturma, zorunlu şifre değiştirme
    ├── views.py / urls.py  Giriş, paneller, personel yönetimi
    ├── services.py         provision_user, reset_credentials
    ├── utils.py            Kullanıcı adı/şifre üretimi, e-posta, audit log
    ├── middleware.py       Zorunlu şifre değiştirme kilidi
    ├── signals.py          Giriş/çıkış/başarısız giriş logları
    ├── admin.py            Django admin kayıtları
    ├── context_processors.py  Kurum bilgileri (KVKK sayfaları)
    ├── tests.py            Akış testleri (15 test)
    ├── templates/core/     login, force_password_change, staff_*, dashboard'lar, privacy, terms, e-posta
    └── static/core/css/    app.css (yeşil/gri kurumsal tema, harici bağımlılık yok)
```

## Modeller

| Model | Amaç |
|-------|------|
| `User` | `role` (admin/teknisyen/müşteri), `is_temporary_password`, `phone`, `specialization`, `hired_at` |
| `Device` | `tracking_id` (`SRV-YYYY-1001`… otomatik), müşteri bilgileri, cihaz bilgileri, arıza, durum, öncelik, atanan teknisyen, maliyetler |
| `DevicePartUsage` | Cihazda kullanılan parçalar (kullanım anındaki birim fiyat saklanır) |
| `DeviceStatusHistory` | Cihaz durum geçmişi |
| `StockPart` / `StockMovement` | Parça kataloğu, kritik seviye, stok hareketleri |
| `TechnicianLeave` | İzin talepleri (tür, tarih aralığı, durum, onaylayan) |
| `AuditLog` | Giriş/çıkış, oluşturma, şifre değişimi vb. işlem geçmişi |

## Güvenlik notları

- Şifreler Django'nun PBKDF2 hashleriyle saklanır; yeni şifre için en az 8 karakter, yaygın/sadece rakam şifre yasağı ve kullanıcı bilgisine benzerlik kontrolü uygulanır.
- Tüm formlar CSRF korumalıdır; çıkış ve yönetim işlemleri yalnızca POST ile çalışır; rol kontrolü sunucu tarafındadır.
- Doğrulama hem tarayıcıda (HTML5 + JS) hem sunucuda (Django formları) yapılır.
- Canlıya alırken `DJANGO_DEBUG=0`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` ayarlayın ve HTTPS kullanın.
