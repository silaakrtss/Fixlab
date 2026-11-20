/* FIXLAB · Şifre sıfırlama sayfaları (müşteri + personel).
   Kod sayfasında 6 kutulu kod girişi, yeni şifre sayfasında şifre denetimi ve göz düğmeleri. */
(function () {
  var form = document.getElementById('reset-form');
  if (!form) return;

  /* --- 6 haneli kod kutuları --- */
  var boxes = Array.prototype.slice.call(document.querySelectorAll('#code-boxes input'));
  var hidden = document.getElementById('code-value');
  if (boxes.length && hidden) {
    var digits = function (v) { return (v || '').replace(/\D/g, ''); };
    var sync = function () { hidden.value = boxes.map(function (b) { return b.value; }).join(''); };
    var fill = function (start, text) {
      var d = digits(text), i = start;
      for (var k = 0; k < d.length && i < boxes.length; k++, i++) { boxes[i].value = d.charAt(k); }
      sync();
      boxes[Math.min(i, boxes.length - 1)].focus();
    };
    boxes.forEach(function (b, i) {
      b.addEventListener('input', function () {
        var d = digits(b.value);
        if (d.length > 1) { b.value = ''; fill(i, d); return; }   // otomatik doldurma / çok karakterli giriş
        b.value = d; sync();
        if (d && i < boxes.length - 1) boxes[i + 1].focus();
      });
      b.addEventListener('keydown', function (e) {
        if (e.key === 'Backspace' && !b.value && i > 0) { boxes[i - 1].value = ''; boxes[i - 1].focus(); sync(); e.preventDefault(); }
        else if (e.key === 'ArrowLeft' && i > 0) { boxes[i - 1].focus(); e.preventDefault(); }
        else if (e.key === 'ArrowRight' && i < boxes.length - 1) { boxes[i + 1].focus(); e.preventDefault(); }
      });
      b.addEventListener('paste', function (e) {
        var t = (e.clipboardData || window.clipboardData).getData('text');
        e.preventDefault(); fill(i, t);
      });
      b.addEventListener('focus', function () { b.select(); });
    });
    form.addEventListener('submit', function (e) {
      sync();
      if (hidden.value.length !== 6) {
        e.preventDefault();
        var first = boxes.filter(function (b) { return !b.value; })[0] || boxes[0];
        first.setCustomValidity('6 haneli kodu girin.'); first.reportValidity();
        first.addEventListener('input', function () { first.setCustomValidity(''); }, { once: true });
      }
    });
  }

  /* --- Yeni şifre alanları --- */
  var p1 = form.elements['password1'], p2 = form.elements['password2'];
  if (p1 && p2) {
    form.addEventListener('submit', function (e) {
      if (p1.value.length < 12) { e.preventDefault(); p1.setCustomValidity('Şifre en az 12 karakter olmalıdır.'); p1.reportValidity(); return; }
      if (p1.value !== p2.value) { e.preventDefault(); p2.setCustomValidity('Girilen şifreler eşleşmiyor.'); p2.reportValidity(); }
    });
    [p1, p2].forEach(function (el) { el.addEventListener('input', function () { this.setCustomValidity(''); }); });
  }

  /* --- Şifreyi göster / gizle --- */
  document.querySelectorAll('.cp-eye').forEach(function (b) {
    b.addEventListener('click', function () {
      var inp = document.getElementById(b.getAttribute('data-target'));
      var show = inp.type === 'password';
      inp.type = show ? 'text' : 'password';
      b.setAttribute('aria-pressed', show ? 'true' : 'false');
      b.setAttribute('aria-label', show ? 'Şifreyi gizle' : 'Şifreyi göster');
    });
  });
})();
