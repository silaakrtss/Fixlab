/* FIXLAB · Bildirimler sayfası: tek bir bildirimi «Okundu olarak işaretle».
   JavaScript yoksa form normal POST ile çalışır; varsa sayfa yenilenmeden yalnızca o kart güncellenir
   ve zil / sol menü okunmamış sayaçları sunucudan dönen sayıyla güncellenir. */
(function () {
  function cookie(name) { var m = document.cookie.match('(?:^|; )' + name + '=([^;]*)'); return m ? decodeURIComponent(m[1]) : ''; }
  function setCounts(n) {
    var text = n > 99 ? '99+' : String(n);
    var bell = document.getElementById('nt-badge');
    if (bell) { bell.textContent = text; bell.hidden = !n; }
    Array.prototype.forEach.call(document.querySelectorAll('[data-nt-count]'), function (el) { el.textContent = text; el.hidden = !n; });
  }
  var DONE = '<span class="nt-read-done"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>Okundu</span>';

  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form.matches || !form.matches('[data-nt-read]')) return;
    e.preventDefault();
    var btn = form.querySelector('button'); if (btn) btn.disabled = true;
    var token = cookie('csrftoken') || (form.querySelector('input[name=csrfmiddlewaretoken]') || {}).value;
    fetch(form.action, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': token, 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        var item = form.closest('[data-nt-item]');
        if (item) { item.classList.remove('unread'); item.classList.add('is-read'); }
        form.insertAdjacentHTML('afterend', DONE); form.remove();
        setCounts(d.unread);
      })
      .catch(function () { if (btn) btn.disabled = false; });
  });
})();
