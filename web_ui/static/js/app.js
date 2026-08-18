/* The chat client: streaming, a single status line, dictation, and read-aloud.
 *
 * There is deliberately no tool-call transcript — the user sees a conversation
 * and one status line that says, in plain words, what the assistant is doing.
 */
(function () {
  // ── Theme (the server is the source of truth) ─────────────────────────────

  async function refreshTheme() {
    try {
      const data = await fetch('/api/theme').then((r) => r.json());
      if (data.theme) document.documentElement.dataset.theme = data.theme;
    } catch (e) {}
  }

  // ── Microphone chooser (any page that shows one) ──────────────────────────

  function micDeviceId() {
    try { return localStorage.getItem('micDeviceId') || ''; } catch (e) { return ''; }
  }
  function saveMicDevice(id) {
    try {
      if (id) localStorage.setItem('micDeviceId', id);
      else localStorage.removeItem('micDeviceId');
    } catch (e) {}
  }
  async function populateMicChoosers() {
    const selects = document.querySelectorAll('[data-mic-chooser]');
    if (!selects.length || !navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
    let devices = [];
    try { devices = await navigator.mediaDevices.enumerateDevices(); } catch (e) { return; }
    const current = micDeviceId();
    selects.forEach((sel) => {
      devices.filter((d) => d.kind === 'audioinput').forEach((d, i) => {
        const opt = document.createElement('option');
        opt.value = d.deviceId;
        opt.textContent = d.label || 'Microphone ' + (i + 1);
        if (d.deviceId === current) opt.selected = true;
        sel.appendChild(opt);
      });
      sel.addEventListener('change', () => saveMicDevice(sel.value));
    });
  }
  populateMicChoosers();

  // ── Text size (browser zoom), remembered between launches ──────────────────
  // A settings control scales the whole app up or down (the way Ctrl+wheel
  // would), and the choice is saved so the next launch starts at the same size.
  window.__readZoom = function () {
    try {
      const v = parseFloat(localStorage.getItem('appZoom'));
      return Number.isFinite(v) ? v : 1;
    } catch (e) { return 1; }
  };
  window.__applyZoom = function (z) {
    z = Math.min(1.6, Math.max(0.7, Number(z) || 1));
    document.documentElement.style.zoom = z;
    try { localStorage.setItem('appZoom', String(z)); } catch (e) {}
    const el = document.getElementById('zoom-value');
    if (el) el.textContent = Math.round(z * 100) + '%';
    return z;
  };
  window.__applyZoom(window.__readZoom());

  // ── Modals + Escape ────────────────────────────────────────────────────────
  // Escape closes whichever modal is open first (restoring focus to whatever
  // opened it). Only when nothing is open does it jump back to the skip link,
  // so after tabbing deep into a long chat you can reach the message box without
  // tabbing through every message in reverse. Exposed on window so the settings
  // page's modals share the same behaviour.
  window.__openModal = function (el, focusEl) {
    window.__modalEl = el;
    window.__modalReturn = document.activeElement;
    el.hidden = false;
    if (focusEl) focusEl.focus();
  };
  window.__closeModal = function () {
    if (!window.__modalEl) return false;
    const el = window.__modalEl;
    const ret = window.__modalReturn;
    window.__modalEl = null;
    window.__modalReturn = null;
    el.hidden = true;
    if (ret && ret.focus) ret.focus();
    return true;
  };

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (window.__closeModal()) return;  // a modal was open; Escape just closes it
    const skip = document.getElementById('skip-link');
    if (skip) { skip.focus(); return; }
    const el = document.activeElement;
    if (el && el !== document.body && typeof el.blur === 'function') el.blur();
  });

  // Skip links jump past the app bar straight to the real content. On the
  // settings page the target is a plain <main>, so land on its first focusable
  // control rather than an inert container that would need an extra Tab.
  function firstFocusable(root) {
    const sel = 'a[href], button:not([disabled]), input:not([disabled]), ' +
      'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const list = root.querySelectorAll(sel);
    for (const el of list) {
      if (!el.hidden && el.offsetParent !== null) return el;
    }
    return null;
  }
  const skipLinkTop = document.getElementById('skip-link');
  if (skipLinkTop && skipLinkTop.getAttribute('href') === '#main-content') {
    skipLinkTop.addEventListener('click', (e) => {
      e.preventDefault();
      const main = document.getElementById('main-content');
      const target = main ? firstFocusable(main) : null;
      if (target) target.focus();
    });
  }

  // ── App bar: back/forward history, session dropdown, quit ─────────────────

  const backBtn = document.getElementById('back-btn');
  const fwdBtn = document.getElementById('forward-btn');

  // A small persistent navigation history (capped), so back/forward survive
  // closing and reopening the app.
  function navState() {
    try {
      return {
        list: JSON.parse(localStorage.getItem('navHistory') || '[]'),
        index: parseInt(localStorage.getItem('navIndex') || '0', 10) || 0,
      };
    } catch (e) { return { list: [], index: 0 }; }
  }
  function saveNav(list, index) {
    try {
      localStorage.setItem('navHistory', JSON.stringify(list));
      localStorage.setItem('navIndex', String(index));
    } catch (e) {}
  }
  function updateNavButtons() {
    if (!backBtn || !fwdBtn) return;
    const s = navState();
    backBtn.disabled = s.index <= 0;
    fwdBtn.disabled = s.index >= s.list.length - 1;
  }
  function recordCurrentPath() {
    const path = location.pathname + location.search;
    let s = navState();
    if (!s.list.length) {
      s.list = [path];
      s.index = 0;
    } else if (s.list[s.index] !== path) {
      s.list = s.list.slice(0, s.index + 1);
      s.list.push(path);
      s.index = s.list.length - 1;
    }
    if (s.list.length > 50) {
      const excess = s.list.length - 50;
      s.list = s.list.slice(excess);
      s.index = Math.max(0, s.index - excess);
    }
    saveNav(s.list, s.index);
    updateNavButtons();
  }

  if (backBtn) backBtn.addEventListener('click', () => {
    const s = navState();
    if (s.index > 0) {
      s.index -= 1;
      saveNav(s.list, s.index);
      location.href = s.list[s.index];
    }
  });
  if (fwdBtn) fwdBtn.addEventListener('click', () => {
    const s = navState();
    if (s.index < s.list.length - 1) {
      s.index += 1;
      saveNav(s.list, s.index);
      location.href = s.list[s.index];
    }
  });
  recordCurrentPath();

  const sessionsBtn = document.getElementById('sessions-btn');
  const sessionsMenu = document.getElementById('sessions-menu');
  if (sessionsBtn && sessionsMenu) {
    sessionsBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = !sessionsMenu.hidden;
      sessionsMenu.hidden = open;
      sessionsBtn.setAttribute('aria-expanded', open ? 'false' : 'true');
    });
    document.addEventListener('click', () => {
      sessionsMenu.hidden = true;
      sessionsBtn.setAttribute('aria-expanded', 'false');
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        sessionsMenu.hidden = true;
        sessionsBtn.setAttribute('aria-expanded', 'false');
      }
    });
  }

  const quitBtn = document.getElementById('quit-btn');
  const quitModal = document.getElementById('quit-modal');
  if (quitBtn && quitModal) {
    const quitCancel = document.getElementById('quit-cancel');
    const quitConfirm = document.getElementById('quit-confirm');
    quitBtn.addEventListener('click', () => {
      window.__openModal(quitModal, quitCancel);
    });
    if (quitCancel) quitCancel.addEventListener('click', () => window.__closeModal());
    if (quitConfirm) quitConfirm.addEventListener('click', () => {
      window.__closeModal();
      fetch('/api/quit', { method: 'POST' }).catch(() => {});
      window.close();
    });
  }

  // ── Session activity: working/unread dots and the "done working" toast ────

  const currentSessionId = (() => {
    const v = document.getElementById('chat-view');
    return v ? v.dataset.sessionId : null;
  })();

  function lastSeen() {
    try { return JSON.parse(localStorage.getItem('lastSeenMessages') || '{}'); } catch (e) { return {}; }
  }
  function saveLastSeen(map) {
    try { localStorage.setItem('lastSeenMessages', JSON.stringify(map)); } catch (e) {}
  }

  function showDoneToast(s) {
    const area = document.getElementById('toast-area');
    if (!area) return;
    const el = document.createElement('div');
    el.className = 'done-toast';
    const a = document.createElement('a');
    a.href = '/sessions/' + s.id;
    const strong = document.createElement('strong');
    strong.textContent = s.name;
    a.appendChild(strong);
    a.appendChild(document.createTextNode(' done working \u2192'));
    el.appendChild(a);
    area.appendChild(el);
    setTimeout(() => { if (el.parentNode) el.remove(); }, 7000);
  }

  function showToast(text) {
    const area = document.getElementById('toast-area');
    if (!area) return;
    const el = document.createElement('div');
    el.className = 'done-toast subtle';
    el.textContent = text;
    area.appendChild(el);
    setTimeout(() => { if (el.parentNode) el.remove(); }, 2000);
  }

  let prevRunning = null;
  async function refreshActivity() {
    let data;
    try { data = await fetch('/api/sessions/status').then((r) => r.json()); } catch (e) { return; }
    const seen = lastSeen();
    const byId = {};
    data.forEach((s) => { byId[s.id] = s; });

    document.querySelectorAll('#sessions-menu a[data-session-id]').forEach((a) => {
      const id = a.dataset.sessionId;
      const s = byId[id];
      const dot = a.querySelector('.session-dot');
      if (!dot) return;
      if (!s) { dot.hidden = true; return; }
      const isCurrent = id === currentSessionId;
      const status = a.querySelector('.session-status');
      if (s.running) {
        dot.hidden = false;
        dot.classList.add('working');
        dot.classList.remove('unread');
        if (status) status.textContent = ' (working)';
      } else {
        dot.classList.remove('working');
        const unread = !isCurrent && s.last_role === 'assistant' &&
          seen[id] !== undefined && seen[id] !== s.last_message_id;
        dot.hidden = !unread;
        dot.classList.toggle('unread', unread);
        if (status) status.textContent = unread ? ' (new reply)' : '';
      }
      if (isCurrent) seen[id] = s.last_message_id;
    });
    saveLastSeen(seen);

    if (prevRunning) {
      data.forEach((s) => {
        if (prevRunning[s.id] && !s.running && s.last_role === 'assistant' &&
            s.id !== currentSessionId) {
          showDoneToast(s);
        }
      });
    }
    prevRunning = {};
    data.forEach((s) => { prevRunning[s.id] = s.running; });
  }

  refreshActivity();
  setInterval(refreshActivity, 2500);

  // ── Scroll persistence (per page, including the chat input) ───────────────
  // Remember where you were scrolled on each page, so back/forward returns you
  // to exactly the same spot.

  const scrollPageKey = location.pathname + location.search;
  let scrollState = {};
  try { scrollState = JSON.parse(localStorage.getItem('scrollPositions') || '{}'); } catch (e) {}
  let scrollSaveTimer = null;

  function saveScrollState() {
    try { localStorage.setItem('scrollPositions', JSON.stringify(scrollState)); } catch (e) {}
  }
  function rememberScroll(slot, el) {
    if (!el) return;
    if (!scrollState[scrollPageKey]) scrollState[scrollPageKey] = {};
    scrollState[scrollPageKey][slot] = el.scrollTop;
    if (!scrollSaveTimer) {
      scrollSaveTimer = setTimeout(() => { scrollSaveTimer = null; saveScrollState(); }, 120);
    }
  }
  function savedScroll(slot) {
    const v = (scrollState[scrollPageKey] || {})[slot];
    return typeof v === 'number' ? v : null;
  }
  function watchScroll(el, slot) {
    if (!el) return;
    el.addEventListener('scroll', () => rememberScroll(slot, el), { passive: true });
  }

  // Flush the very latest position when the page is torn down.
  window.addEventListener('pagehide', saveScrollState);

  // The settings page scrolls the whole content area. This is kept per-run
  // (sessionStorage), not across runs: a fresh launch starts at the top, but
  // revisiting Settings during the same run returns you to where you were.
  (function () {
    const s = document.querySelector('.settings-scroll');
    if (!s) return;
    let saved = null;
    try { saved = JSON.parse(sessionStorage.getItem('settingsScroll') || 'null'); } catch (e) {}
    if (typeof saved === 'number') s.scrollTop = saved;
    let t = null;
    s.addEventListener('scroll', () => {
      if (t) return;
      t = setTimeout(() => {
        t = null;
        try { sessionStorage.setItem('settingsScroll', JSON.stringify(s.scrollTop)); } catch (e) {}
      }, 120);
    }, { passive: true });
  })();

  // ── Chat (chat pages only) ────────────────────────────────────────────────

  const view = document.getElementById('chat-view');
  if (!view) return;

  const sessionId = view.dataset.sessionId;
  const isHome = view.dataset.isHome === '1';
  const hasKey = view.dataset.hasKey === '1';
  const sttAvailable = view.dataset.sttAvailable === '1';
  const sttStreaming = view.dataset.sttStreaming === '1';
  const ttsAvailable = view.dataset.ttsAvailable === '1';

  const messages = document.getElementById('messages');
  const statusBar = document.getElementById('status-bar');
  const form = document.getElementById('chat-form');
  const textarea = document.getElementById('chat-textarea');
  const sendBtn = document.getElementById('send-btn');
  const stopBtn = document.getElementById('stop-btn');

  // Accessibility: the "Skip to Talk" link focuses the Talk button, past the
  // whole chat history, so tabbing back once reaches the latest message.
  const skipLink = document.getElementById('skip-link');
  if (skipLink) {
    skipLink.addEventListener('click', (e) => {
      const href = skipLink.getAttribute('href');
      if (href === '#chat-textarea') {
        e.preventDefault();
        textarea.focus();
        if (textarea.scrollIntoView) textarea.scrollIntoView({ block: 'center' });
      } else if (href === '#mic-btn') {
        e.preventDefault();
        const mic = document.getElementById('mic-btn');
        if (mic) {
          mic.focus();
          if (mic.scrollIntoView) mic.scrollIntoView({ block: 'center' });
        }
      }
    });
  }

  let running = false;
  let turnStartedAt = 0;

  function renderMarkdown(el) {
    const raw = el.textContent;
    if (raw.trim()) el.innerHTML = window.md.render(raw);
  }

  function escapeAttr(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // Turn a bare URL in a message into a small preview card (title + blurb).
  function addLinkPreviews(container) {
    if (!container) return;
    const links = Array.from(container.querySelectorAll('a[href^="http"]')).filter((a) => {
      const t = (a.textContent || '').trim();
      return t && (t === a.href || /^https?:\/\//.test(t) || a.href.includes(t));
    });
    if (!links.length) return;
    const a = links[0];
    fetch('/api/link_preview?url=' + encodeURIComponent(a.href))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok || !data.title) return;
        const card = document.createElement('a');
        card.className = 'link-preview';
        card.href = a.href;
        card.target = '_blank';
        card.rel = 'noopener noreferrer';
        card.innerHTML =
          (data.image
            ? '<div class="link-preview-img"><img src="' + escapeAttr(data.image) +
              '" alt="" loading="lazy" referrerpolicy="no-referrer"></div>'
            : '') +
          '<div class="link-preview-body">' +
          '<span class="link-preview-title">' + escapeAttr(data.title) + '</span>' +
          (data.description
            ? '<span class="link-preview-desc">' + escapeAttr(data.description) + '</span>'
            : '') +
          '<span class="link-preview-site">' + escapeAttr(data.site || '') + '</span>' +
          '</div>';
        const p = a.closest('p');
        if (p && p.parentNode) p.insertAdjacentElement('afterend', card);
        else container.appendChild(card);
      })
      .catch(() => {});
  }

  // ── Rendering ─────────────────────────────────────────────────────────────

  function addCopyButton(bubbleEl) {
    if (!bubbleEl || bubbleEl.querySelector('.copy-btn')) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'copy-btn';
    btn.title = 'Copy';
    btn.setAttribute('aria-label', 'Copy message');
    btn.innerHTML =
      '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true">' +
      '<rect x="9" y="9" width="11" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="2"/>' +
      '<path d="M5 15V5a2 2 0 0 1 2-2h10" fill="none" stroke="currentColor" stroke-width="2"/>' +
      '</svg>';
    bubbleEl.insertBefore(btn, bubbleEl.firstChild);
  }

  function addPlayButton(bubbleEl) {
    if (!ttsAvailable || !bubbleEl || bubbleEl.querySelector('.play-btn')) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'play-btn';
    btn.title = 'Read aloud';
    btn.setAttribute('aria-label', 'Read this message aloud');
    btn.innerHTML =
      '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true">' +
      '<path fill="currentColor" d="M8 5v14l11-7z"/></svg>';
    bubbleEl.insertBefore(btn, bubbleEl.firstChild);
  }

  function bubble(role) {
    const wrap = document.createElement('div');
    wrap.className = 'message ' + role;
    const inner = document.createElement('div');
    inner.className = 'bubble';
    // Copy is inserted last so it comes first in the DOM (and thus first in
    // tab order); the play button sits to its left visually.
    addPlayButton(inner);
    addCopyButton(inner);
    const content = document.createElement('div');
    content.className = 'content';
    inner.appendChild(content);
    wrap.appendChild(inner);
    messages.appendChild(wrap);
    return content;
  }

  function appendUser(text) {
    const content = bubble('user');
    content.textContent = text;
    renderMarkdown(content);
    scrollToBottom();
    return content.closest('.message');
  }

  function appendAction(sessionId) {
    const wrap = document.createElement('div');
    wrap.className = 'message action';
    const a = document.createElement('a');
    a.className = 'open-project-btn';
    a.href = '/sessions/' + sessionId;
    a.textContent = 'Open this project \u2192';
    wrap.appendChild(a);
    messages.appendChild(wrap);
    scrollToBottom();
  }

  function appendSummary(summaryText) {
    const wrap = document.createElement('div');
    wrap.className = 'message summary';
    const details = document.createElement('details');
    details.className = 'summary-note';
    const summary = document.createElement('summary');
    summary.textContent = 'Earlier conversation was summarized';
    const text = document.createElement('div');
    text.className = 'summary-text';
    text.textContent = summaryText;
    renderMarkdown(text);
    details.appendChild(summary);
    details.appendChild(text);
    wrap.appendChild(details);
    messages.appendChild(wrap);
  }

  const chatScroller = document.querySelector('.chat-scroll');
  function scrollToBottom() {
    if (chatScroller) chatScroller.scrollTop = chatScroller.scrollHeight;
  }

  // A small jump-to-bottom button appears once you scroll up far enough from
  // the newest message. It is removed from the tab order while hidden, and sits
  // just above the composer, just to the right of the chat column.
  const scrollBottomBtn = document.getElementById('scroll-bottom-btn');
  const composerWrap = document.querySelector('.composer-wrap');
  const chatScrollInner = document.querySelector('.chat-scroll-inner');
  function updateScrollBottomBtn() {
    if (!chatScroller || !scrollBottomBtn) return;
    const nearBottom =
      chatScroller.scrollHeight - chatScroller.scrollTop - chatScroller.clientHeight < 200;
    scrollBottomBtn.hidden = nearBottom;
  }
  function positionScrollBottomBtn() {
    if (!scrollBottomBtn || !composerWrap || !chatScrollInner) return;
    const gap = 16;
    const btnW = 56;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const contentRight = chatScrollInner.getBoundingClientRect().right;
    const composerTop = composerWrap.getBoundingClientRect().top;
    scrollBottomBtn.style.bottom = (vh - composerTop + gap) + 'px';
    scrollBottomBtn.style.right = Math.max(gap, vw - contentRight - gap - btnW) + 'px';
  }
  if (chatScroller && scrollBottomBtn) {
    chatScroller.addEventListener('scroll', updateScrollBottomBtn, { passive: true });
    scrollBottomBtn.addEventListener('click', () => {
      scrollToBottom();
      updateScrollBottomBtn();
    });
    updateScrollBottomBtn();
    positionScrollBottomBtn();
    window.addEventListener('resize', positionScrollBottomBtn);
    if (window.ResizeObserver && composerWrap) {
      new ResizeObserver(positionScrollBottomBtn).observe(composerWrap);
    }
  }

  function setStatus(text) {
    statusBar.textContent = text;
    statusBar.hidden = false;
  }

  function clearStatus() {
    statusBar.hidden = true;
    statusBar.textContent = '';
  }

  // ── Status wording ────────────────────────────────────────────────────────

  const TOOL_STATUS = {
    read: 'Reading',
    write: 'Writing',
    edit: 'Editing',
    bash: 'Running',
    grep: 'Searching',
    glob: 'Looking for',
    webfetch: 'Looking up',
    websearch: 'Searching the web',
    task: 'Researching',
    explore: 'Looking around',
    browser: 'Checking the website',
    capture: 'Looking at the screen',
    create_project: 'Creating project',
    open_project: 'Opening project',
    rename_project: 'Renaming project',
    delete_project: 'Removing project',
    list_projects: 'Listing projects',
  };

  function clip(s, n) {
    s = String(s || '');
    return s.length > n ? s.slice(0, n) + '\u2026' : s;
  }

  function toolTarget(name, args) {
    const a = args || {};
    if (name === 'read' || name === 'write' || name === 'edit') {
      const p = a.filePath || a.path || '';
      return p ? ' ' + p.split(/[\\/]/).pop() : ' a file';
    }
    if (name === 'bash') {
      const cmd = String(a.command || '').trim().split(/\s+/)[0];
      return cmd ? ' ' + cmd : ' a command';
    }
    if (name === 'grep' || name === 'glob') {
      return a.pattern ? ' for ' + clip(a.pattern, 28) : '';
    }
    if (name === 'webfetch') return ' a webpage';
    if (name === 'websearch') return a.query ? ' for ' + clip(a.query, 28) : '';
    if (name === 'task' || name === 'explore') {
      return a.description ? ' ' + clip(a.description, 28) : '';
    }
    if (name === 'create_project' || name === 'open_project' ||
        name === 'rename_project' || name === 'delete_project') {
      return a.name ? ' ' + clip(a.name, 28) : '';
    }
    return '';
  }

  function statusForTool(name, args) {
    return (TOOL_STATUS[name] || 'Working') + toolTarget(name, args) + '\u2026';
  }

  // ── Read-aloud ────────────────────────────────────────────────────────────

  let ttsAutoEnabled = view.dataset.ttsAuto === '1';
  const ttsVoice = view.dataset.ttsVoice;
  const ttsSpeed = parseFloat(view.dataset.ttsSpeed || '1.25');
  let ttsVolume = parseFloat(view.dataset.ttsVolume || '0.75');
  let ttsChain = Promise.resolve();
  let currentAudio = null;
  // Every live audio element, so stopping can silence them all at once — never
  // leave two voices playing because one slipped past the current-audio pointer.
  const activeAudios = new Set();
  let stopToken = 0;
  let activePlayBtn = null;
  // What is playing right now (for resuming after dictation interrupts it).
  let nowSpeaking = null;
  let pendingResume = null;

  const TTS_PLAY_SVG =
    '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>';
  const TTS_PAUSE_SVG =
    '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><path fill="currentColor" d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>';

  function setPlayBtnState(btn, state) {
    if (!btn) return;
    btn.classList.toggle('playing', state === 'pause');
    btn.classList.toggle('loading', state === 'loading');
    btn.innerHTML = state === 'pause' ? TTS_PAUSE_SVG : TTS_PLAY_SVG;
    btn.setAttribute('aria-label',
      state === 'pause' ? 'Pause reading' :
      state === 'loading' ? 'Preparing audio' :
      'Read this message aloud');
  }

  // Stop any speech (auto or manual) without touching the auto-read toggle.
  function stopSpeech() {
    stopToken += 1;
    activeAudios.forEach((a) => { try { a.pause(); } catch (e) {} });
    activeAudios.clear();
    currentAudio = null;
    if (activePlayBtn) { setPlayBtnState(activePlayBtn, 'play'); activePlayBtn = null; }
    nowSpeaking = null;
    clearHighlight();
  }

  // Queue a read-aloud run. `force` means "read this once even if auto-read is
  // off" (manual play / resume); `btn` is the play button when there is one.
  // `sentences`/`cache` let a resume skip the plan step and reuse audio that
  // was already synthesised while the user was dictating. `bubble` is the
  // message element being read, so a subtle underline can follow the sentence.
  function queueSpeak(text, force, btn, startIndex, sentences, cache, bubble) {
    text = (text || '').trim();
    if (!text) return;
    if (force) {
      activePlayBtn = btn;
      setPlayBtnState(btn, 'loading');
    }
    const token = stopToken;
    ttsChain = ttsChain.then(() => speakOne(text, force, token, startIndex, sentences, cache, bubble)).then(() => {
      if (force && stopToken === token && activePlayBtn === btn) {
        setPlayBtnState(btn, 'play');
        activePlayBtn = null;
      }
    });
  }

  async function speak(text, bubble) {
    if (!ttsAvailable || !ttsAutoEnabled) return;
    if (listening) return;  // don't auto-read over the user's dictation
    queueSpeak(text, false, null, 0, null, null, bubble);
  }

  // Read one bubble aloud on demand, once, regardless of the auto-read toggle.
  function playBubble(text, btn) {
    if (!ttsAvailable) return;
    text = (text || '').trim();
    if (!text) return;
    if (activePlayBtn === btn) { stopSpeech(); return; }
    stopSpeech();
    queueSpeak(text, true, btn, 0, null, null, btn.closest('.bubble'));
  }

  // Continue reading a message that dictation interrupted, from the sentence
  // that was playing, so the user can make a note and then keep listening.
  function resumePending() {
    if (!pendingResume || !ttsAvailable) return;
    const r = pendingResume;
    pendingResume = null;
    queueSpeak(r.text, true, r.btn, r.index, r.sentences, r.cache, r.bubble);
  }

  // ── Underline the sentence being read ─────────────────────────────────────
  let highlightBubble = null;

  function clearHighlight() {
    if (highlightBubble) {
      highlightBubble.querySelectorAll('.tts-cursor').forEach((el) => {
        const parent = el.parentNode;
        el.replaceWith(...el.childNodes);
      });
      highlightBubble = null;
    }
  }

  function setHighlight(bubble, sentences, index) {
    if (!bubble || !Array.isArray(sentences) || !sentences.length) return;
    if (bubble !== highlightBubble) clearHighlight();
    highlightBubble = bubble;
    bubble.querySelectorAll('.tts-cursor').forEach((el) => {
      const parent = el.parentNode;
      el.replaceWith(...el.childNodes);
    });
    const total = sentences.reduce((s, t) => s + (t || '').length, 0);
    if (!total) return;
    let before = 0;
    for (let i = 0; i < index; i++) before += (sentences[i] || '').length;
    const content = bubble.querySelector('.content') || bubble;
    const len = (content.textContent || '').length;
    if (!len) return;
    const start = Math.max(0, Math.round(len * (before / total)));
    const end = Math.min(len, Math.round(len * ((before + (sentences[index] || '').length) / total)));
    if (end <= start) return;
    const walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT);
    let offset = 0;
    const parts = [];
    while (walker.nextNode()) {
      const n = walker.currentNode;
      const nLen = n.nodeValue.length;
      const nStart = offset;
      const nEnd = offset + nLen;
      offset = nEnd;
      if (nEnd <= start || nStart >= end) continue;
      parts.push({ node: n, s: Math.max(0, start - nStart), e: Math.min(nLen, end - nStart) });
    }
    for (const p of parts) {
      const span = document.createElement('span');
      span.className = 'tts-cursor';
      const range = document.createRange();
      range.setStart(p.node, p.s);
      range.setEnd(p.node, p.e);
      try { range.surroundContents(span); } catch (err) { /* ignore */ }
    }
  }

  // Resolve only when the audio has actually finished (or been paused).
  function playToEnd(audio) {
    return new Promise((resolve) => {
      const done = () => resolve();
      audio.addEventListener('ended', done, { once: true });
      audio.addEventListener('error', done, { once: true });
      audio.addEventListener('pause', done, { once: true });
      audio.loop = false;
      audio.play().catch(done);
    });
  }

  function synthChunk(sentence) {
    return fetch('/api/tts/speak', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: sentence, voice: ttsVoice, speed: ttsSpeed }),
    }).then((r) => {
      if (!r.ok) throw new Error('tts speak failed');
      return r.blob();
    });
  }

  // Synthesise a sentence once, remembering the promise so a resume can reuse
  // audio that was prepared in advance (while the user was dictating). A failed
  // synthesis drops out of the cache so the next attempt re-tries fresh.
  function synthCached(sentences, index, cache) {
    if (cache && cache[index]) return cache[index];
    const p = synthChunk(sentences[index]);
    if (cache) {
      cache[index] = p;
      p.catch(() => { if (cache[index] === p) delete cache[index]; });
    }
    return p;
  }

  async function speakOne(text, force, token, startIndex = 0, sentences = null, cache = null, bubble = null) {
    try {
      if (!sentences) {
        sentences = [text];
        try {
          const plan = await fetch('/api/tts/plan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
          }).then((r) => r.json());
          if (Array.isArray(plan.sentences) && plan.sentences.length) sentences = plan.sentences;
        } catch (e) {}
      }

      if (startIndex >= sentences.length) startIndex = 0;

      // Prefetch the first chunk, then fetch the next while the current one is
      // playing, so there is no synthesis round-trip pause between sentences.
      let pending = synthCached(sentences, startIndex, cache);
      for (let i = startIndex; i < sentences.length; i++) {
        if (stopToken !== token) return;
        if (!force && !ttsAutoEnabled) return;

        let blob = null;
        try { blob = await pending; } catch (e) {}
        if (stopToken !== token) return;  // stopped while the chunk was fetching

        if (i + 1 < sentences.length) pending = synthCached(sentences, i + 1, cache);
        else pending = null;

        if (!blob) continue;
        const audio = new Audio(URL.createObjectURL(blob));
        audio.volume = ttsVolume;
        audio.loop = false;
        activeAudios.add(audio);
        currentAudio = audio;
        if (force && activePlayBtn) setPlayBtnState(activePlayBtn, 'pause');
        nowSpeaking = { text, sentences, index: i, force, btn: activePlayBtn, bubble };
        setHighlight(bubble, sentences, i);
        await playToEnd(audio);
        activeAudios.delete(audio);
        if (currentAudio === audio) currentAudio = null;
      }
    } catch (e) { /* read-aloud is best-effort */ }
    nowSpeaking = null;
    clearHighlight();
  }

  // ── SSE ───────────────────────────────────────────────────────────────────

  async function readSSE(resp, onEvent) {
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        for (const line of chunk.split('\n')) {
          if (line.startsWith('data: ')) {
            try { onEvent(JSON.parse(line.slice(6))); } catch (e) {}
          }
        }
      }
    }
  }

  let pendingOpen = null;

  function handleEvent(ev) {
    switch (ev.type) {
      case 'reasoning':
        setStatus('Thinking\u2026');
        break;
      case 'content':
        if (needsBreak) { assistantBuffer += '\n\n'; needsBreak = false; }
        assistantBuffer += ev.text;
        assistantEl.textContent = assistantBuffer;
        renderMarkdown(assistantEl);
        scrollToBottom();
        setStatus('Writing a reply\u2026');
        break;
      case 'tool_start':
        needsBreak = true;
        setStatus(statusForTool(ev.name, ev.args));
        break;
      case 'tool_end':
        if (ev.open_session) {
          pendingOpen = ev.open_session;
          appendAction(ev.open_session);
        }
        setStatus('Thinking\u2026');
        break;
      case 'compacting':
        needsBreak = true;
        setStatus('Summarising our conversation\u2026');
        break;
      case 'done':
        finishAssistant(ev);
        break;
      case 'error':
        if (assistantBuffer) finishAssistant(ev);
        else {
          showError(ev.message);
          revertFailedTurn();
        }
        endTurn();
        break;
      case 'aborted':
        endTurn();
        break;
      case 'stream_end':
        endTurn();
        break;
      case 'attached':
        break;
    }
  }

  let assistantEl = null;
  let assistantBuffer = '';
  let needsBreak = false;
  let pendingUserMsg = null;

  // Take back the user's message when a send failed, so the conversation reads
  // as if it was never sent. Called together with removeEmptyAssistant().
  function revertFailedTurn() {
    if (pendingUserMsg && pendingUserMsg.parentNode) pendingUserMsg.remove();
    pendingUserMsg = null;
  }

  // If a turn produced no reply at all (an error before any words arrived),
  // take the empty bubble back out so the log isn't left with a blank message.
  function removeEmptyAssistant() {
    if (!assistantEl || assistantBuffer.trim()) return;
    const msg = assistantEl.closest('.message');
    if (msg && msg.parentNode) msg.remove();
    assistantEl = null;
    assistantBuffer = '';
  }

  function finishAssistant(ev) {
    pendingUserMsg = null;
    if (assistantEl && assistantBuffer.trim()) {
      // Render from the raw buffer, not from the element: by the time the turn
      // ends the element holds already-rendered HTML, and re-rendering its
      // textContent would strip every markdown construct.
      assistantEl.innerHTML = window.md.render(assistantBuffer);
      speak(assistantBuffer, assistantEl.closest('.bubble'));
      addLinkPreviews(assistantEl);
    } else {
      removeEmptyAssistant();
    }
    if (ev.type === 'done' && turnStartedAt) {
      const secs = Math.max(1, Math.round((Date.now() - turnStartedAt) / 1000));
      const note = document.createElement('span');
      note.className = 'worked-note';
      note.textContent = secs === 1 ? 'Worked for 1 second' : 'Worked for ' + secs + ' seconds';
      const bubble = assistantEl ? assistantEl.closest('.bubble') : null;
      if (bubble) bubble.appendChild(note);
    }
    turnStartedAt = 0;
    assistantEl = null;
    assistantBuffer = '';
    clearStatus();
    maybeAutoOpen();
    scrollToBottom();
  }

  function maybeAutoOpen() {
    if (!pendingOpen || !isHome) return;
    const target = pendingOpen;
    pendingOpen = null;
    setStatus('Opening your project\u2026');
    setTimeout(() => { window.location.href = '/sessions/' + target; }, 1200);
  }

  function showError(text) {
    const wrap = document.createElement('div');
    wrap.className = 'message error';
    wrap.textContent = text || 'Something went wrong. Please try again.';
    messages.appendChild(wrap);
    scrollToBottom();
  }

  function endTurn() {
    removeEmptyAssistant();
    running = false;
    sendBtn.hidden = false;
    stopBtn.hidden = true;
    clearStatus();
    refreshTheme();
  }

  function beginTurn() {
    running = true;
    sendBtn.hidden = true;
    stopBtn.hidden = false;
    turnStartedAt = Date.now();
    setStatus('Working\u2026');
  }

  async function sendMessage(text) {
    text = (text || '').trim();
    if (!text || running) return;
    if (!hasKey) {
      window.location.href = '/settings';
      return;
    }
    pendingUserMsg = appendUser(text);
    assistantEl = bubble('assistant');
    assistantBuffer = '';
    needsBreak = false;
    beginTurn();
    try {
      const resp = await fetch('/api/sessions/' + sessionId + '/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });
      if (!resp.ok) {
        showError('I could not send that message. Please try again.');
        revertFailedTurn();
        endTurn();
        return;
      }
      await readSSE(resp, handleEvent);
    } catch (e) {
      showError('The connection was lost. Please try again.');
      revertFailedTurn();
      endTurn();
    }
  }

  // ── Composer ──────────────────────────────────────────────────────────────

  const recentProjects = document.getElementById('recent-projects');
  function hideRecentProjects() {
    if (recentProjects) recentProjects.hidden = true;
  }

  // Each session remembers its own unsent draft, so switching away and back
  // never loses what you were typing.
  const composerKey = 'composer:' + sessionId;
  function saveComposer() {
    try { localStorage.setItem(composerKey, textarea.value); } catch (e) {}
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    hideRecentProjects();
    cancelDictation();
    sendMessage(messageWithAttachments(textarea.value));
    textarea.value = '';
    textarea.style.height = 'auto';
    clearAttachments();
    try { localStorage.removeItem(composerKey); } catch (err) {}
  });

  textarea.addEventListener('focus', hideRecentProjects);

  textarea.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  textarea.addEventListener('input', () => {
    // If the user starts typing while dictation is active, stop dictation so it
    // can't overwrite their manual edits a moment later.
    if (listening && !writingFromDictation) {
      cancelDictation();
    }
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 240) + 'px';
    textarea.scrollTop = textarea.scrollHeight;
    saveComposer();
  });

  try {
    const draft = localStorage.getItem(composerKey);
    if (draft) {
      textarea.value = draft;
      // Wait for layout so scrollHeight is real, then size the box to the text
      // and put the textarea back where it was scrolled (or at the end).
      requestAnimationFrame(() => {
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 240) + 'px';
        const saved = savedScroll('textarea');
        textarea.scrollTop = saved !== null ? saved : textarea.scrollHeight;
      });
    }
  } catch (e) {}

  stopBtn.addEventListener('click', async () => {
    await fetch('/api/sessions/' + sessionId + '/cancel', { method: 'POST' });
  });

  // ── Drag-and-drop attachments ─────────────────────────────────────────────
  // Every dropped file is saved by the server and shown as a chip; when the
  // message is sent, its path is handed to the AI so it can read it with its
  // own tools.

  let attachments = [];
  const attachmentsBox = document.getElementById('attachments');

  const FILE_ICON_SVG =
    '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">' +
    '<path fill="none" stroke="currentColor" stroke-width="2" d="M6 2h8l4 4v16H6z"/>' +
    '<path fill="none" stroke="currentColor" stroke-width="2" d="M14 2v4h4"/></svg>';
  const FOLDER_ICON_SVG =
    '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">' +
    '<path fill="none" stroke="currentColor" stroke-width="2" d="M3 6a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';

  function renderAttachments() {
    if (!attachmentsBox) return;
    attachmentsBox.hidden = attachments.length === 0;
    attachmentsBox.innerHTML = '';
    attachments.forEach((a, i) => {
      const chip = document.createElement('span');
      chip.className = 'attachment-chip';

      const thumb = document.createElement('span');
      thumb.className = 'attachment-thumb';
      if (a.thumb) {
        const img = document.createElement('img');
        img.src = a.thumb;
        img.alt = '';
        thumb.appendChild(img);
      } else {
        thumb.innerHTML = a.isDir ? FOLDER_ICON_SVG : FILE_ICON_SVG;
      }
      chip.appendChild(thumb);

      const label = document.createElement('span');
      label.className = 'attachment-name';
      label.textContent = a.name;
      chip.appendChild(label);

      const rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'rm';
      rm.setAttribute('aria-label', 'Remove ' + a.name);
      rm.textContent = '\u00d7';
      rm.addEventListener('click', () => {
        if (a.thumb) { try { URL.revokeObjectURL(a.thumb); } catch (e) {} }
        attachments.splice(i, 1);
        renderAttachments();
      });
      chip.appendChild(rm);

      attachmentsBox.appendChild(chip);
    });
  }

  function clearAttachments() {
    attachments.forEach((a) => { if (a.thumb) { try { URL.revokeObjectURL(a.thumb); } catch (e) {} } });
    attachments = [];
    renderAttachments();
  }

  function messageWithAttachments(base) {
    if (!attachments.length) return base;
    const lines = attachments.map((a) => '- ' + a.path).join('\n');
    const header = 'Attached:\n' + lines;
    const text = (base || '').trim();
    return text ? header + '\n\n---\n\n' + text : header;
  }

  // A dropped item is just a path for the AI to read — the file (or folder)
  // itself stays where it is on the user's computer.
  function fileUriToPath(uri) {
    const u = (uri || '').trim();
    if (!u.startsWith('file://')) return null;
    try {
      return decodeURIComponent(new URL(u).pathname);
    } catch (e) {
      return decodeURIComponent(u.slice('file://'.length));
    }
  }

  function droppedPaths(e) {
    try {
      const uris = (e.dataTransfer.getData('text/uri-list') || '')
        .split(/\r?\n/).map((s) => s.trim()).filter((s) => s && !s.startsWith('#'));
      return uris.map(fileUriToPath).filter(Boolean);
    } catch (err) {
      return [];  // getData can throw for some drags
    }
  }

  function handleDroppedPaths(paths) {
    for (const p of paths) {
      const clean = p.replace(/\/+$/, '');
      const parts = clean.split('/').filter(Boolean);
      const name = parts[parts.length - 1] || clean;
      const isDir = p !== clean;
      attachments.push({ path: clean, name, isDir, thumb: null });
    }
    renderAttachments();
  }

  async function handleDroppedFiles(files) {
    for (const file of files) {
      setStatus('Attaching ' + file.name + '\u2026');
      const isImage = (file.type || '').startsWith('image/');
      const thumb = isImage ? URL.createObjectURL(file) : null;
      try {
        const fd = new FormData();
        fd.append('file', file, file.name);
        const data = await fetch('/api/sessions/' + sessionId + '/upload', {
          method: 'POST',
          body: fd,
        }).then((r) => r.json());
        if (!data || !data.path) throw new Error('no path');
        attachments.push({ path: data.path, name: data.name || file.name, thumb });
        renderAttachments();
        clearStatus();
      } catch (e) {
        if (thumb) { try { URL.revokeObjectURL(thumb); } catch (err) {} }
        setStatus('Could not attach ' + file.name);
      }
    }
  }

  const attachBtn = document.getElementById('attach-btn');
  const fileInput = document.getElementById('file-input');
  if (attachBtn && fileInput) {
    attachBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
      const files = Array.from(fileInput.files || []);
      fileInput.value = '';
      if (files.length) handleDroppedFiles(files);
    });
  }

  // Dropping a file or folder anywhere on the chat window attaches it — not
  // just onto the text box. While a file is being dragged over the window, a
  // full-screen dashed overlay appears so it is obvious where it will go.
  const dropOverlay = document.getElementById('drop-overlay');
  let dragDepth = 0;
  const hasFiles = (e) => {
    if (!e.dataTransfer) return false;
    const types = Array.from(e.dataTransfer.types || []);
    // Files come through as `Files`; folders come through as `text/uri-list`.
    return types.includes('Files') || types.includes('text/uri-list');
  };

  window.addEventListener('dragenter', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth += 1;
    if (dropOverlay) dropOverlay.hidden = false;
  });
  window.addEventListener('dragover', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
  });
  window.addEventListener('dragleave', (e) => {
    if (!hasFiles(e)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0 && dropOverlay) dropOverlay.hidden = true;
  });
  window.addEventListener('drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0;
    if (dropOverlay) dropOverlay.hidden = true;
    const paths = droppedPaths(e);
    if (paths.length) {
      handleDroppedPaths(paths);
      return;
    }
    // No file:// URIs (e.g. an item dragged from inside another window) — fall
    // back to saving the file's contents so the AI still has a path to read.
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length) handleDroppedFiles(files);
  });

  // ── Dictation ─────────────────────────────────────────────────────────────

  const micBtn = document.getElementById('mic-btn');
  const micLabel = micBtn ? micBtn.querySelector('.mic-label') : null;
  let micOn = view.dataset.sttEnabled === '1';
  let listening = false;
  let ws = null;
  let stream = null;
  let streamCtx = null;
  let workletLoaded = false;
  let workletNode = null;
  let srcNode = null;
  let anchor = '';
  let writingFromDictation = false;

  function setMicUI() {
    if (!micBtn) return;
    micBtn.setAttribute('aria-pressed', micOn ? 'true' : 'false');
    micBtn.classList.toggle('mic-off', !micOn);
  }

  function setComposerText(full) {
    if (!listening) return;
    writingFromDictation = true;
    textarea.value = (anchor ? anchor + ' ' : '') + full;
    textarea.dispatchEvent(new Event('input'));
    writingFromDictation = false;
    textarea.scrollTop = textarea.scrollHeight;
  }

  function ensureStreamCtx() {
    if (!streamCtx || streamCtx.state === 'closed') {
      streamCtx = new AudioContext({ sampleRate: 16000 });
    }
    if (streamCtx.state === 'suspended') streamCtx.resume();
  }

  function toggleDictation() {
    if (listening) { stopDictation(); return; }
    if (!micOn) {
      micOn = true;
      setMicUI();
      const fd = new FormData();
      fd.append('stt_enabled', 'on');
      fetch('/_settings/prefs', { method: 'POST', body: fd });
    }
    startDictation();
  }

  if (micBtn && sttAvailable) {
    setMicUI();
    micBtn.addEventListener('click', toggleDictation);
  }

  // Space toggles dictation when not typing in the box; Escape stops both
  // dictation and read-aloud.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      pendingResume = null;  // Escape means "stop everything", not "resume later"
      if (listening) { e.preventDefault(); stopDictation(); }
      stopSpeech();
      return;
    }
    if (e.code === 'Space' && document.activeElement !== textarea && micBtn && sttAvailable) {
      e.preventDefault();
      toggleDictation();
    }
  });

  function teardownAudio() {
    if (srcNode) { try { srcNode.disconnect(); } catch (e) {} srcNode = null; }
    if (workletNode) { try { workletNode.disconnect(); } catch (e) {} workletNode = null; }
    if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
    // streamCtx is kept open so the worklet module stays registered.
  }

  async function startDictation() {
    // If read-aloud is playing, remember where it was so we can resume after —
    // and start re-synthesising that sentence now, so the moment dictation
    // stops the audio can start again immediately instead of after a wait.
    let bookmark = null;
    if (nowSpeaking) {
      const cache = {};
      const sentences = nowSpeaking.sentences;
      const index = nowSpeaking.index;
      // Pre-synthesise just the sentence we'll resume with. One sentence, not
      // more: dictation already competes with whisper for CPU, and piling on
      // extra synthesis can leave a chunk half-finished (or failed) right when
      // playback starts, which is what caused the brief garble on resume.
      if (sentences && index < sentences.length) {
        synthCached(sentences, index, cache);
      }
      pendingResume = { text: nowSpeaking.text, sentences, index, btn: nowSpeaking.btn, cache, bubble: nowSpeaking.bubble };
      bookmark = { bubble: nowSpeaking.bubble, sentences, index };
    } else {
      pendingResume = null;
    }
    stopSpeech();  // dictation takes over: stop any read-aloud
    if (bookmark) setHighlight(bookmark.bubble, bookmark.sentences, bookmark.index);
    try {
      ensureStreamCtx();  // created/resumed within the user gesture, before awaits
      const audio = { echoCancellation: true, noiseSuppression: true, autoGainControl: false, channelCount: 1 };
      const mid = micDeviceId();
      if (mid) audio.deviceId = { exact: mid };
      stream = await navigator.mediaDevices.getUserMedia({ audio });
      anchor = textarea.value.trim();
      listening = true;
      textarea.placeholder = 'Listening\u2026';
      micBtn.classList.add('recording');
      if (micLabel) micLabel.textContent = 'Stop';

      if (sttStreaming) {
        if (streamCtx.state === 'suspended') await streamCtx.resume();
        if (!workletLoaded) {
          await streamCtx.audioWorklet.addModule('/static/js/stt-worklet.js');
          workletLoaded = true;
        }
        srcNode = streamCtx.createMediaStreamSource(stream);
        workletNode = new AudioWorkletNode(streamCtx, 'stt-capture');
        workletNode.port.onmessage = (e) => {
          if (ws && ws.readyState === 1) ws.send(e.data);
        };
        srcNode.connect(workletNode);

        ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/api/stt/stream');
        ws.binaryType = 'arraybuffer';
        ws.onmessage = (e) => {
          let d; try { d = JSON.parse(e.data); } catch (_) { return; }
          if (d.text) setComposerText(d.text);
        };
        ws.onclose = () => { if (listening) finishDictation(); };
      } else {
        const recorder = new MediaRecorder(stream);
        const chunks = [];
        recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
        recorder.onstop = async () => {
          const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
          if (!blob.size) { finishDictation(); return; }
          const fd = new FormData();
          fd.append('audio', blob, 'speech.webm');
          try {
            const data = await fetch('/api/stt', { method: 'POST', body: fd }).then((r) => r.json());
            if (data.text) setComposerText(data.text);
          } catch (e) {}
          finishDictation();
        };
        recorder.start();
        window.__sttRecorder = recorder;
      }
    } catch (e) {
      cancelDictation();
    }
  }

  async function stopDictation() {
    if (sttStreaming && ws && ws.readyState === 1) {
      const final = await new Promise((resolve) => {
        const timeout = setTimeout(() => resolve(null), 8000);
        ws.onmessage = (e) => {
          let d; try { d = JSON.parse(e.data); } catch (_) { return; }
          if (d.partial === false) {
            clearTimeout(timeout);
            resolve((d.text || '').trim());
          }
        };
        try { ws.send('end'); } catch (_) { resolve(null); }
      });
      if (final) setComposerText(final);
      finishDictation();
      return;
    }
    const rec = window.__sttRecorder;
    if (rec && rec.state !== 'inactive') { rec.stop(); return; }
    finishDictation();
  }

  function cancelDictation() {
    pendingResume = null;  // a send or manual edit ends the note-taking, not resume
    if (!listening && !ws && !window.__sttRecorder) return;
    listening = false;
    if (ws) { ws.onmessage = null; try { ws.close(); } catch (e) {} ws = null; }
    teardownAudio();
    window.__sttRecorder = null;
    if (micBtn) micBtn.classList.remove('recording');
    if (micLabel) micLabel.textContent = 'Talk';
    textarea.placeholder = 'Type or speak your message';
  }

  function finishDictation() {
    listening = false;
    if (ws) { try { ws.close(); } catch (e) {} ws = null; }
    teardownAudio();
    window.__sttRecorder = null;
    if (micBtn) micBtn.classList.remove('recording');
    if (micLabel) micLabel.textContent = 'Talk';
    textarea.placeholder = 'Type or speak your message';
    if (!running) clearStatus();  // keep the AI's "Working…" status if a turn is live
    resumePending();
    // Deliberately do NOT focus the textarea here: if the user toggled dictation
    // with Space (while not typing), stealing focus back would make the next
    // Space type a literal space instead of toggling again.
    textarea.scrollTop = textarea.scrollHeight;
  }

  // ── TTS toggle ────────────────────────────────────────────────────────────

  const ttsBtn = document.getElementById('tts-btn');
  if (ttsBtn && ttsAvailable) {
    ttsBtn.setAttribute('aria-pressed', ttsAutoEnabled ? 'true' : 'false');
    ttsBtn.classList.toggle('tts-off', !ttsAutoEnabled);
    ttsBtn.addEventListener('click', async () => {
      ttsAutoEnabled = !ttsAutoEnabled;
      ttsBtn.classList.toggle('tts-off', !ttsAutoEnabled);
      ttsBtn.setAttribute('aria-pressed', ttsAutoEnabled ? 'true' : 'false');
      if (!ttsAutoEnabled) {
        stopSpeech();
        if (ttsVolumeMenu) ttsVolumeMenu.hidden = true;  // hide the slider with the button
      }
      const fd = new FormData();
      fd.append('tts_auto', ttsAutoEnabled ? 'on' : 'off');
      await fetch('/_settings/prefs', { method: 'POST', body: fd });
      if (ttsAutoEnabled) {
        // Turning it on starts by reading the last reply, then keeps going.
        const bubbles = document.querySelectorAll('.message.assistant .content');
        if (bubbles.length) speak((bubbles[bubbles.length - 1].textContent || '').trim(), bubbles[bubbles.length - 1].closest('.bubble'));
      }
    });
  }

  // The read-aloud button raises a small vertical volume slider on hover or on
  // keyboard focus, so it works for mouse and keyboard users alike. A mouse
  // click also focuses the button, so the slider auto-hides a moment later
  // unless the pointer is on it — only keyboard (Tab) focus keeps it open.
  const ttsWrap = document.querySelector('.tts-wrap');
  const ttsVolumeMenu = document.getElementById('tts-volume-menu');
  const ttsVolumeInput = document.getElementById('tts-volume');
  if (ttsWrap && ttsVolumeMenu) {
    let hovered = false;
    let focused = false;
    let hideTimer = null;
    const sync = () => { ttsVolumeMenu.hidden = !ttsAutoEnabled || !(hovered || focused); };
    ttsWrap.addEventListener('mouseenter', () => { hovered = true; clearTimeout(hideTimer); sync(); });
    ttsWrap.addEventListener('mouseleave', () => { hovered = false; sync(); });
    ttsWrap.addEventListener('focusin', () => {
      focused = true;
      clearTimeout(hideTimer);
      hideTimer = setTimeout(() => {
        const ae = document.activeElement;
        const keyboardFocus = ae && ae.matches && ae.matches(':focus-visible');
        if (focused && !hovered && !keyboardFocus) { focused = false; sync(); }
      }, 1000);
      sync();
    });
    ttsWrap.addEventListener('focusout', () => { focused = false; clearTimeout(hideTimer); sync(); });
  }
  if (ttsVolumeInput) {
    ttsVolumeInput.value = String(ttsVolume);
    const applyVolume = () => {
      ttsVolume = parseFloat(ttsVolumeInput.value);
      if (!Number.isFinite(ttsVolume)) ttsVolume = 0.75;
      const fd = new FormData();
      fd.append('tts_volume', String(ttsVolume));
      fetch('/_settings/prefs', { method: 'POST', body: fd }).catch(() => {});
    };
    ttsVolumeInput.addEventListener('input', applyVolume);
    // Arrow keys adjust the volume when the slider has focus.
    ttsVolumeInput.addEventListener('keydown', (e) => {
      let v = parseFloat(ttsVolumeInput.value);
      if (e.key === 'ArrowUp' || e.key === 'ArrowRight') v = Math.min(1, v + 0.05);
      else if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') v = Math.max(0, v - 0.05);
      else return;
      e.preventDefault();
      ttsVolumeInput.value = String(v);
      applyVolume();
    });
  }

  // Read the unsent draft aloud, so a voice-first user can hear what they have
  // typed or dictated before sending it.
  const readDraftBtn = document.getElementById('read-draft-btn');
  if (readDraftBtn && ttsAvailable) {
    readDraftBtn.addEventListener('click', () => {
      const text = (textarea.value || '').trim();
      if (!text) return;
      if (activePlayBtn === readDraftBtn) { stopSpeech(); return; }
      stopSpeech();
      queueSpeak(text, true, readDraftBtn, 0);
    });
  }

  // ── Re-attach to a run that is already going ──────────────────────────────

  async function reattach() {
    try {
      const state = await fetch('/api/sessions/' + sessionId + '/state').then((r) => r.json());
      if (!state.running) return;
      beginTurn();
      setStatus('Working\u2026');
      const resp = await fetch('/api/sessions/' + sessionId + '/attach');
      await readSSE(resp, (ev) => {
        if (ev.type === 'tool_start') setStatus(statusForTool(ev.name, ev.args));
        else if (ev.type === 'content') setStatus('Writing a reply\u2026');
        else if (ev.type === 'reasoning') setStatus('Thinking\u2026');
        else if (ev.type === 'compacting') setStatus('Summarising\u2026');
        else if (ev.type === 'stream_end') reloadMessages();
      });
    } catch (e) {}
  }

  async function reloadMessages() {
    try {
      const rows = await fetch('/api/sessions/' + sessionId + '/messages').then((r) => r.json());
      messages.innerHTML = '';
      for (const m of rows) {
        if (m.kind === 'summary') {
          appendSummary(m.summary_text || '');
        } else if (m.role === 'tool') {
          if (m.open_session) appendAction(m.open_session);
        } else if (m.role === 'user') {
          appendUser(m.content || '');
        } else if (m.role === 'assistant' && (m.content || '').trim()) {
          const content = bubble('assistant');
          content.textContent = m.content;
          renderMarkdown(content);
          addLinkPreviews(content);
        }
      }
    } catch (e) {}
    endTurn();
    scrollToBottom();
  }

  document.querySelectorAll('.content[data-markdown], .summary-text[data-markdown]').forEach((el) => {
    renderMarkdown(el);
    addLinkPreviews(el);
  });

  // A copy button and a read-aloud button in the corner of every bubble.
  // Copy is inserted last so it comes first in the DOM (and thus first in tab
  // order); the play button sits to its left visually.
  document.querySelectorAll('.message .bubble').forEach((el) => {
    addPlayButton(el);
    addCopyButton(el);
  });
  document.addEventListener('click', (e) => {
    const playBtn = e.target.closest('.play-btn');
    if (playBtn) {
      const bubbleEl = playBtn.closest('.bubble');
      const content = bubbleEl ? bubbleEl.querySelector('.content') : null;
      const text = content ? (content.textContent || '').trim() : '';
      if (text) {
        if (listening) cancelDictation();
        playBubble(text, playBtn);
      }
      return;
    }
    const btn = e.target.closest('.copy-btn');
    if (!btn) return;
    const bubbleEl = btn.closest('.bubble');
    const content = bubbleEl ? bubbleEl.querySelector('.content') : null;
    const text = content ? (content.textContent || '').trim() : '';
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
      btn.classList.add('copied');
      showToast('Copied');
      setTimeout(() => btn.classList.remove('copied'), 1200);
    }).catch(() => {});
  });

  // ── Model switcher (composer drop-up) ────────────────────────────────────

  const modelBtn = document.getElementById('model-btn');
  const modelMenu = document.getElementById('model-menu');
  const modelWarning = document.getElementById('model-warning');
  let modelData = null;
  let pendingModel = null;
  const childMode = view.dataset.childMode === '1';
  let parentPassword = null;

  // In child mode, model switching needs the parent password first.
  function promptParentPassword(onSuccess) {
    const modal = document.getElementById('chat-password-modal');
    const input = document.getElementById('chat-password-input');
    const err = document.getElementById('chat-password-error');
    const confirmBtn = document.getElementById('chat-password-confirm');
    const cancelBtn = document.getElementById('chat-password-cancel');
    if (!modal) { onSuccess(null); return; }
    input.value = '';
    err.hidden = true;
    window.__openModal(modal, input);
    const cleanup = () => window.__closeModal();
    cancelBtn.onclick = cleanup;
    confirmBtn.onclick = async () => {
      const password = input.value.trim();
      if (!password) { err.textContent = 'Please type the password.'; err.hidden = false; return; }
      const r = await fetch('/api/child/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      }).then((x) => x.json()).catch(() => ({ ok: false }));
      if (r.ok) { cleanup(); onSuccess(password); }
      else { err.textContent = 'That password is not right.'; err.hidden = false; }
    };
  }

  async function loadModels() {
    try {
      modelData = await fetch('/api/sessions/' + sessionId + '/models').then((r) => r.json());
    } catch (e) {}
  }

  function renderModelMenu() {
    if (!modelData || !modelMenu) return;
    modelMenu.innerHTML = '';
    modelData.models.forEach((m) => {
      const b = document.createElement('button');
      b.type = 'button';
      if (m.id === modelData.current_model) b.classList.add('current');
      b.innerHTML =
        '<span>' + escapeAttr(m.name) + (m.recommended ? ' (recommended)' : '') + '</span>' +
        '<span class="model-price">' + escapeAttr(m.price_label) + '</span>';
      b.addEventListener('click', () => {
        modelMenu.hidden = true;
        chooseModel(m);
      });
      modelMenu.appendChild(b);
    });
  }

  function chooseModel(m) {
    if (!modelData || m.id === modelData.current_model) return;
    pendingModel = m;
    document.getElementById('model-warning-text').textContent =
      'Switch to "' + m.name + '"? If it is cheaper, the conversation will be ' +
      'summarised first, then the new model takes over.';
    window.__openModal(modelWarning, document.getElementById('model-switch-cancel'));
  }

  if (modelBtn && modelMenu) {
    modelBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const openMenu = () => {
        const wasOpen = !modelMenu.hidden;
        modelMenu.hidden = wasOpen;
        modelBtn.setAttribute('aria-expanded', wasOpen ? 'false' : 'true');
      };
      if (childMode && parentPassword === null) {
        promptParentPassword((password) => { parentPassword = password; openMenu(); });
        return;
      }
      if (!modelData) {
        await loadModels();
        renderModelMenu();
      }
      openMenu();
    });
    document.addEventListener('click', () => {
      if (modelMenu) modelMenu.hidden = true;
      if (modelBtn) modelBtn.setAttribute('aria-expanded', 'false');
    });
  }

  if (modelWarning) {
    const cancel = document.getElementById('model-switch-cancel');
    const confirm = document.getElementById('model-switch-confirm');
    if (cancel) cancel.addEventListener('click', () => {
      pendingModel = null;
      window.__closeModal();
    });
    if (confirm) confirm.addEventListener('click', async () => {
      if (!pendingModel) return;
      const m = pendingModel;
      pendingModel = null;
      window.__closeModal();
      setStatus('Switching to ' + m.name + '…');
      try {
        const resp = await fetch('/api/sessions/' + sessionId + '/model', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model: m.id, parent_password: parentPassword || '' }),
        });
        if (!resp.ok) {
          let msg = 'Could not switch model.';
          try { const d = await resp.json(); if (d && d.detail) msg = d.detail; } catch (e) {}
          clearStatus();
          showError(msg);
          return;
        }
        let finished = false;
        await readSSE(resp, (ev) => {
          if (ev.type === 'switch_status') {
            setStatus(ev.phase === 'compacting'
              ? 'Compacting conversation…'
              : 'Switching to ' + m.name + '…');
          } else if (ev.type === 'switch_done') {
            finished = true;
          }
        });
        if (finished) {
          location.reload();
        } else {
          clearStatus();
          showError('Could not switch model. Please try again.');
        }
      } catch (e) {
        clearStatus();
        showError('Could not switch model. Please try again.');
        return;
      }
    });
  }

  // ── Welcome modal (first run, unless dismissed) ───────────────────────────

  const welcomeModal = document.getElementById('welcome-modal');
  if (welcomeModal) {
    welcomeModal.querySelectorAll('.theme-opt[data-theme]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const theme = btn.dataset.theme;
        document.documentElement.dataset.theme = theme;
        welcomeModal.querySelectorAll('.theme-opt').forEach((b) => {
          b.classList.toggle('active', b === btn);
          b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
        });
        const fd = new FormData();
        fd.append('theme', theme);
        fetch('/_settings/prefs', { method: 'POST', body: fd });
      });
    });
    const go = document.getElementById('welcome-go');
    if (go) {
      const zoomOut = document.getElementById('welcome-zoom-out');
      const zoomIn = document.getElementById('welcome-zoom-in');
      if (zoomOut) zoomOut.addEventListener('click', () => window.__applyZoom(window.__readZoom() - 0.1));
      if (zoomIn) zoomIn.addEventListener('click', () => window.__applyZoom(window.__readZoom() + 0.1));
    }
    if (go) go.addEventListener('click', () => {
      const dontShow = document.getElementById('welcome-dont-show');
      if (dontShow && dontShow.checked) {
        const fd = new FormData();
        fd.append('welcome_seen', 'on');
        fetch('/_settings/prefs', { method: 'POST', body: fd });
      }
      const mic = document.getElementById('welcome-mic');
      if (mic) saveMicDevice(mic.value);
      window.__closeModal();
      welcomeModal.remove();
      if (!hasKey) {
        // No AI connected yet: send them to set it up.
        window.location.href = '/settings';
      } else if (micBtn && sttAvailable) {
        // Ready to go: start listening within this click's user gesture.
        startDictation();
      }
    });
    if (go) window.__openModal(welcomeModal, go);
  }

  // Remember the chat history and the input's own scroll, then position them.
  watchScroll(chatScroller, 'chat');
  watchScroll(textarea, 'textarea');

  // On load, scroll the history to the bottom only when the "jump back in"
  // projects are showing (so they aren't hidden behind the input). Otherwise go
  // back to wherever we were on this page. Runs a frame later so the restored
  // draft has already resized the composer.
  requestAnimationFrame(() => {
    const showingRecent = recentProjects && !recentProjects.hidden;
    if (showingRecent) {
      chatScroller.scrollTop = chatScroller.scrollHeight;
      // Put keyboard focus on the most recent project so it can be opened (or
      // tabbed through) right away, unless the welcome modal already has it.
      if (!welcomeModal) {
        const firstCard = recentProjects.querySelector('.project-card');
        if (firstCard) firstCard.focus();
      }
    } else {
      const saved = savedScroll('chat');
      if (saved !== null) chatScroller.scrollTop = saved;
      else scrollToBottom();
      // Opening a project drops focus on the Talk button so you can just speak.
      if (!isHome) {
        const talk = document.getElementById('mic-btn');
        if (talk) talk.focus();
        else textarea.focus();
      }
    }
  });

  reattach();
})();
