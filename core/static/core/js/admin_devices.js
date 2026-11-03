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
    var show = function () {
      var o = sel.options[sel.selectedIndex];
      out.textContent = '';
      if (note) note.disabled = !(o && o.value);  // teknisyen seçilmediyse not yazılamaz
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
    sel.addEventListener('change', show);
    show();
  }
  if (form) {
    form.addEventListener('submit', function () {
      var btn = form.querySelector('button[type=submit]');
      if (btn) setTimeout(function () { btn.disabled = true; }, 0);  // çift tıklamayla iki kayıt açılmasın
    });
  }
})();
