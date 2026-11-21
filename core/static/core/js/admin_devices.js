/* FIXLAB · Admin › Yeni cihaz kaydı: dosya ekleme (sürükle-bırak + liste) ve «Seçili teknisyen» özeti.
   Sunucu tarafı aynı kuralları forms.AdminDeviceRegistrationForm.validate_files ile ayrıca denetler. */
(function () {
  'use strict';

  // ---- Dosya ekleme ----
  var input = document.getElementById('ad-files');
  if (input && window.DataTransfer) {
    var drop = document.getElementById('ad-drop'), listEl = document.getElementById('ad-filelist'),
        head = document.getElementById('ad-filehead'), count = document.getElementById('ad-filecount'),
        errBox = document.getElementById('ad-fileerr');
    var MAX = parseInt(input.dataset.max, 10), PHOTO = parseInt(input.dataset.photoMb, 10) * 1048576,
        VIDEO = parseInt(input.dataset.videoMb, 10) * 1048576;
    var files = [];
    var ICON_PHOTO = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/></svg>';
    var ICON_VIDEO = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="6" width="14" height="12" rx="2"/><path d="m22 8-6 4 6 4z"/></svg>';

    var ext = function (n) { var i = n.lastIndexOf('.'); return i < 0 ? '' : n.slice(i + 1).toLowerCase(); };
    var kind = function (f) {
      var e = ext(f.name);
      return ['jpg', 'jpeg', 'png'].indexOf(e) > -1 ? 'photo' : ['mp4', 'mov'].indexOf(e) > -1 ? 'video' : null;
    };
    var size = function (b) {
      return b >= 1048576 ? (b / 1048576).toFixed(1).replace('.', ',') + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB';
    };
    var text = function (t) { var s = document.createElement('span'); s.textContent = t; return s.innerHTML; };

    var sync = function () {
      var dt = new DataTransfer();
      files.forEach(function (f) { dt.items.add(f); });
      input.files = dt.files;  // seçilen dosyalar formla birlikte gönderilir
      head.hidden = !files.length;
      count.textContent = files.length + ' dosya';
      listEl.innerHTML = '';
      files.forEach(function (f, i) {
        var k = kind(f), row = document.createElement('div');
        row.className = 'ad-file';
        row.innerHTML = '<span class="ad-file-ico">' + (k === 'video' ? ICON_VIDEO : ICON_PHOTO) + '</span>' +
          '<div class="ad-file-txt"><b>' + text(f.name) + '</b><small>' + (k === 'video' ? 'Video' : 'Fotoğraf') + ' · ' +
          text(ext(f.name).toUpperCase()) + ' · ' + size(f.size) + '</small></div>' +
          '<button type="button" data-i="' + i + '">Kaldır</button>';
        listEl.appendChild(row);
      });
    };
    var add = function (list) {
      var errs = [];
      Array.prototype.forEach.call(list, function (f) {
        var k = kind(f);
        if (!k) { errs.push('«' + f.name + '» desteklenmiyor (JPG, PNG, MP4, MOV).'); return; }
        if (f.size > (k === 'photo' ? PHOTO : VIDEO)) { errs.push('«' + f.name + '» çok büyük.'); return; }
        if (files.length >= MAX) { errs.push('En fazla ' + MAX + ' dosya ekleyebilirsiniz.'); return; }
        if (files.some(function (x) { return x.name === f.name && x.size === f.size; })) return;
        files.push(f);
      });
      errBox.textContent = errs.join(' ');
      errBox.hidden = !errs.length;
      sync();
    };

    document.getElementById('ad-pick').addEventListener('click', function () { input.click(); });
    var more = document.getElementById('ad-more');
    if (more) more.addEventListener('click', function () { input.click(); });
    input.addEventListener('change', function () {
      var picked = Array.prototype.slice.call(input.files);
      input.value = '';
      add(picked);
    });
    listEl.addEventListener('click', function (e) {
      var b = e.target.closest('button[data-i]');
      if (b) { files.splice(parseInt(b.dataset.i, 10), 1); errBox.hidden = true; sync(); }
    });
    ['dragenter', 'dragover'].forEach(function (ev) {
      drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('is-over'); });
    });
    ['dragleave', 'drop'].forEach(function (ev) {
      drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('is-over'); });
    });
    drop.addEventListener('drop', function (e) { add(e.dataTransfer.files); });
  }

  // ---- Seçili teknisyen özeti + çift gönderimi engelleme ----
  var sel = document.getElementById('ad-tech'), out = document.getElementById('ad-selected'),
      form = document.getElementById('ad-form'), note = document.getElementById('ad-note');
  if (sel && out) {
    var oneri = null;      // son AI önerisi: {id, ad, uzmanlik, is, gerekce, ariza_turu}
    var elle = false;      // yönetici listeden kendisi seçtiyse AI önerisi seçimi değiştirmez
    var show = function () {
      var o = sel.options[sel.selectedIndex];
      out.textContent = '';
      if (note) note.disabled = !(o && o.value);  // teknisyen seçilmediyse not yazılamaz
      if (oneri && o && o.value === String(oneri.id)) {
        var b0 = document.createElement('b'); b0.textContent = oneri.ad;
        out.appendChild(document.createTextNode('🤖 Yapay zekâ önerisi: ')); out.appendChild(b0);
        out.appendChild(document.createTextNode(oneri.uzmanlik
          ? ' — uzmanlık «' + oneri.uzmanlik + '», ' + oneri.is + ' açık iş' + (oneri.gerekce ? '. ' + oneri.gerekce : '')
          : ' — arızaya uygun uzman bulunamadı, iş yükü en az olan seçildi (' + oneri.is + ' açık iş).'));
        if (oneri.ariza_turu) out.appendChild(document.createTextNode(' Arıza türü: ' + oneri.ariza_turu + '.'));
        out.appendChild(document.createTextNode(' İsterseniz listeden değiştirebilirsiniz.'));
        return;
      }
      if (!o || !o.value) {
        out.appendChild(document.createTextNode('Teknisyen seçilmedi — kayıt “Teslim alındı” durumunda atama bekler.'));
        return;
      }
      out.appendChild(document.createTextNode('Seçili teknisyen: '));
      var b = document.createElement('b');
      b.textContent = o.getAttribute('data-name');
      out.appendChild(b);
      out.appendChild(document.createTextNode(' — atama sonrası teknisyene bildirim gider.'));
    };
    // Sunucuya seçimin kaynağı bildirilir: default (iş yükü) → kayıtta sunucu uzmanlığa göre seçer; ai / manual → dokunmaz
    var kaynak = document.getElementById('ad-tech-source');
    sel.addEventListener('change', function () { elle = true; if (kaynak) kaynak.value = 'manual'; show(); });
    show();

    // ---- Arıza yazılınca: AI uygun uzmanlığı seçer, o uzmanlıkta en az işi olan teknisyen listede seçili gelir ----
    var issue = document.getElementById('ad-issue');
    if (sel.dataset.aiUrl && issue) {
      var son = '', zaman = null, deger = function (id) { var e = document.getElementById(id); return e ? e.value.trim() : ''; };
      var iste = function () {
        var metin = issue.value.trim(), tur = document.getElementById('ad-type'),
            anahtar = [metin, deger('ad-type'), deger('ad-brand'), deger('ad-model')].join('|');
        if (metin.length < 8 || anahtar === son || elle) return;
        son = anahtar;
        out.textContent = '🤖 Yapay zekâ arızaya uygun teknisyeni seçiyor…';
        var fd = new FormData();
        fd.append('aciklama', metin);
        fd.append('tur', tur && tur.selectedIndex >= 0 ? tur.options[tur.selectedIndex].text : '');
        fd.append('marka', deger('ad-brand')); fd.append('model', deger('ad-model'));
        // Yalnızca anonimleştirme için: bu bilgiler metinde geçerse sunucuda maskelenir, yapay zekâya gitmez
        fd.append('ad', deger('ad-name')); fd.append('telefon', deger('ad-phone')); fd.append('eposta', deger('ad-email'));
        var csrf = form && form.querySelector('[name=csrfmiddlewaretoken]');
        fetch(sel.dataset.aiUrl, { method: 'POST', body: fd, credentials: 'same-origin',
                                   headers: { 'X-CSRFToken': csrf ? csrf.value : '' } })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (elle || anahtar !== son) return;  // bu arada yönetici seçtiyse / metin değiştiyse dokunma
            var opt = d.ok && sel.querySelector('option[value="' + d.id + '"]:not([disabled])');
            if (opt) { oneri = d; sel.value = String(d.id); if (kaynak) kaynak.value = 'ai'; }
            show();
            if (!opt && d.hata) out.insertBefore(document.createTextNode(d.hata + ' '), out.firstChild);
          })
          .catch(function () { show(); });
      };
      var planla = function () { clearTimeout(zaman); zaman = setTimeout(iste, 900); };
      issue.addEventListener('input', planla);
      issue.addEventListener('blur', iste);
      ['ad-type', 'ad-brand', 'ad-model'].forEach(function (id) {
        var e = document.getElementById(id); if (e) e.addEventListener('change', planla);
      });
      // Form hatayla geri döndüyse yöneticinin gönderdiği seçim korunur: metin değişmedikçe öneri istenmez
      if (issue.value.trim()) son = [issue.value.trim(), deger('ad-type'), deger('ad-brand'), deger('ad-model')].join('|');
    }
  }
  if (form) {
    form.addEventListener('submit', function () {
      var btn = form.querySelector('button[type=submit]');
      if (btn) setTimeout(function () { btn.disabled = true; }, 0);  // çift tıklamayla iki kayıt açılmasın
    });
  }
})();
