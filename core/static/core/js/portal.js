/* FIXLAB · Müşteri portalı sayfa davranışları (cihaz kaydı formu, kopyala düğmesi). */
(function () {
  var root = document.body;

  // ---- Teslim yöntemi: kargo / kendim ----
  var radios = document.querySelectorAll('input[name="delivery_method"]');
  function applyDelivery() {
    var v = 'self';
    radios.forEach(function (r) { if (r.checked) v = r.value; r.closest('.pt-method').classList.toggle('is-selected', r.checked); });
    root.setAttribute('data-delivery', v);
  }
  if (radios.length) { radios.forEach(function (r) { r.addEventListener('change', applyDelivery); }); applyDelivery(); }

  // ---- Marka: «Diğer» seçilirse serbest metin ----
  var brand = document.getElementById('f-brand'), other = document.getElementById('f-brand-other-wrap');
  function applyBrand() { if (brand && other) other.hidden = brand.value !== '__other__'; }
  if (brand) { brand.addEventListener('change', applyBrand); applyBrand(); }

  // ---- Dosya ekleme (sürükle-bırak + liste) ----
  var input = document.getElementById('f-files');
  if (input && window.DataTransfer) {
    var drop = document.getElementById('f-drop'), listEl = document.getElementById('f-filelist'),
        head = document.getElementById('f-filehead'), count = document.getElementById('f-filecount'),
        errBox = document.getElementById('f-fileerr');
    var MAX = parseInt(input.dataset.max, 10), PHOTO = parseInt(input.dataset.photoMb, 10) * 1048576,
        VIDEO = parseInt(input.dataset.videoMb, 10) * 1048576;
    var files = [];
    function ext(n) { var i = n.lastIndexOf('.'); return i < 0 ? '' : n.slice(i + 1).toLowerCase(); }
    function kind(f) { var e = ext(f.name); return ['jpg', 'jpeg', 'png'].indexOf(e) > -1 ? 'photo' : ['mp4', 'mov'].indexOf(e) > -1 ? 'video' : null; }
    function size(b) { return b >= 1048576 ? (b / 1048576).toFixed(1).replace('.', ',') + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB'; }
    var ICON_PHOTO = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/></svg>';
    var ICON_VIDEO = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="6" width="14" height="12" rx="2"/><path d="m22 8-6 4 6 4z"/></svg>';
    function esc(t) { var d = document.createElement('div'); d.textContent = t; return d.innerHTML; }

    function sync() {
      var dt = new DataTransfer(); files.forEach(function (f) { dt.items.add(f); }); input.files = dt.files;
      head.hidden = !files.length; count.textContent = files.length + ' dosya';
      listEl.innerHTML = '';
      files.forEach(function (f, i) {
        var k = kind(f), li = document.createElement('div'); li.className = 'pt-file';
        li.innerHTML = '<div class="pt-ico">' + (k === 'video' ? ICON_VIDEO : ICON_PHOTO) + '</div><div><b>' + esc(f.name) + '</b><small>' +
          (k === 'video' ? 'Video' : 'Fotoğraf') + ' · ' + esc(ext(f.name).toUpperCase()) + ' · ' + size(f.size) + '</small></div><button type="button" data-i="' + i + '">Kaldır</button>';
        listEl.appendChild(li);
      });
    }
    function add(list) {
      var errs = [];
      Array.prototype.forEach.call(list, function (f) {
        var k = kind(f);
        if (!k) { errs.push('«' + f.name + '» desteklenmiyor (JPG, PNG, MP4, MOV).'); return; }
        if (f.size > (k === 'photo' ? PHOTO : VIDEO)) { errs.push('«' + f.name + '» çok büyük.'); return; }
        if (files.length >= MAX) { errs.push('En fazla ' + MAX + ' dosya ekleyebilirsiniz.'); return; }
        if (files.some(function (x) { return x.name === f.name && x.size === f.size; })) return;
        files.push(f);
      });
      errBox.textContent = errs.join(' '); errBox.hidden = !errs.length; sync();
    }
    document.getElementById('f-pick').addEventListener('click', function () { input.click(); });
    var more = document.getElementById('f-more'); if (more) more.addEventListener('click', function () { input.click(); });
    // Tarayıcı seçim penceresi input.files'ı değiştirir; seçilenleri listeye ekleyip input'u senkronla.
    input.addEventListener('change', function () { var picked = Array.prototype.slice.call(input.files); input.value = ''; add(picked); });
    listEl.addEventListener('click', function (e) { var b = e.target.closest('button[data-i]'); if (b) { files.splice(parseInt(b.dataset.i, 10), 1); errBox.hidden = true; sync(); } });
    ['dragenter', 'dragover'].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('is-over'); }); });
    ['dragleave', 'drop'].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('is-over'); }); });
    drop.addEventListener('drop', function (e) { add(e.dataTransfer.files); });
  }

  // ---- Takip kodunu kopyala ----
  var copyBtn = document.getElementById('copy-code');
  if (copyBtn) copyBtn.addEventListener('click', function () {
    var code = copyBtn.dataset.code, label = copyBtn.querySelector('span');
    function done() { label.textContent = 'Kopyalandı'; setTimeout(function () { label.textContent = 'Kopyala'; }, 2000); }
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(code).then(done);
    else { var t = document.createElement('textarea'); t.value = code; document.body.appendChild(t); t.select(); try { document.execCommand('copy'); done(); } catch (e) {} t.remove(); }
  });

  // ---- Sol alttaki kullanıcı menüsü (Profil / Çıkış Yap) ----
  var uBtn = document.getElementById('pt-user-btn'), uPop = document.getElementById('pt-user-pop');
  if (uBtn && uPop) {
    var setUser = function (open) { uPop.hidden = !open; uBtn.setAttribute('aria-expanded', open ? 'true' : 'false'); };
    uBtn.addEventListener('click', function (e) { e.stopPropagation(); setUser(uPop.hidden); });
    document.addEventListener('click', function (e) { if (!uPop.hidden && !uPop.contains(e.target)) setUser(false); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !uPop.hidden) { setUser(false); uBtn.focus(); } });
  }

  // ---- Açık / koyu tema ----
  var themeBtn = document.getElementById('pt-theme');
  if (themeBtn) {
    var html = document.documentElement;
    var paintTheme = function () {
      var dark = html.getAttribute('data-theme') === 'dark';
      themeBtn.setAttribute('aria-pressed', dark ? 'true' : 'false');
      themeBtn.setAttribute('aria-label', dark ? 'Açık temaya geç' : 'Koyu temaya geç');
    };
    paintTheme();
    themeBtn.addEventListener('click', function () {
      var dark = html.getAttribute('data-theme') === 'dark';
      if (dark) html.removeAttribute('data-theme'); else html.setAttribute('data-theme', 'dark');
      try { localStorage.setItem('fx-theme', dark ? 'light' : 'dark'); } catch (e) {}
      paintTheme();
    });
  }
})();
