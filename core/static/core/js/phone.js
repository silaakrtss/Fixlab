/* FIXLAB · Telefon alanları: yazarken ya da yapıştırırken numara «(533) 305 54 24» biçimine çevrilir.
   0533..., +90 533..., 90533... gibi girişlerde başındaki 0 / 90 atılır. Sunucu da aynı kuralı uygular. */
(function () {
  'use strict';

  function digitsOf(value) {
    var d = String(value || '').replace(/\D/g, '');
    d = d.replace(/^00/, '');
    if (d.indexOf('90') === 0) d = d.slice(2);   // +90 / 90 ülke kodu (Türkiye numaraları 9 ile başlamaz)
    d = d.replace(/^0+/, '');                    // 0 ile başlayan ulusal önek
    return d.slice(0, 10);
  }

  // Ayraçlar yalnızca bir sonraki hane varken eklenir; böylece Backspace ile geri silmek takılmaz.
  function format(d) {
    var s = '';
    if (d.length > 0) s = '(' + d.slice(0, 3);
    if (d.length > 3) s += ') ' + d.slice(3, 6);
    if (d.length > 6) s += ' ' + d.slice(6, 8);
    if (d.length > 8) s += ' ' + d.slice(8, 10);
    return s;
  }

  function caretFor(text, digitsBefore) {
    if (digitsBefore <= 0) return 0;
    var seen = 0;
    for (var i = 0; i < text.length; i++) {
      if (/\d/.test(text.charAt(i))) seen++;
      if (seen === digitsBefore) return i + 1;
    }
    return text.length;
  }

  function apply(input, keepCaret) {
    var before = input.value;
    var atEnd = !keepCaret || input.selectionStart === null || input.selectionStart >= before.length;
    var leftDigits = atEnd ? 0 : before.slice(0, input.selectionStart).replace(/\D/g, '').length;
    var next = format(digitsOf(before));
    if (next === before) return;
    input.value = next;
    if (!atEnd && input.setSelectionRange) {
      var pos = caretFor(next, leftDigits);
      input.setSelectionRange(pos, pos);
    }
  }

  function bind(input) {
    if (input.dataset.phoneBound) return;
    input.dataset.phoneBound = '1';
    if (!input.getAttribute('placeholder') || /05XX|0532/.test(input.getAttribute('placeholder'))) {
      input.setAttribute('placeholder', '(5XX) XXX XX XX');
    }
    input.setAttribute('autocomplete', 'tel-national');
    input.addEventListener('input', function () { apply(input, true); });
    input.addEventListener('paste', function () { setTimeout(function () { apply(input, false); }, 0); });
    apply(input, false);   // sayfa açılırken dolu gelen eski biçimli numaralar da düzelir
  }

  function init() {
    [].forEach.call(document.querySelectorAll('input[type="tel"], input[inputmode="tel"]'), bind);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
