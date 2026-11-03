/* FIXLAB · Tarayıcı bildirimleri.
   Sayfa açıkken sunucuyu düzenli yoklar; yeni bildirim gelince tarayıcı bildirimi (Notification API) gösterir,
   izin verilmemişse sayfa içi uyarı çıkarır. Zil menüsü ve okunmamış sayısı da buradan güncellenir. */
(function () {
  var cfg = document.getElementById('nt-config');
  if (!cfg) return;
  var FEED = cfg.dataset.feed, MARK = cfg.dataset.markread, ICON = cfg.dataset.icon || '';
  var KEY = 'fixlab_nt_last_' + (cfg.dataset.user || '0');
  var INTERVAL = 15000;
  var btn = document.getElementById('nt-btn'), pop = document.getElementById('nt-pop'),
      badge = document.getElementById('nt-badge'), list = document.getElementById('nt-list'),
      perm = document.getElementById('nt-perm'), permBtn = document.getElementById('nt-perm-btn'),
      readAll = document.getElementById('nt-readall');
  var last = null, timer = null;
  var MUTE = 'fixlab_nt_mute_' + (cfg.dataset.user || '0');
  var page = document.getElementById('an-page'), refreshBtn = document.getElementById('an-refresh'),
      pageLatest = page ? parseInt(page.dataset.latest || '0', 10) : 0,
      browserBox = document.getElementById('nt-browser'), browserHint = document.getElementById('nt-browser-hint');
  function muted() { try { return localStorage.getItem(MUTE) === '1'; } catch (e) { return false; } }
  function setMuted(v) { try { if (v) localStorage.setItem(MUTE, '1'); else localStorage.removeItem(MUTE); } catch (e) {} }

  function load() { try { var v = localStorage.getItem(KEY); return v === null ? null : parseInt(v, 10); } catch (e) { return null; } }
  function save(v) { try { localStorage.setItem(KEY, String(v)); } catch (e) {} }
  function cookie(name) { var m = document.cookie.match('(?:^|; )' + name + '=([^;]*)'); return m ? decodeURIComponent(m[1]) : ''; }
  function esc(t) { var d = document.createElement('div'); d.textContent = t == null ? '' : t; return d.innerHTML; }
  var supported = 'Notification' in window;

  function toast(item) {
    var box = document.getElementById('nt-toasts');
    if (!box) { box = document.createElement('div'); box.id = 'nt-toasts'; box.className = 'nt-toasts'; document.body.appendChild(box); }
    var t = document.createElement('div'); t.className = 'nt-toast'; t.setAttribute('role', 'status');
    t.innerHTML = '<b>' + esc(item.title) + '</b>' + esc(item.body);
    t.addEventListener('click', function () { location.href = item.url; });
    box.appendChild(t); setTimeout(function () { t.remove(); }, 9000);
  }

  function show(item) {
    if (muted()) return;  // «Tarayıcı bildirimleri» anahtarı kapalı
    if (supported && Notification.permission === 'granted') {
      try {
        var n = new Notification(item.title, { body: item.body, tag: 'fixlab-' + item.id, icon: ICON });
        n.onclick = function () { window.focus(); location.href = item.url; n.close(); };
        return;
      } catch (e) { /* bazı mobil tarayıcılar yapıcıyı desteklemez: sayfa içi uyarıya düş */ }
    }
    toast(item);
  }

  function setBadge(n) {
    if (!badge) return;
    badge.textContent = n > 99 ? '99+' : n; badge.hidden = !n;
  }
  function setNavBadges(n) {  // sol menüdeki «Bildirimlerim» rozeti
    Array.prototype.forEach.call(document.querySelectorAll('[data-nt-count]'), function (el) {
      el.textContent = n > 99 ? '99+' : n; el.hidden = !n;
    });
  }

  var POPUP_LIMIT = 6;  // zil kutusunda yalnızca son 6 bildirim görünür; gerisi «Tümünü görüntüle» ile açılır
  function render(items) {
    if (!list) return;
    items = items.slice(0, POPUP_LIMIT);
    if (!items.length) { list.innerHTML = '<li class="nt-empty">Henüz bildiriminiz yok.</li>'; return; }
    list.innerHTML = items.map(function (n) {
      return '<li class="' + (n.read ? '' : 'unread') + '"><a href="' + esc(n.url) + '"><b>' + esc(n.title) + '</b>' +
        (n.body ? '<span>' + esc(n.body) + '</span>' : '') + '<time>' + esc(n.time) + '</time></a></li>';
    }).join('');
  }

  function updatePermBox() {
    if (!perm) return;
    perm.hidden = !(supported && Notification.permission === 'default');
  }

  function poll() {
    fetch(FEED + '?after=' + (last === null ? 0 : last), { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        if (last === null) { last = d.latest; save(last); }       // ilk açılışta eski bildirimleri tekrar gösterme
        else {
          // Başka sekme zaten gösterdiyse (ortak localStorage) tekrar gösterme
          var stored = load(); var seen = stored === null ? last : Math.max(stored, last);
          d.new.forEach(function (n) { if (n.id > seen) show(n); });
          if (d.latest > last) { last = d.latest; save(Math.max(last, seen)); }
        }
        setBadge(d.unread); setNavBadges(d.unread); render(d.recent);
        if (refreshBtn && d.latest > pageLatest) refreshBtn.hidden = false;  // bildirim paneli açıkken yeni kayıt geldi
      })
      .catch(function () { /* ağ yok: bir sonraki turda tekrar dene */ });
  }

  function start() { poll(); timer = setInterval(function () { if (!document.hidden) poll(); }, INTERVAL); }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });

  if (btn && pop) {
    btn.addEventListener('click', function (e) {
      e.stopPropagation(); var open = pop.hidden; pop.hidden = !open;
      btn.setAttribute('aria-expanded', open ? 'true' : 'false'); updatePermBox(); if (open) poll();
    });
    document.addEventListener('click', function (e) { if (!pop.contains(e.target) && e.target !== btn) { pop.hidden = true; btn.setAttribute('aria-expanded', 'false'); } });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { pop.hidden = true; btn.setAttribute('aria-expanded', 'false'); } });
  }
  if (permBtn && supported) permBtn.addEventListener('click', function () {
    Notification.requestPermission().then(function () { updatePermBox(); });
  });
  if (readAll) readAll.addEventListener('submit', function (e) {
    e.preventDefault();
    fetch(MARK, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': cookie('csrftoken') || (readAll.querySelector('input[name=csrfmiddlewaretoken]') || {}).value, 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function () { setBadge(0); poll(); });
  });

  // Bildirimlerim sayfasındaki «Tarayıcı bildirimleri» anahtarı
  function syncBrowserBox() {
    if (!browserBox) return;
    if (!supported) { browserBox.disabled = true; browserBox.checked = false; showHint('Bu tarayıcı bildirimleri desteklemiyor.'); return; }
    if (Notification.permission === 'denied') { browserBox.disabled = true; browserBox.checked = false; showHint('Bildirimler tarayıcı ayarlarında engellenmiş. Adres çubuğundaki site ayarlarından izin verin.'); return; }
    browserBox.disabled = false;
    browserBox.checked = Notification.permission === 'granted' && !muted();
    showHint(Notification.permission === 'default' ? 'Açtığınızda tarayıcı sizden izin isteyecek. Bildirimler sayfa/sekme açıkken gelir.' : '');
  }
  function showHint(t) { if (browserHint) { browserHint.textContent = t; browserHint.hidden = !t; } }
  if (browserBox) {
    browserBox.addEventListener('change', function () {
      if (!browserBox.checked) { setMuted(true); syncBrowserBox(); return; }
      if (Notification.permission === 'granted') { setMuted(false); syncBrowserBox(); return; }
      Notification.requestPermission().then(function (p) { if (p === 'granted') setMuted(false); syncBrowserBox(); });
    });
    syncBrowserBox();
  }

  last = load();
  start();
})();
