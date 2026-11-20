/* FIXLAB · Yapay zekâ arıza asistanı (sohbet)
   mod="ana"  (müşteri ana sayfası): müşteri arızayı sohbete yazar, 📎 ile fotoğraf ekler.
              «Hâlâ düzelmedi» → kayıt sayfasına geçer, sohbette anlatılanlar forma aktarılır.
   mod="form" (cihaz kaydı sayfası): açıklama yazılınca (ve fotoğraf eklenince) başlık + basit çözümler önerir;
              formdaki fotoğraflar asistana gösterilir. «Hâlâ düzelmedi» → kayıt oluşturulur. */
(function () {
  var chat = document.getElementById('ai-chat');
  if (!chat) return;
  var MOD = chat.dataset.mod === 'ana' ? 'ana' : 'form';
  var $ = function (id) { return document.getElementById(id); };
  var fab = $('ai-fab'), teaser = $('ai-teaser'), msgs = $('ai-msgs'), decide = $('ai-decide'),
      chatForm = $('ai-form'), text = $('ai-text');
  // form modu alanları
  var form = $('device-form'), desc = $('f-desc'), title = $('f-title'), files = $('f-files'), list = $('f-filelist');
  // ana sayfa modu: sohbete eklenen fotoğraflar
  var photoInput = $('ai-photos'), chips = $('ai-chips'), sohbetFotolari = [];

  var gecmis = [];          // yapay zekâya giden konuşma: {role, content}
  var son = '', aiBaslik = '', zamanlayici = null, mesgul = false, kullaniciKapatti = false;
  var AKTAR = 'fx-ai-aktar', BALON = 'fx-ai-balon-kapali';

  // ---------- yardımcılar ----------
  function oku(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
  function yaz(k, v) { try { if (v === null) sessionStorage.removeItem(k); else sessionStorage.setItem(k, v); } catch (e) {} }
  function csrf() { var i = document.querySelector('[name=csrfmiddlewaretoken]'); return i ? i.value : ''; }
  function esc(t) { var d = document.createElement('div'); d.textContent = t || ''; return d.innerHTML; }
  function secilenMetin(id) {
    var el = $(id);
    if (!el) return '';
    if (el.options && el.selectedIndex >= 0) return el.value ? el.options[el.selectedIndex].text : '';
    return el.value || '';
  }
  function fotograflar() {
    if (MOD === 'ana') return sohbetFotolari.slice(0, 3);
    return files ? [].filter.call(files.files, function (f) { return /^image\//.test(f.type); }).slice(0, 3) : [];
  }
  function formVerisi() {
    var fd = new FormData();
    fd.append('sayfa', MOD);
    if (MOD === 'form') {
      fd.append('aciklama', desc.value.trim());
      fd.append('tur', secilenMetin('f-type'));
      var b = $('f-brand');
      var marka = b && b.value === '__other__' ? secilenMetin('f-brand-other') : secilenMetin('f-brand');
      fd.append('marka', marka);
      fd.append('model', secilenMetin('f-model'));
      fd.append('marka_model', (marka + ' ' + secilenMetin('f-model')).trim());
    }
    fotograflar().forEach(function (f) { fd.append('resimler', f); });
    return fd;
  }
  function gonder(url, fd) {
    return fetch(url, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-CSRFToken': csrf() } })
      .then(function (r) { return r.json(); });
  }
  // Yapay zekâ metnini güvenli HTML'e çevir: **kalın**, madde işaretli satırlar → liste
  function bicimle(t) {
    var satirlar = esc(t).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').split(/\n+/), html = '', liste = false;
    satirlar.forEach(function (s) {
      var m = s.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
      if (m) { if (!liste) { html += '<ul>'; liste = true; } html += '<li>' + m[1] + '</li>'; }
      else { if (liste) { html += '</ul>'; liste = false; } if (s.trim()) html += '<p style="margin:4px 0">' + s + '</p>'; }
    });
    return html + (liste ? '</ul>' : '');
  }

  // ---------- pencere + slogan balonu ----------
  function balonuGizle(kalici) { if (teaser) teaser.hidden = true; if (kalici) yaz(BALON, '1'); }
  function ac() {
    chat.hidden = false; fab.hidden = true; fab.classList.remove('has-new');
    fab.setAttribute('aria-expanded', 'true'); balonuGizle(true);
  }
  function kapat() {
    chat.hidden = true; fab.hidden = false; kullaniciKapatti = true;
    fab.setAttribute('aria-expanded', 'false'); fab.focus();
  }
  fab.addEventListener('click', function () { ac(); text.focus(); });
  $('ai-close').addEventListener('click', kapat);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !chat.hidden) kapat(); });
  if (teaser) {
    $('ai-teaser-x').addEventListener('click', function (e) { e.stopPropagation(); balonuGizle(true); });
    teaser.addEventListener('click', function () { ac(); text.focus(); });
    if (!oku(BALON) && !oku(AKTAR)) setTimeout(function () { if (chat.hidden) teaser.hidden = false; }, 1200);
  }

  function balon(html, kim) {
    var d = document.createElement('div');
    d.className = 'ai-msg ' + (kim === 'user' ? 'ai-user' : 'ai-bot');
    d.innerHTML = html;
    msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight;
    if (chat.hidden && kim !== 'user') fab.classList.add('has-new');
    return d;
  }
  function yaziyor() { return balon('<span class="ai-typing" aria-label="Asistan yazıyor"><i></i><i></i><i></i></span>', 'bot'); }
  function mesgulAyarla(v) { mesgul = v; chatForm.querySelector('.ai-send').disabled = v; }

  // ---------- Ana sayfa: sohbete fotoğraf ekleme ----------
  function cipleriCiz() {
    if (!chips) return;
    chips.innerHTML = '';
    sohbetFotolari.forEach(function (f, i) {
      var c = document.createElement('span'); c.className = 'ai-chip';
      var img = document.createElement('img'); img.alt = ''; img.src = URL.createObjectURL(f);
      img.onload = function () { URL.revokeObjectURL(img.src); };
      c.appendChild(img); c.appendChild(document.createTextNode('Fotoğraf ' + (i + 1)));
      var x = document.createElement('button'); x.type = 'button'; x.textContent = '×'; x.setAttribute('aria-label', 'Fotoğrafı kaldır');
      x.onclick = function () { sohbetFotolari.splice(i, 1); cipleriCiz(); };
      c.appendChild(x); chips.appendChild(c);
    });
    chips.hidden = !sohbetFotolari.length;
  }
  if (photoInput) {
    $('ai-attach').addEventListener('click', function () { photoInput.click(); });
    photoInput.addEventListener('change', function () {
      [].forEach.call(photoInput.files, function (f) {
        if (/^image\/(jpeg|png)$/.test(f.type) && f.size <= 8 * 1048576 && sohbetFotolari.length < 3) sohbetFotolari.push(f);
      });
      photoInput.value = ''; cipleriCiz(); text.focus();
    });
  }

  // ---------- Kayıt sayfası: açıklama / fotoğraf değişince otomatik analiz ----------
  function analiz(sessiz) {
    var metin = desc.value.trim(), foto = fotograflar().length, anahtar = metin + '|' + foto;
    if (metin.length < 15 || anahtar === son || mesgul) return;
    son = anahtar;
    if (sessiz) {  // ana sayfadan aktarıldıysa: önerileri tekrarlama, yalnızca başlığı doldur
      gonder(chat.dataset.analizUrl, formVerisi()).then(function (d) {
        if (d.ok && title && !title.value.trim()) { title.value = d.baslik; aiBaslik = d.baslik; }
      }).catch(function () {});
      return;
    }
    if (!kullaniciKapatti) ac();
    mesgulAyarla(true);
    var bekle = yaziyor();
    gonder(chat.dataset.analizUrl, formVerisi()).then(function (d) {
      bekle.remove();
      if (!d.ok) { balon(esc(d.hata), 'bot'); return; }
      if (title && (!title.value.trim() || title.value === aiBaslik)) { title.value = d.baslik; aiBaslik = d.baslik; }
      var oneriler = d.oneriler || [];
      balon((foto ? '📷 Açıklamanızı ve <b>' + foto + ' fotoğrafınızı</b> inceledim.' : 'Açıklamanızı inceledim.') +
        (d.acil_uyari ? '<p class="ai-warn">⚠️ ' + esc(d.acil_uyari) + '</p>' : '') +
        '<br><b>Servise gelmeden önce şunları deneyebilirsiniz:</b>' +
        '<ul>' + oneriler.map(function (o) { return '<li>' + esc(o) + '</li>'; }).join('') + '</ul>' +
        '<small>Başlık önerisi forma yazıldı. Denedikten sonra sonucu bana yazabilir ya da aşağıdan seçebilirsiniz.</small>', 'bot');
      gecmis.push({ role: 'assistant', content: (d.acil_uyari ? 'Uyarı: ' + d.acil_uyari + '\n' : '') +
        'Önerilerim:\n' + oneriler.map(function (o) { return '- ' + o; }).join('\n') });
      decide.hidden = false;
    }).catch(function () { bekle.remove(); balon('Bağlantı kurulamadı. Biraz sonra tekrar deneyin.', 'bot'); })
      .then(function () { mesgulAyarla(false); });
  }
  if (MOD === 'form' && desc) {
    desc.addEventListener('blur', function () { analiz(false); });
    var fotoDegisti = function () { clearTimeout(zamanlayici); zamanlayici = setTimeout(function () { analiz(false); }, 400); };
    if (list && window.MutationObserver) new MutationObserver(fotoDegisti).observe(list, { childList: true, subtree: true });
    else if (files) files.addEventListener('change', fotoDegisti);

    // Ana sayfadaki sohbetten gelindiyse: anlatılanları forma aktar, sohbete kaldığı yerden devam et
    var aktarilan = null;
    try { aktarilan = JSON.parse(oku(AKTAR) || 'null'); } catch (e) {}
    yaz(AKTAR, null);
    if (aktarilan && aktarilan.aciklama) {
      if (!desc.value.trim()) desc.value = aktarilan.aciklama;
      gecmis = (aktarilan.gecmis || []).slice(-12);
      ac();
      balon('Ana sayfadaki sohbetimizden arıza açıklamanızı forma aktardım. ✍️ Lütfen kontrol edip <b>cihaz bilgilerini</b> doldurun.' +
        (aktarilan.foto ? ' Sohbette eklediğiniz fotoğrafları aşağıdaki <b>Fotoğraf ve Video Ekle</b> bölümüne tekrar ekleyin.' : ''), 'bot');
      decide.hidden = false;
      analiz(true);
    }
  }

  // ---------- Müşterinin asistana yazması ----------
  chatForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var m = text.value.trim();
    if (!m || mesgul) return;
    var foto = fotograflar().length;
    text.value = '';
    balon(esc(m) + (MOD === 'ana' && foto ? '<br><small>📷 ' + foto + ' fotoğraf ekli</small>' : ''), 'user');
    gecmis.push({ role: 'user', content: m });
    mesgulAyarla(true);
    var bekle = yaziyor(), fd = formVerisi();
    fd.append('mesajlar', JSON.stringify(gecmis.slice(-12)));
    gonder(chat.dataset.sohbetUrl, fd).then(function (d) {
      bekle.remove();
      if (!d.ok) { gecmis.pop(); balon(esc(d.hata), 'bot'); return; }
      gecmis.push({ role: 'assistant', content: d.cevap });
      balon(bicimle(d.cevap), 'bot');
      decide.hidden = false;
    }).catch(function () { bekle.remove(); gecmis.pop(); balon('Bağlantı kurulamadı. Biraz sonra tekrar deneyin.', 'bot'); })
      .then(function () { mesgulAyarla(false); text.focus(); });
  });

  // ---------- Karar: çözüldü / hâlâ düzelmedi ----------
  $('ai-solved').addEventListener('click', function () {
    balon('✅ Sorunum çözüldü', 'user');
    balon('Harika, sevindim! 🎉 Sorununuz çözüldüyse servis kaydı oluşturmanıza gerek yok. ' +
      'Tekrar yaşarsanız bana yine yazabilirsiniz.' +
      (MOD === 'form' ? '<br><a href="' + chat.dataset.homeUrl + '">Ana sayfaya dön →</a>' : ''), 'bot');
    decide.hidden = true;
  });

  $('ai-still').addEventListener('click', function () {
    balon('🛠 Hâlâ düzelmedi', 'user');
    decide.hidden = true;

    if (MOD === 'ana') {  // sohbette anlatılanları kayıt formuna taşı
      var aciklama = gecmis.filter(function (m) { return m.role === 'user'; })
        .map(function (m) { return m.content; }).join('\n').slice(0, 1500);
      yaz(AKTAR, JSON.stringify({ aciklama: aciklama, gecmis: gecmis.slice(-12), foto: sohbetFotolari.length }));
      balon('Anlaşıldı, sizi servis kaydı sayfasına yönlendiriyorum. Anlattıklarınızı forma aktaracağım. 🛠', 'bot');
      setTimeout(function () { location.href = chat.dataset.kayitUrl; }, 900);
      return;
    }
    // Formda boş kalan zorunlu alan varsa oraya götür; yoksa kaydı oluştur
    var eksik = [].filter.call(form.querySelectorAll('[required]'), function (el) {
      return el.offsetParent !== null && !String(el.value || '').trim();
    })[0];
    if (eksik) {
      var etiket = form.querySelector('label[for="' + eksik.id + '"]');
      balon('Anlaşıldı, servis kaydınızı oluşturalım. Önce <b>' + esc(etiket ? etiket.textContent.replace('*', '').trim() : 'zorunlu alanları') +
        '</b> alanını doldurun, ardından <b>Kaydı Oluştur</b> butonuna basın.', 'bot');
      decide.hidden = false;
      eksik.scrollIntoView({ behavior: 'smooth', block: 'center' });
      setTimeout(function () { eksik.focus({ preventScroll: true }); }, 400);
      return;
    }
    balon('Anlaşıldı, servis kaydınızı oluşturuyorum… Teknisyenlerimiz cihazınızı inceleyecek. 🛠', 'bot');
    setTimeout(function () { form.requestSubmit ? form.requestSubmit() : form.submit(); }, 700);
  });
})();
