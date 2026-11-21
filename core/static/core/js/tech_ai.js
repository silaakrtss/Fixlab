/* FIXLAB · Teknisyen AI Çözüm Asistanı
   - Cihaz kaydı bilgileri (arıza, notlar, parçalar, önceki kayıtlar) sunucuda otomatik eklenir.
   - Fotoğraflar doğrudan, videolar tarayıcıda karelere ayrılıp (JPEG) gönderilir: model video değil görüntü okur.
   - Konuşma, sayfa yenilense de kaybolmasın diye bu sekmede (sessionStorage) cihaz bazında saklanır. */
(function () {
  var box = document.getElementById('tai');
  if (!box || !box.dataset.url) return;
  var $ = function (id) { return document.getElementById(id); };
  var msgs = $('tai-msgs'), form = $('tai-input'), text = $('tdx-ai-text'), sendBtn = $('tai-send'),
      photoIn = $('tai-photo'), videoIn = $('tai-video'), chips = $('tai-chips'), count = $('tai-count'),
      note = $('tai-send-note'), good = $('tai-good'), bad = $('tai-bad'), clearBtn = $('tai-clear');
  var MAX_IMG = 6, KARE = 3, KEY = box.dataset.key;
  var gecmis = [], fotolar = [], videolar = [], kayitKareleri = {}, mesgul = false;

  // ---------- yardımcılar ----------
  function csrf() { var i = document.querySelector('[name=csrfmiddlewaretoken]'); return i ? i.value : ''; }
  function esc(t) { var d = document.createElement('div'); d.textContent = t || ''; return d.innerHTML; }
  function sakla() { try { sessionStorage.setItem(KEY, JSON.stringify(gecmis.slice(-20))); } catch (e) {} }
  function yukle() { try { return JSON.parse(sessionStorage.getItem(KEY) || '[]') || []; } catch (e) { return []; } }

  // Model cevabını güvenli HTML'e çevirir: başlıklar, **kalın**, madde/numaralı listeler
  function bicimle(t) {
    var html = '', liste = null;
    function kapat() { if (liste) { html += '</' + liste + '>'; liste = null; } }
    esc(t).split(/\n+/).forEach(function (s) {
      s = s.replace(/\s+$/, '');
      if (!s.trim()) return;
      var baslik = s.match(/^\s*(?:#{1,4}\s*(.+)|\*\*([^*]+)\*\*:?)\s*$/);
      if (baslik) { kapat(); html += '<h4>' + (baslik[1] || baslik[2]).replace(/\*\*/g, '') + '</h4>'; return; }
      s = s.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
      var m = s.match(/^\s*([-*•]|\d+[.)])\s+(.*)$/);
      if (m) {
        var tur = /\d/.test(m[1]) ? 'ol' : 'ul';
        if (liste !== tur) { kapat(); html += '<' + tur + '>'; liste = tur; }
        html += '<li>' + m[2] + '</li>';
      } else { kapat(); html += '<p>' + s + '</p>'; }
    });
    kapat();
    return html;
  }

  function balon(html, kim) {
    var p = document.createElement('div');
    p.className = 'tdx-bubble ' + (kim === 'user' ? 'tdx-bubble-me' : kim === 'err' ? 'tdx-bubble-err' : 'tdx-bubble-bot');
    p.innerHTML = html;
    msgs.appendChild(p);
    msgs.scrollTop = msgs.scrollHeight;
    clearBtn.hidden = !gecmis.length;
    return p;
  }

  // ---------- medya ----------
  function kayitFotolari() { return [].slice.call(box.querySelectorAll('input[name=kayit_foto]:checked')); }
  function kayitVideolari() { return [].slice.call(box.querySelectorAll('input[data-video-url]:checked')); }
  function toplamGorsel() {
    return kayitFotolari().length + fotolar.length + (kayitVideolari().length + videolar.length) * KARE;
  }
  function sayaciGuncelle() {
    var n = kayitFotolari().length + fotolar.length + kayitVideolari().length + videolar.length, g = toplamGorsel();
    count.textContent = n + ' medya eklendi';
    if (g > MAX_IMG) {
      note.textContent = 'Görüntü sınırı ' + MAX_IMG + ' (her video ' + KARE + ' kare sayılır); fazlası gönderilmez.';
      note.classList.add('is-warn');
    } else {
      note.textContent = n ? 'Medya ile birlikte gönderilecek' : 'Medya ile birlikte gönder';
      note.classList.remove('is-warn');
    }
  }
  function cipleriCiz() {
    [].slice.call(chips.querySelectorAll('.tai-chip')).forEach(function (c) { c.remove(); });
    fotolar.forEach(function (f, i) { chips.insertBefore(cip(f, 'Fotoğraf', function () { fotolar.splice(i, 1); }), count); });
    videolar.forEach(function (v, i) { chips.insertBefore(cip(v.kareler[0], 'Video', function () { videolar.splice(i, 1); }), count); });
    sayaciGuncelle();
  }
  function cip(resim, ad, sil) {
    var c = document.createElement('span'); c.className = 'tai-chip';
    if (resim) {
      var img = document.createElement('img'); img.alt = ''; img.src = URL.createObjectURL(resim);
      img.onload = function () { URL.revokeObjectURL(img.src); };
      c.appendChild(img);
    }
    c.appendChild(document.createTextNode(ad));
    var x = document.createElement('button'); x.type = 'button'; x.textContent = '×'; x.setAttribute('aria-label', ad + ' kaldır');
    x.onclick = function () { sil(); cipleriCiz(); };
    c.appendChild(x);
    return c;
  }

  // Videodan eşit aralıklı KARE adet JPEG kare çıkarır (tarayıcıda; sunucuya video yüklenmez)
  function kareCikar(src) {
    return new Promise(function (coz, reddet) {
      var v = document.createElement('video'), kareler = [], zamanlar = [], i = 0;
      var bitti = setTimeout(function () { reddet(new Error('zaman aşımı')); }, 20000);
      v.muted = true; v.preload = 'auto'; v.playsInline = true; v.src = src;
      v.onerror = function () { clearTimeout(bitti); reddet(new Error('Video açılamadı')); };
      v.onloadedmetadata = function () {
        var d = isFinite(v.duration) && v.duration > 0 ? v.duration : 1;
        for (var k = 1; k <= KARE; k++) zamanlar.push(Math.min(d * k / (KARE + 1), Math.max(d - 0.05, 0)));
        v.currentTime = zamanlar[0];
      };
      v.onseeked = function () {
        var olcek = Math.min(1, 1024 / Math.max(v.videoWidth, v.videoHeight || 1));
        var c = document.createElement('canvas');
        c.width = Math.round(v.videoWidth * olcek); c.height = Math.round(v.videoHeight * olcek);
        c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
        c.toBlob(function (b) {
          if (b) kareler.push(b);
          if (++i < zamanlar.length) { v.currentTime = zamanlar[i]; return; }
          clearTimeout(bitti); v.removeAttribute('src'); v.load();
          kareler.length ? coz(kareler) : reddet(new Error('Kare alınamadı'));
        }, 'image/jpeg', 0.82);
      };
    });
  }

  $('tai-add-photo').addEventListener('click', function () { photoIn.click(); });
  $('tai-add-video').addEventListener('click', function () { videoIn.click(); });
  photoIn.addEventListener('change', function () {
    [].forEach.call(photoIn.files, function (f) {
      if (/^image\/(jpeg|png|webp)$/.test(f.type) && f.size <= 10 * 1048576) fotolar.push(f);
    });
    photoIn.value = ''; cipleriCiz(); text.focus();
  });
  videoIn.addEventListener('change', function () {
    var f = videoIn.files[0]; videoIn.value = '';
    if (!f) return;
    var url = URL.createObjectURL(f);
    note.textContent = 'Video karelere ayrılıyor…';
    kareCikar(url).then(function (k) { videolar.push({ ad: f.name, kareler: k }); cipleriCiz(); })
      .catch(function () { balon('Bu video tarayıcıda açılamadı (biçim desteklenmiyor olabilir). MP4 deneyin ya da ekran görüntüsü ekleyin.', 'err'); sayaciGuncelle(); })
      .then(function () { URL.revokeObjectURL(url); });
  });
  box.addEventListener('change', function (e) { if (e.target.closest('.tai-rec-item')) sayaciGuncelle(); });

  // ---------- gönderme ----------
  function mesgulAyarla(v) { mesgul = v; sendBtn.disabled = v; $('tai-issue').disabled = v; text.disabled = v; }

  function gonder(mesaj) {
    if (mesgul) return;
    var medyaVar = toplamGorsel() > 0;
    mesaj = (mesaj || '').trim() || (medyaVar ? 'Eklediğim görüntüleri incele; ne görüyorsun, olası neden ve kontrol adımları neler?' : '');
    if (!mesaj) { text.focus(); return; }
    mesgulAyarla(true);
    var kv = kayitVideolari();
    // Kayıttaki videoların karelerini (bir kez) çıkar, sonra gönder
    Promise.all(kv.map(function (el) {
      var u = el.dataset.videoUrl;
      if (kayitKareleri[u]) return kayitKareleri[u];
      return kareCikar(u).then(function (k) { return (kayitKareleri[u] = k); }).catch(function () { return []; });
    })).then(function (kayitKare) {
      var fd = new FormData(), kalan = MAX_IMG, foto = 0, kare = 0;
      kayitFotolari().forEach(function (el) { if (kalan > 0) { fd.append('kayit_foto', el.value); kalan--; foto++; } });
      fotolar.forEach(function (f) { if (kalan > 0) { fd.append('resimler', f, f.name); kalan--; foto++; } });
      kayitKare.concat(videolar.map(function (v) { return v.kareler; })).forEach(function (liste) {
        liste.forEach(function (b, i) { if (kalan > 0) { fd.append('video_kareleri', b, 'kare' + i + '.jpg'); kalan--; kare++; } });
      });
      var ek = [];
      if (foto) ek.push(foto + ' fotoğraf');
      if (kare) ek.push(kare + ' video karesi');
      balon(esc(mesaj) + (ek.length ? '<small>📎 ' + ek.join(' + ') + '</small>' : ''), 'user');
      gecmis.push({ role: 'user', content: mesaj });
      fd.append('mesajlar', JSON.stringify(gecmis.slice(-12)));
      text.value = '';
      var bekle = balon('<span class="tai-typing" aria-label="Asistan yazıyor"><i></i><i></i><i></i></span>' +
        (foto + kare ? ' <small style="display:inline">görüntüler inceleniyor…</small>' : ''), 'bot');
      return fetch(box.dataset.url, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-CSRFToken': csrf() } })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          bekle.remove();
          if (!d.ok) { gecmis.pop(); balon(esc(d.hata), 'err'); return; }
          gecmis.push({ role: 'assistant', content: d.cevap });
          sakla();
          balon(bicimle(d.cevap), 'bot');
          // Gönderilen medya bir sonraki mesajda tekrar gönderilmesin (model cevabında zaten değerlendirdi)
          fotolar = []; videolar = [];
          kayitFotolari().concat(kv).forEach(function (el) { el.checked = false; });
          cipleriCiz();
          [good, bad].forEach(function (b) { b.disabled = false; b.classList.remove('is-on'); });
        }, function () { bekle.remove(); gecmis.pop(); balon('Sunucuya ulaşılamadı. Bağlantınızı kontrol edip tekrar deneyin.', 'err'); });
    }).then(function () { mesgulAyarla(false); text.focus(); });
  }

  form.addEventListener('submit', function (e) { e.preventDefault(); gonder(text.value); });
  sendBtn.addEventListener('click', function () { gonder(text.value); });
  $('tai-issue').addEventListener('click', function () {
    gonder('Kayıttaki arıza açıklamasına göre olası nedenleri ve sırayla yapmam gereken kontrolleri yaz: ' + (box.dataset.issue || ''));
  });

  // ---------- geri bildirim ----------
  function geriBildirim(deger, btn) {
    var fd = new FormData(); fd.append('action', 'feedback'); fd.append('value', deger);
    fetch(box.dataset.url, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-CSRFToken': csrf() } }).catch(function () {});
    good.disabled = bad.disabled = true; btn.classList.add('is-on');
    btn.textContent += ' ✓';
    setTimeout(function () { btn.textContent = btn.textContent.replace(' ✓', ''); }, 2500);
  }
  good.addEventListener('click', function () { geriBildirim('1', good); });
  bad.addEventListener('click', function () { geriBildirim('0', bad); });

  clearBtn.addEventListener('click', function () {
    gecmis = []; sakla();
    [].slice.call(msgs.children, 1).forEach(function (el) { el.remove(); });
    clearBtn.hidden = true; good.disabled = bad.disabled = true;
  });

  // ---------- önceki konuşmayı geri yükle ----------
  gecmis = yukle();
  gecmis.forEach(function (m) { balon(m.role === 'user' ? esc(m.content) : bicimle(m.content), m.role === 'user' ? 'user' : 'bot'); });
  sayaciGuncelle();
})();
