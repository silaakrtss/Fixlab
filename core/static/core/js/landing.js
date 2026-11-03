(function () {
  'use strict';
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* Slayt gösterisi */
  var slides = [].slice.call(document.querySelectorAll('[data-slide]'));
  var dots = [].slice.call(document.querySelectorAll('[data-dot]'));
  var devices = [].slice.call(document.querySelectorAll('[data-device]'));
  var hero = document.querySelector('.ld-hero');
  var idx = 0, timer = null;
  function show(i) {
    idx = (i + slides.length) % slides.length;
    slides.forEach(function (s, n) { s.classList.toggle('is-active', n === idx); s.setAttribute('aria-hidden', n === idx ? 'false' : 'true'); });
    devices.forEach(function (v, n) { v.classList.toggle('is-active', n === idx % devices.length); });
    dots.forEach(function (d, n) { d.classList.toggle('is-active', n === idx); d.setAttribute('aria-current', n === idx ? 'true' : 'false'); });
  }
  function start() { if (!reduce && slides.length > 1) { stop(); timer = setInterval(function () { show(idx + 1); }, 6500); } }
  function stop() { if (timer) { clearInterval(timer); timer = null; } }
  var prev = document.querySelector('[data-prev]'), next = document.querySelector('[data-next]');
  if (prev) prev.addEventListener('click', function () { show(idx - 1); start(); });
  if (next) next.addEventListener('click', function () { show(idx + 1); start(); });
  dots.forEach(function (d, n) { d.addEventListener('click', function () { show(n); start(); }); });
  if (hero) { hero.addEventListener('mouseenter', stop); hero.addEventListener('mouseleave', start); hero.addEventListener('focusin', stop); hero.addEventListener('focusout', start); }
  show(0); start();

  /* SSS akordeonu */
  [].forEach.call(document.querySelectorAll('.ld-q'), function (btn) {
    btn.addEventListener('click', function () {
      var item = btn.parentNode, panel = document.getElementById(btn.getAttribute('aria-controls'));
      var open = btn.getAttribute('aria-expanded') === 'true';
      btn.setAttribute('aria-expanded', open ? 'false' : 'true');
      item.classList.toggle('is-open', !open);
      if (panel) panel.hidden = open;
    });
  });

  /* Mobil menü */
  var burger = document.querySelector('.ld-burger'), nav = document.getElementById('ld-nav');
  function closeMenu() { if (nav) nav.classList.remove('is-open'); if (burger) { burger.setAttribute('aria-expanded', 'false'); burger.setAttribute('aria-label', 'Menüyü aç'); } }
  if (burger && nav) {
    burger.addEventListener('click', function () {
      var open = nav.classList.toggle('is-open');
      burger.setAttribute('aria-expanded', open ? 'true' : 'false');
      burger.setAttribute('aria-label', open ? 'Menüyü kapat' : 'Menüyü aç');
    });
    [].forEach.call(nav.querySelectorAll('a'), function (a) { a.addEventListener('click', closeMenu); });
    window.addEventListener('resize', function () { if (window.innerWidth > 960) closeMenu(); });
  }

  /* Menüde bulunulan bölümü vurgula (sayfa içi bağlantılar CSS ile yumuşak kayar, yeni sayfa açılmaz) */
  var links = {};
  [].forEach.call(document.querySelectorAll('.ld-nav a[href^="#"]'), function (a) { links[a.getAttribute('href').slice(1)] = a; });
  var ids = Object.keys(links);
  if ('IntersectionObserver' in window && ids.length) {
    var visible = {};
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { visible[e.target.id] = e.isIntersecting; });
      var current = ids.filter(function (id) { return visible[id]; }).pop();
      ids.forEach(function (id) { if (id === current) links[id].setAttribute('aria-current', 'true'); else links[id].removeAttribute('aria-current'); });
    }, { rootMargin: '-35% 0px -55% 0px' });
    ids.forEach(function (id) { var el = document.getElementById(id); if (el) io.observe(el); });
  }
})();
