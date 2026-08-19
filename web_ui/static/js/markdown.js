/* Minimal markdown renderer with syntax highlighting.
 *
 * Self-contained on purpose: no CDN, no build step, works offline. Everything
 * is HTML-escaped before any markup is generated, so model output can never
 * inject nodes into the page.
 */
(function (global) {
  'use strict';

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /* Syntax highlighting via highlight.js (vendored, loaded in base.html).
   * Takes RAW code and returns escaped, token-wrapped HTML. Falls back to plain
   * escaped text when the language is unknown or the blob is too big. */
  const MAX_HIGHLIGHT_CHARS = 40000;

  function highlight(code, lang) {
    if (!code) return '';
    const key = (lang || '').toLowerCase();
    if (key && code.length <= MAX_HIGHLIGHT_CHARS && global.hljs) {
      try {
        return global.hljs.highlight(code, { language: key, ignoreIllegals: true }).value;
      } catch (_) { /* unknown language: fall through to plain text */ }
    }
    return escapeHtml(code);
  }

  /* Add a line-number gutter to already-highlighted HTML.
   *
   * Split on newlines rather than wrapping each source line before
   * highlighting: highlight.js needs the whole block to get multi-line strings
   * and comments right. A span left open at a line break is closed and
   * reopened so each row is valid on its own.
   *
   * The numbers live in a `::before` on each row, not in the text, so copying
   * the block copies code and not a column of digits. */
  function withLineNumbers(html, startLine) {
    const rows = html.split('\n');
    let open = [];
    const out = rows.map((row, idx) => {
      const prefix = open.length ? open.join('') : '';
      // Track spans that cross the line break.
      const tags = row.match(/<span[^>]*>|<\/span>/g) || [];
      for (const tag of tags) {
        if (tag === '</span>') open.pop();
        else open.push(tag);
      }
      const suffix = '</span>'.repeat(open.length);
      const n = (startLine || 1) + idx;
      return '<span class="code-row" data-line="' + n + '">' +
             prefix + row + suffix + '</span>';
    });
    // The gutter is sized to the widest number it will actually hold, rather
    // than to a fixed width guessing at four digits: a five-line block gets a
    // one-character column, a file opened at line 1200 gets four.
    const digits = String((startLine || 1) + rows.length - 1).length;
    // Joined with nothing: each row is a block element, so it breaks the line
    // by itself. Keeping the newlines as well made every line render at double
    // height -- once for the block, once for the literal newline inside <pre>.
    return '<span class="code-lines" style="--digits:' + digits + '">' +
           out.join('') + '</span>';
  }

  /* Two shapes are recognised.
   *
   * A path with a slash in it, anywhere. And -- because the file the assistant
   * most often means is in the top of the project and so has no slash at all
   * ("app.js:3-7") -- a bare filename carrying a line reference. Requiring the
   * line number there is what keeps ordinary prose out: "Node.js" and
   * "version 1.2" are not paths, and without that rule both would become
   * clickable.
   *
   * A bare image filename is the exception, and needs no line number: nobody
   * writes "chart.png" in a sentence meaning anything other than that file,
   * and an image is the one thing worth showing rather than linking.
   *
   * A path starts with /, ~/, ./, ../, or a directory segment, then runs to the
   * next whitespace/quote/angle. The negative lookbehind stops the pass from
   * re-linkifying a href value the link pass just wrote. Trailing sentence
   * punctuation is split back out so it is not swallowed by the link. The line
   * (and optional range) ride in data attributes the app reads on click. */
  const FILE_REF_TOKEN = /(?<![=">])(^|[\s(["'`])((?:\/|~\/|\.{1,2}\/|[\w@.~\-]+\/)[^\s<>"'`]+|[\w@.~\-]+\.[A-Za-z][\w]{0,5}:\d+(?:-\d+)?|[\w@.~\-]+\.(?:png|jpe?g|gif|webp|bmp|svg))/gi;

  /* "n/a", "and/or", "AC/DC" are prose, not paths. A path is absolute or
   * explicitly relative, nested, or ends in a filename extension. */
  function looksLikePath(p) {
    if (/^(\/|~\/|\.{1,2}\/)/.test(p)) return true;
    if ((p.match(/\//g) || []).length >= 2) return true;
    return /\.[A-Za-z0-9]{1,6}$/.test(p);
  }

  function fileRefReplacer(full, pre, tok) {
    if (/^(https?:\/\/|www\.|mailto:)/i.test(tok)) return full;
    const trail = (tok.match(/[.,;:!?)\]}"'`]+$/) || [''])[0];
    const core = trail ? tok.slice(0, -trail.length) : tok;
    const m = /^(.+?)(?::(\d+)(?:-(\d+))?)?$/.exec(core);
    const path = m ? m[1] : '';
    if (!path || !looksLikePath(path)) return full;
    const line = m[2], end = m[3];
    const display = path + (line ? ':' + line + (end ? '-' + end : '') : '');
    const attrs = ' data-path="' + escapeHtml(path) + '"' +
      (line ? ' data-line="' + line + '"' : '') +
      (end ? ' data-end="' + end + '"' : '');
    return pre + '<button type="button" class="file-ref"' + attrs +
      ' title="Show this in your file manager">' + escapeHtml(display) +
      '</button>' + trail;
  }

  function inline(text) {
    let out = escapeHtml(text);
    // Inline code first so its contents are not treated as markup.
    const codes = [];
    out = out.replace(/`([^`\n]+)`/g, (_, code) => {
      codes.push(code);
      return '\u0000CODE' + (codes.length - 1) + '\u0000';
    });

    // Image URLs come from model output like link URLs do, but only the link
    // half was ever checked -- `![x](javascript:…)` and `![x](data:text/html,…)`
    // went straight into a src attribute. Inert in today's browsers, and the
    // asymmetry was an accident rather than a decision.
    //
    // Images are checked by scheme rather than against the link whitelist,
    // because a relative path is a normal thing to write for a picture in the
    // project and it carries no scheme to abuse.
    const BAD_SCHEME = /^[a-z][a-z0-9+.-]*:/i;
    const safeImg = (u) => !BAD_SCHEME.test(u) || /^https?:/i.test(u);
    out = out
      .replace(/!\[([^\]]*)\]\(([^)\s]+)[^)]*\)/g,
        (_, alt, src) => safeImg(src)
          ? `<img src="${src}" alt="${alt}" loading="lazy">`
          : alt)
      .replace(/\[([^\]]+)\]\(([^)\s]+)[^)]*\)/g,
        (_, label, href) => /^(https?:|\/|#)/.test(href)
          ? `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`
          : label);

    // Bare URLs become links. The lookbehind skips anything already inside a
    // tag we just generated: after escapeHtml, a literal " or > can only have
    // come from our own markup. Stashed so the emphasis pass below cannot eat
    // underscores inside a URL.
    const links = [];
    out = out.replace(/(?<!["=>])\bhttps?:\/\/[^\s<>"'`]+/g, (url) => {
      // Sentence punctuation and trailing emphasis markers are almost never
      // part of the URL. Only a trailing run is stripped, so underscores and
      // asterisks *inside* a path survive.
      const tail = (url.match(/[.,;:!?)\]}*_~]+$/) || [''])[0];
      const href = tail ? url.slice(0, -tail.length) : url;
      if (!/^https?:\/\/[^/]/.test(href)) return url;
      links.push(`<a href="${href}" target="_blank" rel="noopener noreferrer">${href}</a>`);
      return '\u0000LINK' + (links.length - 1) + '\u0000' + tail;
    });

    out = out
      .replace(/(\*\*|__)(?=\S)([\s\S]*?\S)\1/g, '<strong>$2</strong>')
      .replace(/(^|[\s(])(\*|_)(?=\S)([^*_]*?\S)\2/g, '$1<em>$3</em>')
      .replace(/~~(?=\S)([\s\S]*?\S)~~/g, '<del>$1</del>')
      // File paths (absolute or relative) and file:line references become links
      // that open the in-app editor. URLs are stashed above, so anything left
      // starting with / or a dir/ segment is a path, not a link.
      .replace(FILE_REF_TOKEN, fileRefReplacer);

    return out
      .replace(/\u0000LINK(\d+)\u0000/g, (_, i) => links[+i])
      .replace(/\u0000CODE(\d+)\u0000/g,
        (_, i) => '<code>' +
          codes[+i].replace(FILE_REF_TOKEN, fileRefReplacer) +
          '</code>');
  }

  function render(src) {
    if (!src) return '';
    const lines = String(src).replace(/\r\n?/g, '\n').split('\n');
    const html = [];
    let i = 0;

    const listStack = [];
    function closeLists(toDepth) {
      while (listStack.length > toDepth) html.push(listStack.pop() === 'ol' ? '</ol>' : '</ul>');
    }

    while (i < lines.length) {
      const line = lines[i];

      // Fenced code block
      const fence = line.match(/^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/);
      if (fence) {
        closeLists(0);
        const marker = fence[1][0];
        const lang = fence[2] || '';
        const body = [];
        i++;
        while (i < lines.length && !new RegExp('^\\s*' + marker + '{3,}\\s*$').test(lines[i])) {
          body.push(lines[i]);
          i++;
        }
        i++;
        const code = body.join('\n');
        html.push(
          '<div class="code-block" data-code="' + escapeHtml(code) + '">' +
          '<div class="code-head"><span class="code-lang">' + escapeHtml(lang || 'text') + '</span>' +
          '<button type="button" class="code-copy" onclick="copyCode(this)" title="Copy to clipboard">' +
          '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" fill="none" stroke="currentColor" stroke-width="2"/></svg>' +
          '</button></div>' +
          '<pre><code>' + withLineNumbers(highlight(code, lang), 1) + '</code></pre></div>'
        );
        continue;
      }

      // Heading
      const heading = line.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        closeLists(0);
        const level = heading[1].length;
        html.push(`<h${level}>${inline(heading[2])}</h${level}>`);
        i++;
        continue;
      }

      // Horizontal rule
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
        closeLists(0);
        html.push('<hr>');
        i++;
        continue;
      }

      // Blockquote
      if (/^\s*>\s?/.test(line)) {
        closeLists(0);
        const quote = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          quote.push(lines[i].replace(/^\s*>\s?/, ''));
          i++;
        }
        html.push('<blockquote>' + render(quote.join('\n')) + '</blockquote>');
        continue;
      }

      // Table
      if (/\|/.test(line) && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1])) {
        closeLists(0);
        const cells = (row) => row.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|');
        // Wrapped so the *wrapper* scrolls when the table is too wide. Making
        // the table itself `display: block` also works for scrolling, but it
        // makes the row group shrink-wrap, so the header tint and the row
        // rules stop short of the right edge.
        html.push('<div class="table-wrap"><table><thead><tr>' +
          cells(line).map((c) => `<th>${inline(c.trim())}</th>`).join('') +
          '</tr></thead><tbody>');
        i += 2;
        while (i < lines.length && /\|/.test(lines[i]) && lines[i].trim()) {
          html.push('<tr>' + cells(lines[i]).map((c) => `<td>${inline(c.trim())}</td>`).join('') + '</tr>');
          i++;
        }
        html.push('</tbody></table></div>');
        continue;
      }

      // List item
      const item = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
      if (item) {
        const depth = Math.floor(item[1].replace(/\t/g, '  ').length / 2) + 1;
        const kind = /^\d/.test(item[2]) ? 'ol' : 'ul';
        while (listStack.length > depth) closeLists(listStack.length - 1);
        while (listStack.length < depth) {
          // Honour the author's first number, so a list starting at 3 does.
          const from = kind === 'ol' ? parseInt(item[2], 10) : 1;
          html.push(kind === 'ul' ? '<ul>' : (from > 1 ? `<ol start="${from}">` : '<ol>'));
          listStack.push(kind);
        }
        html.push('<li>' + inline(item[3]) + '</li>');
        i++;
        continue;
      }

      // Blank line
      if (!line.trim()) {
        // A blank line between items makes one loose list, not several. Closing
        // unconditionally started a fresh <ol> per item, so every one of them
        // rendered as "1."
        const next = lines.slice(i + 1).find((l) => l.trim());
        const listContinues = listStack.length && next
          && /^(\s*)([-*+]|\d+[.)])\s+/.test(next);
        if (!listContinues) closeLists(0);
        i++;
        continue;
      }

      // Paragraph
      closeLists(0);
      const para = [];
      while (i < lines.length && lines[i].trim() &&
             !/^\s*(`{3,}|~{3,})/.test(lines[i]) &&
             !/^(#{1,6})\s/.test(lines[i]) &&
             !/^\s*([-*+]|\d+[.)])\s/.test(lines[i]) &&
             !/^\s*>/.test(lines[i])) {
        para.push(lines[i]);
        i++;
      }
      html.push('<p>' + inline(para.join(' ')) + '</p>');
    }

    closeLists(0);
    return html.join('\n');
  }

  global.md = { render, escapeHtml, highlight, withLineNumbers };
})(window);

function copyCode(button) {
  const block = button.closest('.code-block');
  const code = block ? block.dataset.code : '';
  navigator.clipboard.writeText(code).then(() => {
    button.classList.add('copied');
    showCopyToast();
    setTimeout(() => button.classList.remove('copied'), 1400);
  }).catch(() => {});
}
