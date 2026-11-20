"""Kişisel verileri yapay zekâya (buluta) göndermeden ÖNCE bu bilgisayarda maskeler (KVKK)."""
import re

DESENLER = [  # sıra önemli: uzun numaralar önce
    ("[E-POSTA]", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("[IBAN]",    re.compile(r"\bTR\s?\d{2}(?:\s?\d{4}){5}\s?\d{2}\b", re.I)),
    ("[KART-NO]", re.compile(r"\b(?:\d[ -]?){15,16}\b")),
    # Cep (5xx), sabit hat (2xx/3xx/4xx alan kodları) ve 0850 numaraları
    ("[TELEFON]", re.compile(r"(?<!\d)(?:\+?90[\s-]?)?\(?0?(?:5\d{2}|[2-4]\d{2}|850)\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d)")),
    ("[TC-NO]",   re.compile(r"\b[1-9]\d{10}\b")),
]

# ---------------- İsim tanıma (yaygın Türkçe ad / soyad listesi) ----------------
# Normal kelimeyle karışmayan adlar: büyük/küçük harf fark etmeden maskelenir.
ADLAR = set("""
ahmet mehmet mustafa ali hasan hüseyin ibrahim ismail yusuf ömer osman murat fatih emre burak kerem
selim cemal cemil kemal oğuz tolga serkan taner tamer mahmut halil hamza bilal furkan enes yunus eren
berk batuhan görkem cihan sercan okan orhan ercan koray levent melih utku yiğit mert efe kaan alper
recep hakan erkan sinan ilker gökhan ozan mete kadir yasin abdullah salih sami arif tuncay tuncer
erhan erdal ertuğrul fikret halit harun ilhan kenan kürşat necati nihat oktay orçun 
sadık sedat semih serdar sezer şükrü tahsin tarık turgut uğurcan vedat yakup yavuz yılmaz 
emirhan berkay arda alperen miraç eymen göktuğ çağatay anıl onurcan egemen
ayşe fatma zeynep emine hatice merve esra büşra kübra rabia sena senanur sıla zehra hülya gamze
tuğba tuba esma şeyma sevim songül gülsüm nurten hacer meryem selin ceren cansu dilara burcu aysel
ayten ayla melike melisa irem sude nisa ecrin azra beyza buse cemre gökçe hande leyla nermin sibel
şule tuğçe yeliz zerrin şeyda elif ebru mine asiye aynur ayşegül birsen canan didem dilan elçin
emel fadime feride gülay gülşen hacer handan havva kevser kezban leman medine meltem
müge neslihan nihal nilay nilüfer nurcan nuray özge öznur pelin reyhan saadet sabiha seher selma
semra serpil sevil şenay şengül tülay ümran zübeyde zuhal eda yaren elanur
""".split())

# Aynı zamanda normal kelime olan adlar (gül, deniz, can...): yalnızca isim gibi kullanılınca
# maskelenir (büyük harfle yazılıp ardından soyad/ünvan geliyorsa).
BELIRSIZ_ADLAR = set("""
gül nur su ay can deniz bahar yağmur umut ümit barış güneş onur özgür erdem doğan ırmak çınar ada
ela duru nehir defne bulut toprak demir ateş kartal tuna mutlu kaya aslan ata bora emir volkan engin
uğur doruk veli özlem sevgi yasemin dilek derya aslı gizem eylül damla duygu ipek lale nazlı oya
pınar seda serap filiz hilal kader hayat melek sevda naz çağla asya nil alp
yıldız metin gönül sevinç zafer kuzey ayaz çağrı ezgi esin rıza
""".split())

# Soyadı olduğu kesin olanlar (küçük harfle yazılsa da soyad sayılır)
SOYADLAR = set("""
yılmaz öztürk aydın özdemir arslan şahin yıldırım kılıç çetin özkan şimşek polat özcan korkmaz
çakır erdoğan yavuz acar aktaş güler yalçın bozkurt ünal turan özer kaplan tekin köse yüksel aksoy
karaca kocabaş karakaya kahraman çiftçi uçar türk tunç sönmez ekinci eren altun altıntaş baysal
""".split())
# Normal kelime de olan soyadlar: büyük harfle ya da kesin bir adın hemen ardından gelince soyad sayılır
BELIRSIZ_SOYADLAR = set("""
kaya demir yıldız çelik doğan kara koç kurt güneş bulut ışık sarı taş ateş avcı keskin gül can şen
aslan erdem kartal toprak
""".split()) | BELIRSIZ_ADLAR

UNVANLAR = {"bey", "hanım", "hanim", "abi", "abla", "hoca", "hocam", "amca", "teyze", "beyin", "hanımın"}
EKLER = ("nin", "nın", "nun", "nün", "in", "ın", "un", "ün", "ye", "ya", "yi", "yı", "yu", "yü",
         "e", "a", "i", "ı", "u", "ü", "den", "dan", "ten", "tan", "de", "da", "te", "ta", "le", "la",
         "yle", "yla", "ler", "lar")

_KELIME = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")


def _kucuk(s):
    return s.replace("I", "ı").replace("İ", "i").lower()


def _kok(kelime):
    """'Ayşe'nin' -> ('ayşe', "'nin");  'mehmetin' -> ('mehmet', 'in')."""
    if "'" in kelime or "’" in kelime:
        kok, ek = re.split(r"['’]", kelime, maxsplit=1)
        return _kucuk(kok), kelime[len(kok):]
    k = _kucuk(kelime)
    if k in ADLAR or k in BELIRSIZ_ADLAR or k in SOYADLAR:
        return k, ""
    for ek in EKLER:  # kesin adlarda kesme işaretsiz ekleri de çöz ("mehmetin")
        if k.endswith(ek) and k[: -len(ek)] in ADLAR:
            return k[: -len(ek)], kelime[len(kelime) - len(ek):]
    return k, ""


def _isimleri_maskele(metin):
    kelimeler = [m for m in _KELIME.finditer(metin)
                 if not (m.start() > 0 and metin[m.start() - 1] == "[")]  # [TELEFON] gibi etiketleri atla
    parcalar, son, i = [], 0, 0
    while i < len(kelimeler):
        m = kelimeler[i]
        kelime = m.group()
        kok, ek = _kok(kelime)
        buyuk = kelime[0].isupper()
        sonraki = kelimeler[i + 1] if i + 1 < len(kelimeler) else None
        bitisik = sonraki is not None and metin[m.end():sonraki.start()].strip(" \t") == ""
        s_kelime = sonraki.group() if bitisik else ""
        s_kok = _kok(s_kelime)[0] if bitisik else ""
        s_buyuk = bool(s_kelime) and s_kelime[0].isupper()

        if kok in ADLAR:
            isim = True
        elif kok in BELIRSIZ_ADLAR:
            # "deniz yılmaz", "Can Bey", "Gül Kaya" → isim;  "deniz suyu", "can sıkıcı" → isim değil
            isim = s_kok in SOYADLAR or s_kok in UNVANLAR or (buyuk and s_buyuk)
        else:
            isim = False
        if not isim:
            i += 1
            continue

        # Ardından gelen soyad / ikinci ad (en fazla 2 kelime) da maskelenir
        bas, j, son_ek = m.start(), i, ek
        while j + 1 < len(kelimeler) and j - i < 2:
            a, b = kelimeler[j], kelimeler[j + 1]
            if metin[a.end():b.start()].strip(" \t") != "" or son_ek:
                break
            bk, bek = _kok(b.group())
            if bk in UNVANLAR:
                break
            if (b.group()[0].isupper() or bk in SOYADLAR or bk in ADLAR
                    or (kok in ADLAR and bk in BELIRSIZ_SOYADLAR)):
                j, son_ek = j + 1, bek
            else:
                break
        parcalar.append(metin[son:bas])
        parcalar.append("[KİŞİSEL]" + son_ek)
        son = kelimeler[j].end()
        i = j + 1
    parcalar.append(metin[son:])
    return "".join(parcalar)


def anonimlestir(metin, bilinenler=()):
    """bilinenler: metinde geçerse silinecek ad, soyad, adres gibi değerler."""
    if not metin:
        return ""
    degerler = sorted({str(b).strip() for b in bilinenler if b and len(str(b).strip()) >= 3}, key=len, reverse=True)
    for deger in degerler:
        metin = re.sub(r"\b" + re.escape(deger) + r"\b", "[KİŞİSEL]", metin, flags=re.I)
    for etiket, desen in DESENLER:
        metin = desen.sub(etiket, metin)
    return _isimleri_maskele(metin)


def kullanici_bilgileri(user):
    if not user or not getattr(user, "pk", None):
        return []
    return [user.get_full_name(), user.first_name, user.last_name, user.username,
            user.email, user.phone, user.address, user.district]
