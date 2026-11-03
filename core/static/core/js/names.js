/* FIXLAB · Ad / soyad alanları: yalnızca harf (Türkçe harfler dahil) ve boşluk yazılabilir.
   data-name-only özniteliği olan her alana uygulanır; yazarken ve yapıştırırken harf dışı karakterler atılır.
   Sunucu tarafında aynı kural forms.clean_person_name ile ayrıca denetlenir. */
(function () {
  var MSG = 'Yalnızca harf girebilirsiniz.';
  var NOT_LETTER;
  try { NOT_LETTER = new RegExp('[^\\p{L} ]', 'gu'); } catch (e) { NOT_LETTER = /[0-9_!-\/:-@\[-`{-~]/g; }

  function clean(value) { return value.replace(NOT_LETTER, ''); }

  function bind(input) {
    input.setAttribute('autocapitalize', 'words');
    input.addEventListener('input', function () {
      var cleaned = clean(input.value);
      if (cleaned !== input.value) {
        var pos = input.selectionStart - (input.value.length - cleaned.length);
        input.value = cleaned;
        try { input.setSelectionRange(Math.max(pos, 0), Math.max(pos, 0)); } catch (e) {}
      }
      input.setCustomValidity('');
    });
    input.addEventListener('blur', function () {
      input.value = clean(input.value).replace(/\s+/g, ' ').trim();
    });
    input.addEventListener('invalid', function () {
      if (input.validity.valueMissing) return;
      input.setCustomValidity(MSG);
    });
  }

  function init() {
    var fields = document.querySelectorAll('input[data-name-only]');
    for (var i = 0; i < fields.length; i++) bind(fields[i]);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
