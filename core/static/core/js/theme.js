/* FIXLAB · Açık / koyu tema düğmesi (admin ve teknisyen panelleri).
   Seçim tarayıcıda "fx-theme" anahtarıyla saklanır; müşteri paneliyle ortaktır. */
(function () {
  'use strict';
  var html = document.documentElement;
  var buttons = [].slice.call(document.querySelectorAll('[data-theme-toggle]'));
  if (!buttons.length) return;

  function isDark() { return html.getAttribute('data-theme') === 'dark'; }

  function paint() {
    var dark = isDark();
    buttons.forEach(function (btn) {
      btn.setAttribute('aria-pressed', dark ? 'true' : 'false');
      btn.setAttribute('aria-label', dark ? 'Açık temaya geç' : 'Koyu temaya geç');
      var label = btn.querySelector('.tt-label');
      if (label) label.textContent = dark ? 'Açık tema' : 'Koyu tema';
    });
  }

  buttons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      var dark = isDark();
      if (dark) html.removeAttribute('data-theme'); else html.setAttribute('data-theme', 'dark');
      try { localStorage.setItem('fx-theme', dark ? 'light' : 'dark'); } catch (e) {}
      paint();
    });
  });
  paint();
})();
