/* Visual editor for Telegram texts: turns <textarea data-rich> into a
   toolbar + editable area. The textarea keeps the Telegram HTML that is
   saved (only b, i, u, s, a and line breaks), so the server side is unchanged. */
(function () {
  'use strict';

  var ALLOWED = { B: 'b', STRONG: 'b', I: 'i', EM: 'i', U: 'u', INS: 'u', S: 's', STRIKE: 's', DEL: 's' };
  var BLOCK = { DIV: 1, P: 1, LI: 1, H1: 1, H2: 1, H3: 1, H4: 1, TR: 1, BLOCKQUOTE: 1 };

  function esc(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function safeUrl(href) {
    return /^(https?:\/\/|tg:\/\/|mailto:)/i.test(href || '') ? href : '';
  }

  // Editable DOM -> Telegram HTML
  function serialize(node) {
    var out = '';
    node.childNodes.forEach(function (n) {
      if (n.nodeType === 3) { out += esc(n.nodeValue.replace(/ /g, ' ')); return; }
      if (n.nodeType !== 1) return;
      var tag = n.tagName;
      if (tag === 'BR') { out += '\n'; return; }
      var inner = serialize(n);
      // Bold/italic set by the browser as inline styles (e.g. pasted from Word / Google Docs).
      var st = n.style || {};
      if (tag === 'SPAN' && inner) {
        if (/^(bold|[6-9]00)$/.test(st.fontWeight)) inner = '<b>' + inner + '</b>';
        if (st.fontStyle === 'italic') inner = '<i>' + inner + '</i>';
        if (/underline/.test(st.textDecorationLine || st.textDecoration || '')) inner = '<u>' + inner + '</u>';
      }
      if (ALLOWED[tag] && inner) {
        out += '<' + ALLOWED[tag] + '>' + inner + '</' + ALLOWED[tag] + '>';
      } else if (tag === 'A' && safeUrl(n.getAttribute('href')) && inner) {
        out += '<a href="' + esc(n.getAttribute('href')).replace(/"/g, '&quot;') + '">' + inner + '</a>';
      } else if (BLOCK[tag]) {
        if (out && !/\n$/.test(out)) out += '\n';
        out += inner;
        if (!/\n$/.test(out)) out += '\n';
      } else {
        out += inner;
      }
    });
    return out;
  }

  function clean(html) {
    return html.replace(/<(b|i|u|s)><\/\1>/g, '').replace(/\n{3,}/g, '\n\n').replace(/^\n+|\s+$/g, '');
  }

  // Telegram HTML -> editable DOM (only allowed tags survive)
  function load(area, html) {
    var doc = new DOMParser().parseFromString('<body>' + (html || '').replace(/\n/g, '<br>') + '</body>', 'text/html');
    area.innerHTML = '';
    (function copy(src, dst) {
      src.childNodes.forEach(function (n) {
        if (n.nodeType === 3) { dst.appendChild(document.createTextNode(n.nodeValue)); return; }
        if (n.nodeType !== 1) return;
        var tag = n.tagName, el;
        if (tag === 'BR') { dst.appendChild(document.createElement('br')); return; }
        if (ALLOWED[tag]) el = document.createElement(ALLOWED[tag]);
        else if (tag === 'A' && safeUrl(n.getAttribute('href'))) {
          el = document.createElement('a');
          el.setAttribute('href', n.getAttribute('href'));
        }
        if (el) { copy(n, el); dst.appendChild(el); } else { copy(n, dst); }
      });
    })(doc.body, area);
  }

  function button(label, title, onClick) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'rte-btn';
    b.innerHTML = label;
    b.title = title;
    b.addEventListener('mousedown', function (e) { e.preventDefault(); }); // keep the selection
    b.addEventListener('click', onClick);
    return b;
  }

  function attach(textarea) {
    if (textarea.dataset.richReady) return;
    textarea.dataset.richReady = '1';
    var wrap = document.createElement('div');
    wrap.className = 'rte';
    var bar = document.createElement('div');
    bar.className = 'rte-bar';
    var area = document.createElement('div');
    area.className = 'rte-area';
    area.contentEditable = 'true';
    area.style.minHeight = (parseInt(textarea.getAttribute('rows') || '8', 10) * 1.5) + 'em';
    var codeMode = false;

    function sync() { textarea.value = clean(serialize(area)); }
    function cmd(name, value) {
      area.focus();
      document.execCommand(name, false, value);
      sync();
    }

    bar.appendChild(button('<b>Ж</b>', 'Жирний (Ctrl+B)', function () { cmd('bold'); }));
    bar.appendChild(button('<i>К</i>', 'Курсив (Ctrl+I)', function () { cmd('italic'); }));
    bar.appendChild(button('<u>П</u>', 'Підкреслений (Ctrl+U)', function () { cmd('underline'); }));
    bar.appendChild(button('<s>З</s>', 'Закреслений', function () { cmd('strikeThrough'); }));
    bar.appendChild(button('🔗 Посилання', 'Зробити виділений текст посиланням', function () {
      var sel = window.getSelection();
      var url = window.prompt('Адреса посилання (https://...)', 'https://');
      if (!url || !safeUrl(url)) return;
      if (sel && !sel.isCollapsed && area.contains(sel.anchorNode)) {
        cmd('createLink', url);
      } else {
        cmd('insertHTML', '<a href="' + esc(url).replace(/"/g, '&quot;') + '">' + esc(url) + '</a>');
      }
    }));
    bar.appendChild(button('⛓️‍💥', 'Прибрати посилання', function () { cmd('unlink'); }));
    bar.appendChild(button('🧹', 'Очистити форматування виділеного', function () { cmd('removeFormat'); cmd('unlink'); }));
    var code = button('&lt;/&gt; HTML', 'Показати / сховати код', function () {
      codeMode = !codeMode;
      if (codeMode) { sync(); } else { load(area, textarea.value); }
      area.style.display = codeMode ? 'none' : '';
      textarea.style.display = codeMode ? '' : 'none';
      code.classList.toggle('on', codeMode);
    });
    code.style.marginLeft = 'auto';
    bar.appendChild(code);

    area.addEventListener('input', sync);
    area.addEventListener('blur', sync);
    // Paste keeps bold/italic/links from Word or Google Docs, drops the rest.
    area.addEventListener('paste', function (e) {
      var data = e.clipboardData;
      if (!data) return;
      e.preventDefault();
      var html = data.getData('text/html');
      if (html) {
        var tmp = document.createElement('div');
        var doc = new DOMParser().parseFromString(html, 'text/html');
        tmp.innerHTML = '';
        tmp.appendChild(document.importNode(doc.body, true));
        var t = document.createElement('div');
        load(t, clean(serialize(tmp)));
        document.execCommand('insertHTML', false, t.innerHTML);
      } else {
        document.execCommand('insertText', false, data.getData('text/plain'));
      }
      sync();
    });
    // Enter = a plain line break (Telegram has no paragraphs).
    area.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); document.execCommand('insertLineBreak'); sync(); }
    });

    textarea.parentNode.insertBefore(wrap, textarea);
    wrap.appendChild(bar);
    wrap.appendChild(area);
    wrap.appendChild(textarea);
    textarea.style.display = 'none';
    textarea.classList.add('rte-code');
    load(area, textarea.value);
    if (textarea.form) textarea.form.addEventListener('submit', function () { if (!codeMode) sync(); });
  }

  function init() { document.querySelectorAll('textarea[data-rich]').forEach(attach); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
