/* The chat client: streaming, a single status line, dictation, and read-aloud.
 *
 * There is deliberately no tool-call transcript — the user sees a conversation
 * and one status line that says, in plain words, what the assistant is doing.
 */
(function () {
  // ── Theme (the server is the source of truth) ─────────────────────────────

  /* Repaint the page for a new theme, easing the colours across rather than
   * flipping the screen's brightness in a single frame -- the largest
   * luminance change this app can produce. The transition class is added only
   * for the length of the switch, so nothing else is ever slowed by it, and
   * only when the theme actually changed. Shared by the settings buttons, the
   * welcome dialog, and the assistant's own `set_theme`. */
  window.__shiftTheme = function (theme) {
    const root = document.documentElement;
    if (!theme || root.dataset.theme === theme) return;
    root.classList.add('theme-shifting');
    root.dataset.theme = theme;
    setTimeout(() => root.classList.remove('theme-shifting'), 1100);
  };

  async function refreshTheme() {
    try {
      const data = await fetch('/api/theme').then((r) => r.json());
      window.__shiftTheme(data.theme);
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

  // ── Motion ─────────────────────────────────────────────────────────────────
  // Every programmatic scroll goes through this. A long smooth scroll -- a
  // whole page of content sliding past in one go -- is the classic trigger for
  // vestibular disorders, and the distances here are long by definition:
  // "back to top" and "jump to the newest message" only exist because you are
  // far away. So it is smooth for people who like the sense of where they went,
  // and an instant jump for anyone whose system says reduce motion. Live, not
  // read once, because the setting can change while the app is open.
  window.__scrollBehavior = function () {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
      ? 'auto' : 'smooth';
  };

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
  const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), ' +
    'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function focusableIn(root) {
    return Array.from(root.querySelectorAll(FOCUSABLE))
      .filter((el) => !el.hidden && el.offsetParent !== null);
  }

  // Everything outside the dialog is made `inert` while it is open, so it is
  // removed from the tab order AND from the screen reader's browse mode. These
  // dialogs are declared aria-modal, and without this a user could tab or
  // arrow straight out into the page behind with nothing to signal they had
  // left -- the welcome dialog is the first thing a new user meets, so getting
  // lost there is getting lost immediately.
  //
  // Walking the ancestor chain and inerting each level's other children means
  // this works wherever the dialog sits in the document; some live inside
  // <main>, so inerting <main> wholesale would inert the dialog itself.
  // Live regions must never be inerted: `inert` takes an element out of the
  // accessibility tree entirely, so inerting the announcer would silence
  // exactly the errors raised while a dialog is open -- a rejected password,
  // a failed model switch -- for the user who most needs to hear them.
  const ALWAYS_LIVE = new Set(['sr-announcer', 'toast-area']);

  let inerted = [];
  function setBackgroundInert(modalEl, on) {
    if (!on) {
      inerted.forEach((el) => el.removeAttribute('inert'));
      inerted = [];
      return;
    }
    inerted = [];
    let node = modalEl;
    while (node && node !== document.body && node.parentElement) {
      for (const sib of node.parentElement.children) {
        if (sib === node || sib.hasAttribute('inert')) continue;
        if (ALWAYS_LIVE.has(sib.id)) continue;
        sib.setAttribute('inert', '');
        inerted.push(sib);
      }
      node = node.parentElement;
    }
  }

  // Matches the closing ramp in the stylesheet. A timer rather than
  // `transitionend`, which never fires when the duration is zero and would
  // leave the dialog on screen for good.
  const MODAL_FADE_OUT_MS = 200;

  window.__openModal = function (el, focusEl) {
    window.__modalEl = el;
    window.__modalReturn = document.activeElement;
    el.hidden = false;
    // Next frame, so the browser has painted the hidden->visible change and the
    // opacity transition actually runs instead of being collapsed into it.
    requestAnimationFrame(() => el.classList.add('shown'));
    setBackgroundInert(el, true);
    const target = focusEl || focusableIn(el)[0];
    if (target) target.focus();
  };
  window.__closeModal = function () {
    if (!window.__modalEl) return false;
    const el = window.__modalEl;
    const ret = window.__modalReturn;
    window.__modalEl = null;
    window.__modalReturn = null;
    setBackgroundInert(el, false);
    el.classList.remove('shown');
    // Hidden once the fade has run, not in the same breath as taking the class
    // off -- doing both together is why a dialog eased in and then vanished.
    // `hidden` is what actually removes it from the page and from the reading
    // order, so it still has to happen; it just waits for the ramp.
    //
    // Guarded on `.shown` rather than cancelled, because reopening the same
    // dialog inside the fade puts the class straight back, and this must not
    // then hide the newly opened one out from under the user.
    const settle = () => { if (!el.classList.contains('shown')) el.hidden = true; };
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      settle();
    } else {
      setTimeout(settle, MODAL_FADE_OUT_MS);
    }
    if (ret && ret.focus) ret.focus();
    // So a dialog can clean up after itself however it was dismissed. The
    // camera needs this: Escape closes the dialog, and without a signal the
    // webcam would stay live -- and its light stay on -- until the tab closed.
    el.dispatchEvent(new CustomEvent('modalclosed'));
    return true;
  };

  // `inert` covers modern engines; this keeps Tab inside the dialog on anything
  // that does not support it, and wraps the cycle so the last control leads
  // back to the first rather than to the browser chrome.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab' || !window.__modalEl) return;
    const items = focusableIn(window.__modalEl);
    if (!items.length) { e.preventDefault(); return; }
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }, true);

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
  /* The document itself must never scroll: the app bar, the conversation and
   * the composer are each fixed or self-scrolling, so any movement of the root
   * is a bug that drags the whole interface off screen and leaves dead space
   * with no way to scroll back.
   *
   * `overflow: hidden` alone does not achieve this -- it stops the user
   * scrolling, not the browser, which still scrolls the root to reveal a
   * focus target. Putting it back on every scroll makes the whole class of
   * bug impossible rather than fixing one route into it. */
  window.addEventListener('scroll', () => {
    const root = document.documentElement;
    if (root.scrollTop) root.scrollTop = 0;
    if (root.scrollLeft) root.scrollLeft = 0;
  }, true);

  /* Internal navigation is buttons, not links.
   *
   * Hovering or focusing an <a href> makes the browser park its URL bubble in
   * the corner of the window, which sits there the whole time you are on the
   * control and looks like a website rather than an application. A button
   * shows nothing. Nothing is lost: this runs in its own window with no tabs,
   * and middle-clicking an internal link here would have opened it in the
   * user's real browser, which was never wanted.
   *
   * Links that leave the app stay links -- seeing where an external link goes
   * before you follow it is worth having. */
  document.addEventListener('click', (e) => {
    const nav = e.target.closest('[data-nav]');
    if (!nav) return;
    e.preventDefault();
    window.location.href = nav.dataset.nav;
  });

  const firstFocusable = (root) => focusableIn(root)[0] || null;

  /* Every skip link moves focus itself rather than letting the browser follow
   * the #fragment. Two reasons: an inert container (<main> on the settings
   * page) is not focusable, so the jump would land nowhere; and a fragment
   * jump asks the browser to scroll the target into view, which it did by
   * scrolling the document -- shoving the whole app up the screen. */
  document.querySelectorAll('.skip-link').forEach((link) => {
    link.addEventListener('click', (e) => {
      const href = link.getAttribute('href') || '';
      if (!href.startsWith('#')) return;
      e.preventDefault();
      const named = document.querySelector(href);
      if (!named) return;
      const target = named.matches(FOCUSABLE) ? named : firstFocusable(named);
      if (target) target.focus({ preventScroll: true });
    });
  });

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

    document.querySelectorAll('#sessions-menu [data-session-id]').forEach((a) => {
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

  // ── Sound cues ────────────────────────────────────────────────────────────
  //
  // For someone working by ear, a long turn is indistinguishable from a crash:
  // the screen changes, and nothing else does. These are the non-speech signals
  // for the three moments that matter -- finished, failed, still going.
  //
  // Synthesised rather than shipped as audio files: a few sine tones need no
  // assets, no network, and no decoding, and they can be retuned by editing a
  // number. Kept deliberately soft and low; an alert that startles you is one
  // you will turn off within a day.

  const SOUND = {
    // A quick reply does not need celebrating -- only chime once a turn has run
    // long enough that the user has plausibly looked away.
    minTurnMs: 10_000,
    // Long enough not to nag, short enough to reassure. Starts only after the
    // turn is already slow, so an ordinary reply never ticks at all.
    tickAfterMs: 12_000,
    tickEveryMs: 5_000,
  };

  // Defaults; the chat page overrides these from its own data attributes
  // once it has them. Settings only ever needs the preview.
  let soundCues = true;
  let soundTicks = false;
  let soundVolume = 0.4;
  let audioCtx = null;
  let tickTimer = 0;

  function ctx() {
    if (!audioCtx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      audioCtx = new AC();
    }
    // Browsers suspend audio until the user interacts; by the time a cue fires
    // they have sent a message, so resuming here is enough.
    if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
    return audioCtx;
  }

  // One soft sine blip. `gain` is relative to the user's volume setting.
  function blip(freq, startAt, durationSec, gain) {
    const ac = ctx();
    if (!ac) return;
    const t0 = ac.currentTime + startAt;
    const osc = ac.createOscillator();
    const amp = ac.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(freq, t0);
    // Ramped rather than switched, because an instant start or stop is heard
    // as a click regardless of how quiet the tone itself is.
    const peak = Math.max(0.0001, gain * soundVolume);
    amp.gain.setValueAtTime(0.0001, t0);
    amp.gain.exponentialRampToValueAtTime(peak, t0 + 0.015);
    amp.gain.exponentialRampToValueAtTime(0.0001, t0 + durationSec);
    osc.connect(amp).connect(ac.destination);
    osc.start(t0);
    osc.stop(t0 + durationSec + 0.02);
  }

  // Rising pair: settled, finished, nothing needed from you.
  function cueDone() { blip(660, 0, 0.16, 0.20); blip(880, 0.12, 0.22, 0.20); }
  // Falling pair, lower and longer: unmistakably not the finished sound, even
  // at low volume or through a laptop speaker.
  function cueError() { blip(400, 0, 0.20, 0.26); blip(300, 0.16, 0.32, 0.26); }
  // Barely there on purpose. This one repeats, so it has to be ignorable.
  function cueTick() { blip(520, 0, 0.05, 0.05); }

  function startTicks() {
    stopTicks();
    if (!soundTicks) return;
    tickTimer = setTimeout(function repeat() {
      cueTick();
      tickTimer = setTimeout(repeat, SOUND.tickEveryMs);
    }, SOUND.tickAfterMs);
  }

  function stopTicks() {
    clearTimeout(tickTimer);
    tickTimer = 0;
  }

  window.__previewSounds = function (volume) {
    if (typeof volume === 'number') soundVolume = volume;
    cueDone();
    setTimeout(cueError, 900);
  };

  // ── Chat (chat pages only) ────────────────────────────────────────────────

  const view = document.getElementById('chat-view');
  if (!view) return;

  soundCues = view.dataset.soundCues === '1';
  soundTicks = view.dataset.soundTicks === '1';
  soundVolume = parseFloat(view.dataset.soundVolume || '0.4');

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

  // ── File references ───────────────────────────────────────────────────────
  //
  // The assistant cannot tell the user to go and open a file: they have no file
  // manager open and no idea where the project lives. So a path it mentions
  // becomes something that works from here instead.
  //
  //  * anywhere in a sentence -> a chip that opens their file manager on it
  //  * alone on a line, with a line range -> a window showing those lines
  //
  // The second is the cheap way for the assistant to show code: it writes
  // twenty characters and the app fetches the rest, so nothing is paid for
  // twice and the excerpt cannot drift out of date with the file.

  async function revealFile(path) {
    try {
      const resp = await fetch('/api/files/reveal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, path }),
      });
      if (resp.ok) return;
      let detail = 'Could not open that folder.';
      try { const d = await resp.json(); if (d && d.detail) detail = d.detail; } catch (e) {}
      showToast(detail);
    } catch (e) {
      showToast('Could not open that folder.');
    }
  }

  function peekBlock(ref) {
    const path = ref.dataset.path;
    const start = parseInt(ref.dataset.line || '1', 10);
    const end = parseInt(ref.dataset.end || ref.dataset.line || '0', 10);

    const wrap = document.createElement('div');
    wrap.className = 'code-block file-peek';
    wrap.innerHTML =
      '<div class="code-head">' +
      '<span class="peek-path"></span>' +
      '<button type="button" class="peek-open">Show in folder</button>' +
      '</div><pre><code></code></pre>';
    const label = wrap.querySelector('.peek-path');
    const body = wrap.querySelector('code');
    label.textContent = path + ':' + start + (end && end !== start ? '-' + end : '');
    wrap.querySelector('.peek-open').addEventListener('click', () => revealFile(path));

    const query = new URLSearchParams({
      session_id: sessionId, path, start: String(start), end: String(end || 0),
    });
    fetch('/api/files/peek?' + query)
      .then((r) => (r.ok ? r.json() : r.json().then((d) => Promise.reject(d))))
      .then((data) => {
        body.innerHTML = window.md.withLineNumbers(
          window.md.highlight(data.text, data.lang), data.start
        );
        label.textContent = data.name + ':' + data.start +
          (data.end !== data.start ? '-' + data.end : '');
        label.title = data.path;
      })
      .catch((d) => {
        // Still useful: the path is named, and the button still works.
        wrap.classList.add('peek-error');
        body.textContent = (d && d.detail) || 'Could not read that file.';
      });
    return wrap;
  }

  const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|svg)$/i;

  // An image the assistant mentions on its own line is shown, not linked. It
  // has no other way to put a picture in the conversation, and describing a
  // screenshot in words to someone who could just look at it is absurd.
  function imageBlock(ref) {
    const path = ref.dataset.path;
    const wrap = document.createElement('figure');
    wrap.className = 'file-image';
    const img = document.createElement('img');
    img.src = '/api/files/image?session_id=' + encodeURIComponent(sessionId) +
              '&path=' + encodeURIComponent(path);
    img.alt = path.split(/[\\/]/).pop();
    img.loading = 'lazy';
    // A picture in a reply is there to be looked at, not carried off. Dragging
    // one attached it to the next message.
    img.draggable = false;
    const caption = document.createElement('figcaption');
    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'peek-open';
    open.textContent = 'Show in folder';
    open.addEventListener('click', () => revealFile(path));
    caption.append(document.createTextNode(img.alt + ' '), open);
    img.addEventListener('click', () => openImagePreview({ thumb: img.src, name: img.alt }));
    img.addEventListener('error', () => {
      wrap.classList.add('image-missing');
      img.remove();
      caption.prepend(document.createTextNode('Could not show '));
    });
    wrap.append(img, caption);
    return wrap;
  }

  function upgradeFileRefs(el) {
    el.querySelectorAll('button.file-ref').forEach((ref) => {
      const parent = ref.parentElement;
      // "Alone on its own line" means it is the only thing in its paragraph.
      const alone = parent && parent.tagName === 'P' &&
        parent.textContent.trim() === ref.textContent.trim();
      if (alone && IMAGE_EXT.test(ref.dataset.path || '')) {
        parent.replaceWith(imageBlock(ref));
        return;
      }
      if (alone && ref.dataset.line) {
        parent.replaceWith(peekBlock(ref));
        return;
      }
      ref.addEventListener('click', () => revealFile(ref.dataset.path));
    });
  }

  function renderMarkdown(el) {
    const raw = el.textContent;
    if (raw.trim()) el.innerHTML = window.md.render(raw);
  }

  /* Render, then turn file references into chips and peek windows. Only for
   * finished text: `renderMarkdown` runs on every streamed token, and
   * upgrading there would start a fetch per token and rebuild the peek
   * underneath the user on each one. */
  function renderFinal(el) {
    renderMarkdown(el);
    upgradeFileRefs(el);
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

  // Matches the server-rendered markup in chat_messages.html. Which side a
  // bubble sits on and what colour it is are the only visual cues to who is
  // speaking, and neither reaches a screen reader, so the speaker is named in
  // text that only assistive technology sees.
  const SPEAKER = { user: 'You said:', assistant: 'Assistant said:' };

  function bubble(role) {
    const wrap = document.createElement('div');
    wrap.className = 'message ' + role;
    if (SPEAKER[role]) {
      const who = document.createElement('span');
      who.className = 'sr-only';
      who.textContent = SPEAKER[role];
      wrap.appendChild(who);
    }
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
    renderFinal(content);
    scrollToBottom();
    return content.closest('.message');
  }

  function appendAction(sessionId, name) {
    const wrap = document.createElement('div');
    wrap.className = 'message action';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'open-project-btn';
    button.dataset.nav = '/sessions/' + sessionId;
    button.textContent = name ? 'Open ' + name + ' \u2192' : 'Open this project \u2192';
    wrap.appendChild(button);
    messages.appendChild(wrap);
    scrollToBottom();
    return button;
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
  // Only the button animates. Following a reply as it streams calls
  // scrollToBottom on every token, and a smooth scroll there would still be
  // catching up when the next one starts.
  function glideToBottom() {
    if (!chatScroller) return;
    chatScroller.scrollTo({ top: chatScroller.scrollHeight,
                            behavior: window.__scrollBehavior() });
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
      glideToBottom();
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
    // The status bar is a live region and `content` events arrive per token,
    // so this is called with "Writing a reply..." hundreds of times a turn.
    // Rewriting the node with the same string can make a screen reader
    // re-announce it, so an unchanged status is left strictly alone.
    if (statusBar.textContent === text && !statusBar.hidden) return;
    statusBar.textContent = text;
    statusBar.hidden = false;
  }

  function clearStatus() {
    statusBar.hidden = true;
    statusBar.textContent = '';
  }

  // ── Screen reader announcements ───────────────────────────────────────────

  const srAnnouncer = document.getElementById('sr-announcer');
  let announceTimer = 0;

  // Say something once, out of band. Used for finished replies and errors,
  // which are the two things a user who cannot see the screen must not miss.
  function announce(text) {
    if (!srAnnouncer) return;
    text = (text || '').trim();
    if (!text) return;
    clearTimeout(announceTimer);
    // Clearing first guarantees a change even when the same text repeats,
    // which is what makes a repeated error announce the second time too.
    srAnnouncer.textContent = '';
    announceTimer = setTimeout(() => { srAnnouncer.textContent = text; }, 50);
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

  // Apply the welcome question's answer to the page that is already open, so
  // the first reply behaves the way the user just asked for rather than
  // waiting for a reload they have no reason to perform.
  function applyA11yMode(mode) {
    const usesReader = mode === 'screen_reader';
    ttsAutoEnabled = mode === 'read_aloud';
    soundTicks = usesReader;
    const btn = document.getElementById('tts-btn');
    if (btn) btn.setAttribute('aria-pressed', ttsAutoEnabled ? 'true' : 'false');
  }
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
      upgradeFileRefs(assistantEl);
      speak(assistantBuffer, assistantEl.closest('.bubble'));
      addLinkPreviews(assistantEl);
      // Announce the finished reply once. Skipped when read-aloud is on,
      // because Kokoro is about to say the same words and two voices talking
      // over each other is worse than either alone.
      if (!ttsAutoEnabled) announce(assistantBuffer);
      // Only for a turn slow enough that the user may have looked away, and
      // never when read-aloud is on -- the reply speaking is itself the signal.
      if (soundCues && !ttsAutoEnabled && turnStartedAt &&
          Date.now() - turnStartedAt >= SOUND.minTurnMs) {
        cueDone();
      }
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
    stopTicks();
    clearStatus();
    maybeAutoOpen();
    scrollToBottom();
  }

  /* Creating a project used to throw the user into it a moment later, in the
   * middle of reading the reply that said it had been made. Disorienting for
   * anyone, and much worse if you cannot see it happen -- the page changes
   * underneath you with no warning and no way back to what you were reading.
   *
   * The button was always there; it was just pointless while this navigated
   * anyway. Now it is the only way in, so the user moves when they are ready.
   * Keyboard focus lands on it, so pressing Enter is all it takes. */
  function maybeAutoOpen() {
    if (!pendingOpen || !isHome) return;
    pendingOpen = null;
    const button = messages.querySelector('.message.action .open-project-btn:last-of-type')
      || [...messages.querySelectorAll('.open-project-btn')].pop();
    if (!button) return;
    button.focus({ preventScroll: true });
    scrollToBottom();
    announce(button.textContent.trim() + '. Press Enter to open it.');
  }

  function showError(text) {
    const wrap = document.createElement('div');
    wrap.className = 'message error';
    wrap.textContent = text || 'Something went wrong. Please try again.';
    messages.appendChild(wrap);
    scrollToBottom();
    // The message log is not a live region, so an error would otherwise appear
    // in silence -- the user would sit waiting for a reply that never comes.
    announce(wrap.textContent);
    stopTicks();
    // Unlike the finished chime this fires however short the turn was, and
    // regardless of read-aloud: a failure is the one thing you must not miss.
    if (soundCues) cueError();
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
    startTicks();
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

  // Show a full-size image from an attachment chip. Clicking the picture is
  // the obvious thing to try, and there is nowhere else to see what you
  // attached before sending it.
  // ── Zooming a picture ──────────────────────────────────────────────────
  //
  // Not a nicety. A photo of a homework page, a screenshot of an error, a
  // phone picture of a wiring diagram -- the whole reason to attach one is to
  // look at it, and "fits on the screen" is not the same as "readable".
  // Bounds around "fits the screen". Out as well as in, but not so far in
  // either direction that the picture becomes a stamp or a single blurred
  // pixel and the way back is not obvious.
  const PREVIEW_MIN = 0.4;
  const PREVIEW_MAX = 6;
  const PREVIEW_STEP = 1.35;
  // Room around the picture at Fit, so it is not jammed against the edges of
  // the screen, and clear of the bar along the bottom.
  const PREVIEW_INSET = 44;
  const PREVIEW_BAR = 104;
  let pvScale = 1;

  // The width the picture fits the screen at. Everything else is a multiple
  // of it, so "100%" means "as big as the screen allows" rather than the
  // picture's own pixel count, which is not a number anyone is thinking about.
  let pvFitW = 0;

  function pvMeasure() {
    const stage = document.getElementById('preview-stage');
    const img = stage && stage.querySelector('img');
    if (!img || !img.naturalWidth) return;
    const room = Math.max(120, stage.clientWidth - PREVIEW_INSET * 2);
    const tall = Math.max(120, stage.clientHeight - PREVIEW_INSET - PREVIEW_BAR);
    pvFitW = Math.min(room / img.naturalWidth, tall / img.naturalHeight) * img.naturalWidth;
  }

  function pvApply(announceIt) {
    const stage = document.getElementById('preview-stage');
    const img = stage && stage.querySelector('img');
    if (!img) return;
    if (!pvFitW) pvMeasure();
    // The picture's own size changes. Scaling it with a transform left the
    // layout box the size it started at, so the window never grew and zooming
    // in only slid a magnified crop about inside it.
    img.style.width = Math.round(pvFitW * pvScale) + 'px';
    img.style.height = 'auto';
    stage.classList.toggle('zoomed', pvScale > 1);
    if (pvScale <= 1) { stage.scrollLeft = 0; stage.scrollTop = 0; }
    const label = Math.round(pvScale * 100) + '%';
    const out = document.getElementById('preview-zoom-value');
    if (out) out.textContent = label;
    const minus = document.getElementById('preview-zoom-out');
    const plus = document.getElementById('preview-zoom-in');
    if (minus) minus.disabled = pvScale <= PREVIEW_MIN + 0.001;
    if (plus) plus.disabled = pvScale >= PREVIEW_MAX - 0.001;
    if (announceIt) announce('Zoom ' + label);
  }

  // `atX`/`atY` are where in the window to hold still, from its top left.
  // Without holding something still, zooming walks away from the thing you
  // were trying to look at.
  function pvZoom(factor, atX, atY) {
    const stage = document.getElementById('preview-stage');
    if (!stage) return;
    const before = pvScale;
    pvScale = Math.min(PREVIEW_MAX, Math.max(PREVIEW_MIN, pvScale * factor));
    if (pvScale === before) return;
    const holdX = atX === undefined ? stage.clientWidth / 2 : atX;
    const holdY = atY === undefined ? stage.clientHeight / 2 : atY;
    const ratio = pvScale / before;
    const left = (stage.scrollLeft + holdX) * ratio - holdX;
    const top = (stage.scrollTop + holdY) * ratio - holdY;
    pvApply(true);
    stage.scrollLeft = left;
    stage.scrollTop = top;
  }

  function pvReset() {
    pvScale = 1;
    pvMeasure();
    pvApply(false);
    const stage = document.getElementById('preview-stage');
    if (stage) { stage.scrollLeft = 0; stage.scrollTop = 0; }
  }

  function setUpPreviewZoom() {
    const modal = document.getElementById('image-preview');
    const stage = document.getElementById('preview-stage');
    if (!modal || !stage) return;
    const img = stage.querySelector('img');

    const btn = (id, fn) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('click', fn);
    };
    btn('preview-zoom-in', () => pvZoom(PREVIEW_STEP));
    btn('preview-zoom-out', () => pvZoom(1 / PREVIEW_STEP));
    btn('preview-zoom-reset', () => { pvReset(); announce('Zoom 100%'); });

    stage.addEventListener('wheel', (e) => {
      e.preventDefault();
      const box = stage.getBoundingClientRect();
      pvZoom(e.deltaY < 0 ? PREVIEW_STEP : 1 / PREVIEW_STEP,
             e.clientX - box.left, e.clientY - box.top);
    }, { passive: false });

    stage.addEventListener('dblclick', (e) => {
      if (pvScale > 1) { pvReset(); return; }
      const box = stage.getBoundingClientRect();
      pvZoom(2.5, e.clientX - box.left, e.clientY - box.top);
    });

    // Dragging pans. It used to start a native image drag, which the app's own
    // file-drop handler then caught -- so pulling the picture sideways to see
    // the rest of it quietly attached that picture to the next message, and
    // the only sign was a chip you had not put there.
    let panning = false, lastX = 0, lastY = 0;
    stage.addEventListener('pointerdown', (e) => {
      if (pvScale <= 1) return;
      panning = true;
      lastX = e.clientX;
      lastY = e.clientY;
      stage.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    stage.addEventListener('pointermove', (e) => {
      if (!panning) return;
      // The window scrolls. Nothing here has to clamp the picture inside the
      // window any more -- a scroll container cannot be scrolled past its own
      // content, so it is not possible to drag the picture out of sight.
      stage.scrollLeft -= e.clientX - lastX;
      stage.scrollTop -= e.clientY - lastY;
      lastX = e.clientX;
      lastY = e.clientY;
    });
    const stopPan = () => { panning = false; };
    stage.addEventListener('pointerup', stopPan);
    stage.addEventListener('pointercancel', stopPan);
    if (img) img.addEventListener('dragstart', (e) => e.preventDefault());

    // The keyboard gets the same picture. Arrows pan by a tenth of the window,
    // which is a step you can follow rather than a jump.
    modal.addEventListener('keydown', (e) => {
      const step = stage.clientWidth / 10;
      if (e.key === '+' || e.key === '=') { pvZoom(PREVIEW_STEP); }
      else if (e.key === '-' || e.key === '_') { pvZoom(1 / PREVIEW_STEP); }
      else if (e.key === '0') { pvReset(); announce('Zoom 100%'); }
      else if (e.key === 'ArrowLeft') { stage.scrollLeft -= step; }
      else if (e.key === 'ArrowRight') { stage.scrollLeft += step; }
      else if (e.key === 'ArrowUp') { stage.scrollTop -= step; }
      else if (e.key === 'ArrowDown') { stage.scrollTop += step; }
      else return;
      e.preventDefault();
    });

    window.addEventListener('resize', () => {
      if (modal.hidden) return;
      pvMeasure();
      pvApply(false);
    });
  }
  setUpPreviewZoom();

  function openImagePreview(att) {
    const modal = document.getElementById('image-preview');
    if (!modal || !att.thumb) return;
    const img = modal.querySelector('img');
    img.src = att.thumb;
    img.alt = att.name;
    img.draggable = false;
    modal.querySelector('.preview-name').textContent = att.name;
    // naturalWidth is 0 until it has loaded, so fit again when it arrives.
    img.onload = () => { if (!modal.hidden) pvReset(); };
    pvFitW = 0;
    pvReset();
    window.__openModal(modal, modal.querySelector('.preview-close'));
  }

  function moveAttachment(from, to) {
    if (to < 0 || to >= attachments.length) return;
    const [moved] = attachments.splice(from, 1);
    attachments.splice(to, 0, moved);
    renderAttachments();
    // Keep the keyboard on the button that was just used, so a run of moves
    // does not need the user to find it again each time.
    const chips = attachmentsBox.querySelectorAll('.attachment-chip');
    const btn = chips[to] && chips[to].querySelector(to < from ? '.move-left' : '.move-right');
    if (btn) btn.focus();
    announce(moved.name + ' moved to position ' + (to + 1) + ' of ' + attachments.length);
  }

  function removeAttachment(index) {
    const [gone] = attachments.splice(index, 1);
    if (gone && gone.thumb && gone.thumb.startsWith('blob:')) { try { URL.revokeObjectURL(gone.thumb); } catch (e) {} }
    renderAttachments();
  }

  function clearAttachments() {
    const count = attachments.length;
    attachments.forEach((a) => {
      if (a.thumb && a.thumb.startsWith('blob:')) { try { URL.revokeObjectURL(a.thumb); } catch (e) {} }
    });
    attachments.length = 0;
    renderAttachments();
    announce(count === 1 ? 'Attachment removed.' : 'All ' + count + ' attachments removed.');
    if (textarea) textarea.focus();
  }

  let dragFrom = null;

  /* Attachments live through a reload the same way the draft does. The files
   * themselves are already saved on disk by the upload, so only the list needs
   * keeping -- and the thumbnail is re-pointed at the server, because the blob
   * URL made when the file was dropped dies with the page. */
  const attachKey = 'attachments:' + sessionId;

  function saveAttachments() {
    try {
      localStorage.setItem(attachKey, JSON.stringify(
        attachments.map((a) => ({ path: a.path, name: a.name, isDir: !!a.isDir,
                                  isImage: !!a.thumb }))
      ));
    } catch (e) {}
  }

  function restoreAttachments() {
    let saved = [];
    try {
      saved = JSON.parse(localStorage.getItem(attachKey) || '[]');
    } catch (e) {
      // Only a corrupt value is expected here. A blanket catch previously hid
      // a ReferenceError -- this ran before `attachKey` was declared -- and
      // attachments silently never came back.
      return;
    }
    if (!Array.isArray(saved) || !saved.length) return;
    saved.forEach((a) => {
      if (!a || !a.path) return;
      attachments.push({
        path: a.path,
        name: a.name || 'file',
        isDir: !!a.isDir,
        thumb: a.isImage ? '/api/files/attachment?path=' + encodeURIComponent(a.path) : null,
      });
    });
    renderAttachments();
  }

  function renderAttachments() {
    if (!attachmentsBox) return;
    attachmentsBox.hidden = attachments.length === 0;
    attachmentsBox.innerHTML = '';
    saveAttachments();
    attachments.forEach((a, i) => {
      const chip = document.createElement('span');
      chip.className = 'attachment-chip';

      // Dragging is the quick way for anyone with a mouse; the arrow buttons
      // below do the same job for everyone else. Neither replaces the other.
      if (attachments.length > 1) {
        chip.draggable = true;
        chip.dataset.index = String(i);
        chip.addEventListener('dragstart', (e) => {
          dragFrom = i;
          chip.classList.add('dragging');
          e.dataTransfer.effectAllowed = 'move';
          // Some payload is required for a drag to start at all; the marker
          // also lets the file-drop overlay tell this apart from a real file.
          e.dataTransfer.setData('text/x-attachment', String(i));
        });
        chip.addEventListener('dragend', () => {
          dragFrom = null;
          attachmentsBox.querySelectorAll('.attachment-chip')
            .forEach((c) => c.classList.remove('dragging', 'drop-target'));
        });
        chip.addEventListener('dragover', (e) => {
          if (dragFrom === null || dragFrom === i) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = 'move';
          // Clear them all and mark the one under the pointer, rather than
          // pairing up enter and leave: dragleave also fires when the pointer
          // crosses onto a child of the chip, and a chip is made of five of
          // them, so the paired version flickered on the way across.
          attachmentsBox.querySelectorAll('.attachment-chip')
            .forEach((c) => c.classList.remove('drop-target'));
          chip.classList.add('drop-target');
        });
        chip.addEventListener('dragleave', () => {
          chip.classList.remove('drop-target');
        });
        chip.addEventListener('drop', (e) => {
          if (dragFrom === null || dragFrom === i) return;
          e.preventDefault();
          e.stopPropagation();
          chip.classList.remove('drop-target');
          moveAttachment(dragFrom, i);
          dragFrom = null;
        });
      }

      // The number the user can say out loud. One sequence for everything, in
      // the order they are shown, renumbered when they are reordered -- which
      // is the whole point of it: "swap the last two, then use number 2".
      // Numbering images and files separately would break that the moment the
      // two kinds are interleaved, and "delete one" would stop being an
      // answer to anything.
      const badge = document.createElement('span');
      badge.className = 'attachment-num';
      badge.textContent = String(i + 1);
      badge.setAttribute('aria-hidden', 'true');
      chip.appendChild(badge);

      const thumb = document.createElement('span');
      thumb.className = 'attachment-thumb';
      if (a.thumb) {
        const img = document.createElement('img');
        img.src = a.thumb;
        img.alt = '';
        // Or the browser's own image drag beats the chip's reorder drag, and
        // ends with the picture attached a second time.
        img.draggable = false;
        thumb.appendChild(img);
      } else {
        thumb.innerHTML = a.isDir ? FOLDER_ICON_SVG : FILE_ICON_SVG;
      }

      // Spoken, the kind matters and the icon does not carry it. Sighted users
      // read it off the thumbnail, so it costs them no space.
      const kind = a.isDir ? 'folder' : (a.thumb ? 'image' : 'file');
      const said = 'Number ' + (i + 1) + ' of ' + attachments.length + ', ' + kind + ', ' + a.name;

      // The picture and its name open a preview; only images have one to show.
      const face = document.createElement('button');
      face.type = 'button';
      face.className = 'attachment-face';
      face.appendChild(thumb);
      const label = document.createElement('span');
      label.className = 'attachment-name';
      label.textContent = a.name;
      face.appendChild(label);
      // The chip truncates the name, so the whole one -- and where it came
      // from -- lives in the tooltip.
      face.title = a.name + '\n' + a.path;
      if (a.thumb) {
        face.setAttribute('aria-label', 'Preview ' + said);
        face.addEventListener('click', () => openImagePreview(a));
      } else {
        face.setAttribute('aria-label', said);
        face.disabled = true;
      }
      chip.appendChild(face);

      // Reordering, because the user refers to attachments by position --
      // "the first picture is the error, the second is what I wanted".
      if (attachments.length > 1) {
        const left = document.createElement('button');
        left.type = 'button';
        left.className = 'move move-left';
        left.innerHTML = '&#8249;';
        left.title = 'Move earlier';
        left.setAttribute('aria-label', 'Move ' + said + ' earlier');
        left.disabled = i === 0;
        left.addEventListener('click', () => moveAttachment(i, i - 1));
        chip.appendChild(left);

        const right = document.createElement('button');
        right.type = 'button';
        right.className = 'move move-right';
        right.innerHTML = '&#8250;';
        right.title = 'Move later';
        right.setAttribute('aria-label', 'Move ' + said + ' later');
        right.disabled = i === attachments.length - 1;
        right.addEventListener('click', () => moveAttachment(i, i + 1));
        chip.appendChild(right);
      }

      const rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'rm';
      rm.title = 'Remove ' + a.name;
      rm.setAttribute('aria-label', 'Remove ' + said);
      rm.textContent = '\u00d7';
      rm.addEventListener('click', () => removeAttachment(i));
      chip.appendChild(rm);

      attachmentsBox.appendChild(chip);
    });

    // One button to undo a mistaken drag of a whole folder, rather than
    // clicking a hundred crosses.
    if (attachments.length > 1) {
      const clear = document.createElement('button');
      clear.type = 'button';
      clear.className = 'attachment-clear';
      clear.title = 'Remove all attachments';
      clear.setAttribute('aria-label', 'Remove all ' + attachments.length + ' attachments');
      clear.innerHTML =
        '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">' +
        '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
        'd="M4 7h16M10 7V5h4v2M6 7l1 13h10l1-13M10 11v6M14 11v6"/></svg>' +
        '<span>Clear all</span>';
      clear.addEventListener('click', clearAttachments);
      attachmentsBox.appendChild(clear);
    }
  }


  function clearAttachments() {
    attachments.forEach((a) => { if (a.thumb && a.thumb.startsWith('blob:')) { try { URL.revokeObjectURL(a.thumb); } catch (e) {} } });
    attachments = [];
    renderAttachments();
  }

  function messageWithAttachments(base) {
    if (!attachments.length) return base;
    // Numbered, and with the name the user is looking at. The model used to
    // get bare paths, so "delete number 2" and "the second picture" had
    // nothing to land on but the order of a list nobody had told it was
    // ordered -- and the path it holds is a name this app invented, which the
    // user has never seen and would not recognise if they had.
    const lines = attachments.map((a, i) => {
      const kind = a.isDir ? 'folder' : (a.thumb ? 'image' : 'file');
      return (i + 1) + '. ' + a.name + ' (' + kind + ') - ' + a.path;
    }).join('\n');
    const header =
      'Attached (the user sees these numbers and may refer to them by number):\n' + lines;
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
        const resp = await fetch('/api/sessions/' + sessionId + '/upload', {
          method: 'POST',
          body: fd,
        });
        const data = await resp.json().catch(() => null);
        // The server's own words when it has some -- "that file is too big"
        // tells you what to do next; "could not attach" tells you to try
        // again forever.
        if (!resp.ok) throw new Error((data && data.detail) || 'Could not attach that file.');
        if (!data || !data.path) throw new Error('Could not attach ' + file.name);
        attachments.push({ path: data.path, name: data.name || file.name, thumb });
        renderAttachments();
        clearStatus();
      } catch (e) {
        if (thumb) { try { URL.revokeObjectURL(thumb); } catch (err) {} }
        setStatus(e && e.message ? e.message : 'Could not attach ' + file.name);
      }
    }
  }

  // ── Camera ────────────────────────────────────────────────────────────────
  //
  // Showing the assistant something that is not on the computer: homework on
  // paper, a label, a device with an error on its screen, a drawing. Typing a
  // description of it is exactly the step this app exists to remove.
  //
  // The button only appears once a camera is known to exist, so nobody is
  // offered something that will fail.

  const cameraBtn = document.getElementById('camera-btn');
  const cameraModal = document.getElementById('camera-modal');
  const cameraVideo = document.getElementById('camera-video');
  const cameraShot = document.getElementById('camera-shot');
  const cameraError = document.getElementById('camera-error');
  let cameraStream = null;

  async function hasCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return false;
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      return devices.some((d) => d.kind === 'videoinput');
    } catch (e) {
      return false;
    }
  }

  function stopCamera() {
    if (cameraStream) {
      cameraStream.getTracks().forEach((t) => { try { t.stop(); } catch (e) {} });
      cameraStream = null;
    }
    if (cameraVideo) cameraVideo.srcObject = null;
  }

  function closeCamera() {
    cameraModal.classList.remove('camera-ready');
    stopCamera();
    if (window.__modalEl === cameraModal) window.__closeModal();
  }

  async function openCamera() {
    if (!cameraModal) return;
    cameraError.hidden = true;
    // Starting a camera takes a second or two. Without this the dialog is just
    // a black rectangle and it is not obvious anything is happening.
    cameraModal.classList.add('camera-starting');
    cameraModal.classList.remove('camera-ready');
    // Nothing to take a picture of yet. Enabled once a real frame arrives, so
    // pressing it early cannot produce an empty photo.
    setCameraReady(false);
    window.__openModal(cameraModal, cameraShot);
    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 960 } },
        audio: false,
      });
      cameraVideo.srcObject = cameraStream;
      await cameraVideo.play().catch(() => {});
      // Wait for a real frame, not just the play() promise: the element can be
      // playing and still be showing nothing.
      if (!cameraVideo.videoWidth) {
        await new Promise((resolve) => {
          cameraVideo.addEventListener('loadeddata', resolve, { once: true });
          setTimeout(resolve, 3000);
        });
      }
      cameraModal.classList.remove('camera-starting');
      cameraModal.classList.add('camera-ready');
      setCameraReady(true);
      announce('Camera ready. Press Space to take the photo.');
    } catch (e) {
      // Denied, in use, or unplugged since the button appeared. Say which in
      // ordinary words rather than showing the browser's exception.
      const denied = e && (e.name === 'NotAllowedError' || e.name === 'SecurityError');
      cameraError.textContent = denied
        ? 'This app needs permission to use your camera. Allow it in your browser, then try again.'
        : 'No camera was available. It may be unplugged, or another program may be using it.';
      cameraError.hidden = false;
      cameraModal.classList.remove('camera-starting');
      announce(cameraError.textContent);
    }
  }

  function setCameraReady(ready) {
    const take = document.getElementById('camera-take');
    if (take) take.disabled = !ready;
    if (cameraShot) cameraShot.disabled = !ready;
  }

  function takePhoto() {
    if (!cameraStream || !cameraVideo.videoWidth) return;
    const canvas = document.createElement('canvas');
    canvas.width = cameraVideo.videoWidth;
    canvas.height = cameraVideo.videoHeight;
    // Drawn unmirrored: the preview is flipped so aiming feels natural, but the
    // photo has to show text the right way round or it is useless to read.
    canvas.getContext('2d').drawImage(cameraVideo, 0, 0);

    cameraShot.classList.add('flash');
    setTimeout(() => cameraShot.classList.remove('flash'), 240);

    canvas.toBlob((blob) => {
      if (!blob) {
        cameraError.textContent = 'The photo could not be saved. Please try again.';
        cameraError.hidden = false;
        return;
      }
      const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const file = new File([blob], 'photo-' + stamp + '.jpg', { type: 'image/jpeg' });
      closeCamera();
      announce('Photo taken and attached to your message.');
      if (soundCues) cueDone();
      handleDroppedFiles([file]);
    }, 'image/jpeg', 0.92);
  }

  // Clicking the backdrop, or the Close button, dismisses the picture preview.
  const imagePreview = document.getElementById('image-preview');
  if (imagePreview) {
    imagePreview.querySelector('.preview-close')
      .addEventListener('click', () => window.__closeModal());
    imagePreview.addEventListener('click', (e) => {
      if (e.target === imagePreview) window.__closeModal();
    });
  }

  if (cameraBtn && cameraModal) {
    const refreshCameraBtn = () => hasCamera().then((present) => {
      cameraBtn.hidden = !present;
    });
    refreshCameraBtn();
    // Checking once at load meant plugging a webcam in after opening the app
    // did nothing until a reload. The browser fires this whenever the device
    // list changes, which covers both plugging in and unplugging.
    if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
      navigator.mediaDevices.addEventListener('devicechange', refreshCameraBtn);
    }
    cameraBtn.addEventListener('click', openCamera);
    cameraShot.addEventListener('click', takePhoto);
    document.getElementById('camera-take').addEventListener('click', takePhoto);
    document.getElementById('camera-cancel').addEventListener('click', closeCamera);
    // Space anywhere in the dialog takes the photo. Enter already activates
    // whichever button has focus, so it needs nothing here.
    cameraModal.addEventListener('keydown', (e) => {
      if (e.key === ' ' || e.key === 'Spacebar') {
        e.preventDefault();
        e.stopPropagation();
        takePhoto();
      }
    });
    // Escape is handled globally and closes the dialog; make sure the camera
    // light goes out with it rather than staying on until the tab closes.
    cameraModal.addEventListener('modalclosed', stopCamera);
  }

  // After the definitions above, not before them.
  restoreAttachments();

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
      /* Stop capturing before waiting for anything.
       *
       * This used to await the final transcript with the microphone still
       * live, and the worklet kept posting audio down the socket the whole
       * time. So pressing Stop did nothing visible, the server's buffer grew
       * faster than it could transcribe it, and the wait ran to its timeout
       * with the CPU pinned -- the button stuck on "Stop" and the machine
       * roaring. Everything the user can see reacts on the first frame now,
       * and the audio the server still has to chew through is fixed at the
       * moment they pressed the button. */
      listening = false;
      teardownAudio();
      if (micBtn) micBtn.classList.remove('recording');
      if (micLabel) micLabel.textContent = 'Talk';
      textarea.placeholder = 'Finishing what you said\u2026';

      const final = await new Promise((resolve) => {
        // Generous, because it is bounded work now: the last few seconds of
        // audio, transcribed once. It should never be reached.
        const timeout = setTimeout(() => resolve(null), 20000);
        ws.onmessage = (e) => {
          let d; try { d = JSON.parse(e.data); } catch (_) { return; }
          if (d.partial === false) {
            clearTimeout(timeout);
            resolve((d.text || '').trim());
          }
        };
        try { ws.send('end'); } catch (_) { clearTimeout(timeout); resolve(null); }
      });
      // setComposerText only writes while `listening`, and we just cleared it.
      if (final) {
        writingFromDictation = true;
        textarea.value = (anchor ? anchor + ' ' : '') + final;
        textarea.dispatchEvent(new Event('input'));
        writingFromDictation = false;
        textarea.scrollTop = textarea.scrollHeight;
      }
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
          renderFinal(content);
          addLinkPreviews(content);
        }
      }
    } catch (e) {}
    endTurn();
    scrollToBottom();
  }

  document.querySelectorAll('.content[data-markdown], .summary-text[data-markdown]').forEach((el) => {
    renderFinal(el);
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
      // The provider is part of the identity, not decoration: OpenRouter
      // resells most of what the first-party keys offer, so "Claude Opus 5"
      // can appear twice, at different prices and against different accounts.
      // "recommended" rides on the provider line rather than the name: inline
      // it pushed names onto three wrapped lines and made the menu enormous.
      const via = [m.provider_label, m.recommended ? 'recommended' : '']
        .filter(Boolean).join(' \u00b7 ');
      b.innerHTML =
        '<span class="model-name"><span class="model-title">' + escapeAttr(m.name) + '</span>' +
        '<span class="model-via">' + escapeAttr(via) + '</span></span>' +
        '<span class="model-price">' + escapeAttr(m.price_label) + '</span>';
      b.addEventListener('click', () => {
        modelMenu.hidden = true;
        modelBtn.setAttribute('aria-expanded', 'false');
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
    // Loading and rendering happen here rather than at each call site. The
    // child-mode path used to open the menu straight after the password was
    // accepted, skipping the load entirely -- so unlocking it produced an
    // empty menu: a thin bar above the button with nothing in it, which the
    // next click then closed.
    async function showMenu() {
      if (!modelData) {
        await loadModels();
        renderModelMenu();
      }
      modelMenu.hidden = false;
      modelBtn.setAttribute('aria-expanded', 'true');
      const first = modelMenu.querySelector('button');
      if (first) first.focus();
    }
    function hideMenu() {
      modelMenu.hidden = true;
      modelBtn.setAttribute('aria-expanded', 'false');
      // Closing the menu without switching spends the password too. Otherwise
      // an unlock left lying open is an unlock the parent has forgotten about.
      if (!pendingModel) parentPassword = null;
    }
    modelBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!modelMenu.hidden) { hideMenu(); return; }
      if (childMode) {
        // Asked every time, and the answer buys exactly one switch. Holding it
        // for the page meant a parent who changed the model and walked away
        // left it changeable, and putting it straight back is the one thing
        // the lock exists to stop. The server checks it again on the switch
        // itself, so a build with no password modal is not a way past it.
        promptParentPassword((password) => { parentPassword = password; showMenu(); });
        return;
      }
      await showMenu();
    });
    document.addEventListener('click', () => {
      if (!modelMenu.hidden) hideMenu();
    });
    modelMenu.addEventListener('click', (e) => e.stopPropagation());
    modelMenu.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { hideMenu(); modelBtn.focus(); }
    });
  }

  if (modelWarning) {
    const cancel = document.getElementById('model-switch-cancel');
    const confirm = document.getElementById('model-switch-confirm');
    if (cancel) cancel.addEventListener('click', () => {
      pendingModel = null;
      parentPassword = null;
      window.__closeModal();
    });
    if (confirm) confirm.addEventListener('click', async () => {
      if (!pendingModel) return;
      const m = pendingModel;
      pendingModel = null;
      window.__closeModal();
      setStatus('Switching to ' + m.name + '…');
      // Spent on this one switch, whether or not it works. The next one asks
      // again, so a child cannot follow a parent's change with their own.
      const password = parentPassword;
      parentPassword = null;
      try {
        const resp = await fetch('/api/sessions/' + sessionId + '/model', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model: m.id, parent_password: password || '' }),
        });
        if (!resp.ok) {
          let msg = 'Could not switch model.';
          try { const d = await resp.json(); if (d && d.detail) msg = d.detail; } catch (e) {}
          clearStatus();
          showError(msg);
          return;
        }
        let finished = false;
        let failure = '';
        await readSSE(resp, (ev) => {
          if (ev.type === 'switch_status') {
            setStatus(ev.phase === 'compacting'
              ? 'Compacting conversation…'
              : 'Switching to ' + m.name + '…');
          } else if (ev.type === 'switch_done') {
            finished = true;
          } else if (ev.type === 'error') {
            // The stream has already sent 200 by this point, so a failure can
            // only arrive as an event. Without this the real reason was
            // discarded and every failure read as "please try again".
            failure = ev.message || '';
          }
        });
        if (finished) {
          location.reload();
        } else {
          clearStatus();
          showError(failure || 'Could not switch model. Please try again.');
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
        window.__shiftTheme(theme);
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
    // Dismissing the welcome screen means it has been seen -- by whichever
    // route. It used to be remembered only if you also noticed and ticked a
    // "Don't show this again" box, so pressing Get started welcomed you again
    // on every single launch. Nothing here is lost by moving on: the theme,
    // the text size, the microphone and the read-aloud choice all live in
    // Settings too.
    let welcomeMarked = false;
    function markWelcomeSeen() {
      if (welcomeMarked) return;
      welcomeMarked = true;
      const fd = new FormData();
      fd.append('welcome_seen', 'on');
      fetch('/_settings/prefs', { method: 'POST', body: fd }).catch(() => {});
    }
    welcomeModal.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') markWelcomeSeen();
    });

    if (go) go.addEventListener('click', () => {
      const fd = new FormData();
      fd.append('welcome_seen', 'on');
      welcomeMarked = true;

      // How the app should talk to the user. A screen reader and our own
      // read-aloud are alternatives, never layers: running both means two
      // voices reading the same reply over each other.
      const mode = (welcomeModal.querySelector('input[name="a11y_mode"]:checked') || {}).value;
      if (mode) {
        const usesReader = mode === 'screen_reader';
        fd.append('uses_screen_reader', usesReader ? 'on' : 'off');
        // The reader speaks the replies, so ours must not.
        fd.append('tts_auto', mode === 'read_aloud' ? 'on' : 'off');
        // The working tick exists for people who cannot watch the screen, so
        // it is on by default for exactly them and off for everyone else.
        fd.append('sound_ticks', usesReader ? 'on' : 'off');
        applyA11yMode(mode);
      }

      if ([...fd.keys()].length) {
        fetch('/_settings/prefs', { method: 'POST', body: fd });
      }
      const mic = document.getElementById('welcome-mic');
      if (mic) saveMicDevice(mic.value);
      window.__closeModal();
      // Taken out of the page only once it has faded; removing it here and now
      // would snatch it away mid-ramp, which is the one dialog where the
      // abrupt version is most likely to be someone's first impression.
      setTimeout(() => welcomeModal.remove(), 260);
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
