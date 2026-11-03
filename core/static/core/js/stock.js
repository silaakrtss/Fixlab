/* FIXLAB · Stok Depo listesi: arama + «Sadece kritik olanlar» filtresi (sayfa yenilemeden). */
(function () {
  var input = document.getElementById('st-search'), crit = document.getElementById('st-crit'),
      table = document.getElementById('st-table'), none = document.getElementById('st-none');
  if (!input || !crit || !table) return;
  var rows = Array.prototype.slice.call(table.querySelectorAll('tbody tr[data-search]'));
  function norm(t) { return (t || '').toLocaleLowerCase('tr-TR').trim(); }
  function apply() {
    var q = norm(input.value), onlyCrit = crit.getAttribute('aria-pressed') === 'true', shown = 0;
    rows.forEach(function (r) {
      var ok = (!q || norm(r.dataset.search).indexOf(q) !== -1) && (!onlyCrit || r.dataset.critical === '1');
      r.hidden = !ok; if (ok) shown++;
    });
    if (none && rows.length) { none.hidden = shown !== 0; }
  }
  input.addEventListener('input', apply);
  crit.addEventListener('click', function () {
    crit.setAttribute('aria-pressed', crit.getAttribute('aria-pressed') === 'true' ? 'false' : 'true'); apply();
  });
})();
