# FIXLAB — PostgreSQL + LDAP ile çalıştırma

## Mimari (hocaya anlatım özeti)

| Ne | Nerede tutulur |
|---|---|
| Kullanıcı adı, e-posta, ad soyad, telefon, **rol**, **şifre** (admin / teknisyen / müşteri) | **LDAP** (OpenLDAP) |
| Cihazlar, izinler, stok, bildirimler, işlem kayıtları, atamalar | **PostgreSQL** |
| Django'daki `User` satırı | Yalnızca "gölge kayıt": cihaz/izin ilişkileri için gerekir, **şifre içermez** |

* Rol, kullanıcının LDAP'taki kutusundan (OU) gelir: `ou=admins`, `ou=technicians`, `ou=customers` (kök: `dc=fixlab,dc=local`).
* Giriş yapılınca şifre **LDAP sunucusunda** doğrulanır (`core/ldap_backend.py`). Şifreler LDAP'ta `{SSHA}` (tuzlu SHA-1) olarak durur.
* Müşteri kaydı, personel ekleme, ilk giriş şifre değiştirme, "şifremi unuttum", "bilgileri yeniden gönder" ve teknisyen silme işlemleri LDAP'a yazar (`core/accounts.py`).
* LDAP erişim kodu: `core/directory.py`. Testler: `core/tests_directory.py` (sahte LDAP ile) ve `core/tests.py` (LDAP modu testleri).
* Bir hesabı pasife almak (Personel Yönetimi › Sil, geçmişi varsa) girişi engeller; LDAP kaydı korunur.

## Gerekenler

* Python 3.10+  ·  Docker Desktop (PostgreSQL ve LDAP'ı tek komutla başlatmak için)

## Kurulum — adım adım

Komutları proje klasöründe (`manage.py`'nin olduğu yer) çalıştırın.

**1) Python paketleri**
```
python -m venv .venv
.venv\Scripts\activate          (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
```

**2) PostgreSQL + LDAP sunucularını başlat**
```
docker compose up -d
docker compose ps               (iki servis "running" olmalı: fixlab-postgres ve fixlab-ldap; ilk açılış 1 dakika sürebilir)
```
Ayarlar `.env` dosyasında hazırdır (`DB_ENGINE=postgres`, `LDAP_ENABLED=1`).

**3) Veritabanını hazırla** — iki yoldan biri:

* *Eski SQLite verinizi (db.sqlite3) korumak istiyorsanız:*
  ```
  python tools/sqlite_to_postgres.py
  ```
  (SQLite'ı `eski_veri.json`'a yedekler, PostgreSQL'de tabloları kurar ve veriyi yükler. `db.sqlite3` silinmez.)
* *Sıfırdan başlıyorsanız:*
  ```
  python manage.py migrate
  ```

**4) Kullanıcıları LDAP'a taşı / dizini hazırla**
```
python manage.py ldap_bootstrap
```
Bu komut: LDAP'ta `admins`, `technicians`, `customers` kutularını açar ve veritabanındaki her kullanıcıyı LDAP'a ekler.
Eski şifre özetleri LDAP'a taşınamadığı için:
* varsayılan `admin` hesabı `.env`/ayarlardaki şifreyle açılır (`FixLab#Admin2025`, `FIXLAB_ADMIN_PASSWORD` ile değiştirilir),
* diğer personel için **yeni geçici şifre** üretilir ve ekranda bir kez listelenir (ilk girişte değiştirmeleri istenir),
* müşteriler "Şifremi unuttum" ile kendi şifrelerini belirler.
Geçici şifreleri e-postayla da göndermek için: `python manage.py ldap_bootstrap --notify`

**5) Denetle ve çalıştır**
```
python manage.py ldap_check
python manage.py runserver
```
Tarayıcı: <http://127.0.0.1:8000/giris/> (personel) · <http://127.0.0.1:8000/musteri/giris/> (müşteri)

## LDAP'ı gözle görmek

* Uygulamadan: `python manage.py ldap_check` (bağlantı + dizindeki kullanıcı sayıları)
* Komut satırından: `docker compose exec ldap ldapsearch -x -H ldap://localhost:1389 -D "cn=admin,dc=fixlab,dc=local" -w fixlab_ldap_admin -b "dc=fixlab,dc=local" "(objectClass=inetOrgPerson)" uid mail`

## Testler
```
python manage.py test core
```
Testler hız için SQLite ve sahte (bellek içi) LDAP kullanır; PostgreSQL/LDAP sunucusu gerektirmez.

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| `ldap_bootstrap`: "LDAP sunucusuna bağlanılamadı" | `docker compose ps` ile `fixlab-ldap` çalışıyor mu? Değilse `docker compose logs ldap --tail 40` 389 portu başka programda mı? `.env` → `LDAP_URL`, `LDAP_BIND_PASSWORD` |
| `connection refused` (5432) | `fixlab-postgres` çalışıyor mu? Bilgisayarda yerel PostgreSQL 5432'yi tutuyorsa docker-compose.yml'deki portu `"5433:5432"` yapıp `.env`'de `DB_PORT=5433` yazın |
| Giriş sayfasında "Kimlik doğrulama sunucusuna ulaşılamıyor" | LDAP kapalı: `docker compose start ldap` |
| LDAP'a ulaşılamayınca acil giriş | `python manage.py createsuperuser` ile yerel hesap açıp `.env`'de `LDAP_LOCAL_FALLBACK=1` yapın (işiniz bitince 0'a alın) |
| LDAP'sız / SQLite ile çalışmak | `.env` → `LDAP_ENABLED=0` ve/veya `DB_ENGINE=sqlite` (hesaplar o zaman Django'nun kendi şifre sistemiyle çalışır) |
| LDAP'ı sıfırlamak | `docker compose down -v` (tüm LDAP+PostgreSQL verisini siler!), sonra 2–4. adımlar |

## Bilmeniz gerekenler

* **Şifre değişimi / oturumlar:** Şifre değişince kullanıcının diğer açık oturumları düşer (gölge kayıttaki şifre alanı her değişimde yenilenir).
* **Django yönetim sitesi (/django-admin/):** Yeni kullanıcı eklemek uygulamadaki gibi LDAP'a da yazar; ad/e-posta/telefon/rol düzenlemeleri LDAP'a yansıtılır. Oradaki "şifre değiştir" ekranı LDAP şifresini **değiştirmez** — şifre için uygulamadaki akışları kullanın.
* **LDAP'ta doğrudan kullanıcı eklerseniz** (bir LDAP istemcisiyle), kullanıcı ilk girişinde veritabanında otomatik oluşur; rolü bulunduğu kutudan alır.
* **Canlıya alırken:** `docker-compose.yml` ve `.env` içindeki varsayılan şifreleri değiştirin; LDAP bağlantısını `ldaps://` ya da `LDAP_START_TLS=1` ile şifreleyin; `.env` dosyasını paylaşmayın (içinde e-posta uygulama şifreniz var); `DJANGO_DEBUG=0` ve güçlü `DJANGO_SECRET_KEY` kullanın.


## STOK_BILDIRIM_NOTLARI
# FIXLAB · Stok Depo + Admin Bildirimlerim yaması

Zip'teki dosyaları `fixlab/` klasörünün üzerine kopyalayın (aynı yollar), sonra:

    python manage.py migrate        # 0005_stock_panel_notifications
    python manage.py test core
    python manage.py runserver

`.env` ve `db.sqlite3` yamada yoktur.

## Stok Depo (Admin)
- /panel/stok/ liste · /panel/stok/yeni/ yeni parça · /panel/stok/<id>/ detay + stok girişi · /panel/stok/<id>/duzenle/ düzenle
- Sil: hiç kullanılmamış parça silinir; cihazda kullanılmış / talebi olan parça arşivlenir (is_active=False).
- Her adet değişimi StockMovement kaydı bırakır (giriş, düzeltme, çıkış) ve işlem kayıtlarına yazılır.

## Bildirimlerim (Admin)
- Zil, her admin sayfasında /bildirimler/ sayfasını açar; rozet ve sol menü sayısı canlı güncellenir.
- Parça talebi: Django admin › Parça talepleri'nden (teknisyen ekranı hazır olana dek) açılır; admin panelinde Onayla → stoktan düşer,
  cihaza parça olarak eklenir; Reddet → değişiklik yok. Teknisyene bildirim gider.
- İzin talebi: Django admin › İzin talepleri'nde "Onay bekliyor" kaydı açılınca adminlere düşer; Onayla/Reddet panelden.
- Bildirim tercihleri anahtarları zil, panel ve tarayıcı bildirimini filtreler (kayıt kullanıcıya özeldir).
- "Tarayıcı bildirimleri" anahtarı izin ister; sekme açıkken 15 sn'de bir yoklanır (kapalıyken Web Push gerekir).


# FIXLAB · Müşteri portalı yaması

Bu zip'teki dosyaları kendi `fixlab/` klasörünüzün üzerine kopyalayın (aynı yollar), sonra:

    python manage.py migrate
    python manage.py test core
    python manage.py runserver

`.env` ve `db.sqlite3` dosyalarına dokunulmadı; yamada yoklar.

## Bildirimler (tarayıcı seviyesinde)
- Zil menüsünden "Tarayıcı bildirimlerini aç" ile izin verilir.
- Sekme açıkken her 15 sn'de bir sunucu yoklanır (core/static/core/js/notify.js).
- Sekme/tarayıcı tamamen kapalıyken bildirim GELMEZ; bunun için Web Push (service worker + pywebpush + HTTPS) gerekir.
- Okunmamış bildirimler giriş yapınca zil rozetinde ve /bildirimler/ sayfasında görünür.

## Durum / kargo değişikliklerini kim yapar?
Şimdilik Django admin (/django-admin/ → Cihazlar). Durumu, tahmini ücreti/teslim tarihini,
geri kargo takip kodunu değiştirip kaydedince müşteriye otomatik bildirim gider.

## Şifre sıfırlama (müşteri + personel), zil kutusu, admin menüsü, Personel Yönetimi
- Şifre sıfırlama 3 ayrı sayfa: e-posta → kod doğrulama → yeni şifre. Müşteri: /musteri/sifremi-unuttum/ · /musteri/kod-dogrula/ · /musteri/sifre-yenile/. Personel: /giris/sifremi-unuttum/ · /giris/kod-dogrula/ · /giris/sifre-yenile/.
- Zil: tüm panellerde kutu açılır (son 6 bildirim) + «Tümünü görüntüle» → bildirimler sayfası.
- Admin menüsü: Cihazlar alt öğeleri henüz geliştirilmediği için tıklanmaz; sayaçlar context_processors.admin_nav_counts() ile hesaplanır. Yeni kayıtlar sayfası: admin_new_devices (panel/cihazlar/yeni-kayitlar/); «Detay ve ata» sonra yapılacak, şimdilik tıklanmaz. Teknisyen atama: staffing.assign_device() + Device sinyalleri (atanma zamanı, bildirim); migration 0006 (assigned_at) — `python manage.py migrate` çalıştırın.
- Personel Yönetimi: core/staffing.py (iş yükü, izin takvimi, izin ver/düzenle/iptal, sil→geçmişi varsa pasife al).

## PostgreSQL + LDAP
- Kimlik bilgileri (şifre dahil) LDAP'ta, uygulama verisi PostgreSQL'de. Ayrıntı ve kurulum: CALISTIRMA.md.
- Yeni dosyalar: core/directory.py (LDAP katmanı), core/accounts.py (hesap/şifre servisi), core/ldap_backend.py,
  komutlar ldap_bootstrap / ldap_check, docker-compose.yml, tools/sqlite_to_postgres.py, requirements.txt (ldap3, psycopg).
- Signal'ler loaddata (raw) yüklemelerinde bildirim üretmez.

## Yeni kayıt › «Detay ve ata», isim kısıtları, müşteri girişi ölçüleri
- **Detay ve ata**: Yeni kayıtlar listesindeki buton artık `panel/cihazlar/yeni-kayitlar/<id>/` sayfasını açar (`views.admin_new_device_detail`,
  şablon `admin_new_device_detail.html`). Cihaz/müşteri bilgileri, aşama çubuğu, işlem geçmişi, kayıt özeti, müşterinin önceki kayıtları ve «Teknisyene ata».
- **Teknisyene ata**: teknisyenler açılır listede; her isim yanında «N aktif iş · uzmanlık alanı» görünür, liste Türkçe alfabeye göre A→Z sıralıdır
  (`staffing.assignable_technicians`). Liste kapalıyken varsayılan olarak en az açık işi olan teknisyen seçili gelir (eşitlikte alfabetik ilk).
  Pasif teknisyen listede yok; izindeki teknisyen listede görünür ama seçilemez.
- Teknisyene yazılan not, teknisyenin bildirimine ve işlem kaydına eklenir (`assign_device(..., note="")`). Veritabanında yeni alan yok, migration gerekmez.
- **Ad / soyad**: yalnızca harf (Türkçe harfler dahil) ve kelimeler arası boşluk. Sunucu: `forms.clean_person_name` (müşteri üye ol, personel ekle,
  kargo alıcı adı). Tarayıcı: `static/core/js/names.js` (`data-name-only` olan alanlar; rakam/sembol yazılamaz, yapıştırılsa da atılır).
- **Müşteri girişi**: görsel, panel, başlık, alan ve düğme ölçüleri personel girişiyle aynı (`customer.css` sonundaki `.cp-fixed` bloğu).
- Testler: `python manage.py test core`

## Bildirim tıklaması + teknisyen sıralaması (güncelleme)
- Admin bildirimine (zil kutusu, tarayıcı bildirimi, panel) tıklanınca artık Django yönetimi değil **Bildirimler paneli** açılır (`views.notification_open`); bildirim okundu işaretlenir.
  Bildirimler panelindeki kartlardan «Cihazı aç / Stok hareketini aç» bağlantıları kaldırıldı. Teknisyen ve müşteri bildirimleri eskisi gibi çalışır.
- «Teknisyene ata» listesi artık **en az açık işi olandan en çok olana** sıralıdır; eşit iş sayısında ada göre A→Z. Varsayılan seçili olan listenin ilkidir
  (en az işi olan). İzindeki teknisyenler listenin sonunda, seçilemez durumdadır. İş sayısı / uzmanlık yazıları değişmedi (`staffing.assignable_technicians`).


## Cihazlar › Tüm cihazlar · Yeni cihaz kaydı · Kayıt oluşturuldu (yeni)
- **Tüm cihazlar** (`panel/cihazlar/`, `views.admin_device_list`, şablon `admin_device_list.html`): tüm servis kayıtları; Takip kodu / Müşteri / Cihaz /
  Teknisyen / Durum / Tarih / Detay sütunları, arama (çok sözcüklü), durum ve teknisyen süzgeci («Atanmamış» dahil), 10'arlı sayfalama, «Toplam N kayıt».
  Soldaki menüde «Tüm cihazlar» artık gerçek bağlantıdır (Kargolanacaklar hâlâ «Yakında»).
- **Yeni cihaz kaydı** (`panel/cihazlar/yeni/`, `views.admin_device_create`, `forms.AdminDeviceRegistrationForm`): yönetici servise getirilen cihazı müşteri adına kaydeder.
  Alanlar: ad soyad, telefon, e-posta, kategori, marka, model, seri no (isteğe bağlı), müşterinin bildirdiği arıza, fotoğraf/video (gerçek yükleme; JPG/PNG/MP4/MOV), teknisyen.
  Kayıt «Teslim alındı» durumunda açılır; takip kodu (`TS-YYYY-NNNN`) otomatik üretilir. Aynı e-postayla kayıtlı müşteri hesabı varsa kayıt o hesaba bağlanır.
- **Teknisyen ata açılır listesi**: varsayılan olarak en az açık işi olan teknisyen seçili gelir; liste azdan çoğa sıralıdır, her satırda «N aktif iş · uzmanlık alanı» yazar
  (`staffing.assignable_technicians`, «Teknisyene ata» kartıyla aynı görünüm). İzindeki teknisyen listede görünür ama seçilemez; en altta «Şimdilik atama yapma» vardır
  (seçilirse kayıt «Yeni kayıtlar» listesinde atama bekler). Teknisyen seçilirse iş doğrudan teknisyenin paneline düşer ve teknisyene bildirim gider.
- **Kayıt oluşturuldu** (`panel/cihazlar/<id>/olusturuldu/`): takip kodu + «Cihaz detayına git» / «Cihazlar listesini görüntüle». Takip kodu, mevcut
  `utils.send_device_registered_email` ile müşteriye e-postalanır; gönderim başarısız olursa ekranda uyarı çıkar (başarılıymış gibi gösterilmez).
- **Detay** (`panel/cihazlar/<id>/`, `views.admin_device_detail`): «Yeni kayıtlar › Detay ve ata» ile aynı şablon/gövde (`_device_detail_page`); her cihaz açılır,
  teslim edilmiş / iptal / onarılamayan kayıtlarda atama kartı gizlenir. «Yeni kayıtlar» detay sayfası davranışı değişmedi (yalnızca yeni kayıtlar açılır).
- Veritabanında yeni alan yok, **migration gerekmez**. Yeni dosyalar: `admin_devices.css`, `admin_devices.js`, 3 şablon. `dark.css` sonuna koyu tema kuralları eklendi.
- Test: `python manage.py test core` (yeni `AdminAllDevicesTests`; `AdminSidebarTests` içindeki «Tüm cihazlar tıklanamaz» beklentisi güncellendi).
