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
   * welcome dialog, and the assistant's own `set_appearance`. */
  window.__shiftTheme = function (theme) {
    const root = document.documentElement;
    if (!theme || root.dataset.theme === theme) return;
    root.classList.add('theme-shifting');
    root.dataset.theme = theme;
    setTimeout(() => root.classList.remove('theme-shifting'), 1100);
  };

  /* Pull whatever the server now says the page should look and sound like, and
   * apply it where it stands.
   *
   * Called at the end of every turn, because the assistant can be asked to
   * change any of this -- "read your replies to me", "make the writing bigger",
   * "turn that ticking off" -- and a change you have to reload to see is not a
   * change anybody asked for. It is also how a second window catches up.
   *
   * Everything here is idempotent, and each apply checks whether the value
   * actually moved: this runs after every turn, and re-setting the volume on a
   * turn that never touched it would fight the slider under the user's hand. */
  async function refreshSettings() {
    let data;
    try {
      data = await fetch('/api/theme').then((r) => r.json());
    } catch (e) { return; }
    window.__shiftTheme(data.theme);
    window.__applyAccent(data.accent, data.accent_text);
    if (data.zoom && Math.abs(parseFloat(data.zoom) - window.__readZoom()) > 0.001) {
      window.__applyZoom(parseFloat(data.zoom));
    }
    if (typeof applyChatSettings === 'function') applyChatSettings(data);
    // The settings panel is fetched once and kept, so its controls still show
    // whatever was true when it was built. Opening it after asking for light
    // mode showed Dark as the chosen one, which reads as the app having ignored
    // what it just did.
    document.querySelectorAll('.theme-opt').forEach((b) => {
      const mine = b.dataset.theme === data.theme;
      b.classList.toggle('active', mine);
      b.setAttribute('aria-pressed', mine ? 'true' : 'false');
    });
    for (const [id, on] of [['tts_auto', data.tts_auto], ['stt_enabled', data.stt_enabled],
                            ['sound_cues', data.sound_cues], ['sound_ticks', data.sound_ticks]]) {
      const box = document.querySelector('input[type="checkbox"][name="' + id + '"]');
      if (box) box.checked = !!on;
    }
    for (const [id, value] of [['tts_speed', data.tts_speed], ['tts_volume', data.tts_volume],
                               ['sound_volume', data.sound_volume]]) {
      const slider = document.querySelector('input[name="' + id + '"]');
      // Never while it is being dragged: the value under the user's hand wins.
      if (slider && document.activeElement !== slider) slider.value = value;
    }
    const voice = document.querySelector('select[name="tts_voice"]');
    if (voice && data.tts_voice && document.activeElement !== voice) voice.value = data.tts_voice;
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
    // Zooming out makes every px smaller, including the conversation column's
    // own width -- so at 80% the text sat in a thin ribbon with two thirds of
    // the screen given over to empty gutter. This undoes that for the column
    // only: below 100% its width grows by exactly as much as the zoom shrinks
    // it, so it covers the same amount of glass it did at 100%. Above 100% it
    // is left alone, because the gutters closing up as you zoom in is the
    // point.
    document.documentElement.style.setProperty('--zoom', String(z));
    document.documentElement.style.setProperty('--zoom-widen', String(1 / Math.min(z, 1)));
    try { localStorage.setItem('appZoom', String(z)); } catch (e) {}
    window.__showZoom();
    window.__measureLayout();
    return z;
  };
  /* The size the user chose, told to the server as well.
   *
   * localStorage alone is enough to remember it between launches, and that is
   * all this was for. But the assistant can be asked to make the writing
   * bigger, and it has no way to reach a browser's private store -- so the
   * server has to know the number too. The page is rendered with it, so there
   * is no flash and no round trip on the way in; this is only the way out. */
  window.__saveZoom = function (z) {
    const applied = window.__applyZoom(z);
    const body = new FormData();
    body.append('zoom', String(applied));
    fetch('/_settings/prefs', { method: 'POST', body }).catch(() => {});
    return applied;
  };
  // How wide the page actually is, in the units the layout is written in --
  // which is the window divided by the zoom, not the window. The one decision
  // that turns on it today is whether there is gutter beside the conversation
  // for the settings button to live in; see `.wide-gutter`.
  window.__measureLayout = function () {
    const wide = window.innerWidth / window.__readZoom() >= 1180;
    document.documentElement.classList.toggle('wide-gutter', wide);
  };
  window.addEventListener('resize', () => window.__measureLayout());
  // The number next to the buttons. Separate, because the settings panel is
  // fetched long after the zoom has been applied: its markup arrives saying
  // "100%" whatever the real zoom is, and until this was called from the panel
  // as well, nothing corrected it until you pressed one of the buttons.
  window.__showZoom = function () {
    const el = document.getElementById('zoom-value');
    if (el) el.textContent = Math.round(window.__readZoom() * 100) + '%';
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

  /* Where the keyboard goes when a dialog closes.
   *
   * `remembered.focus()` looks like it does this on its own, and for a dialog
   * opened by pressing a button it does. Two cases it silently does nothing at
   * all for, and "nothing" here means focus lands on `<body>`: the next Tab
   * starts again from the top of the document and a screen reader announces
   * none of it.
   *
   *  - A dialog nothing opened. The welcome appears on load, when the active
   *    element is the body, so the body is what gets remembered and refocused.
   *    That is the first thing a new user meets.
   *  - A button that has since been re-rendered away -- a project card, a
   *    message action. The node is remembered, detached, and unfocusable.
   *
   * Both end the same way, so both are checked the same way: try, then look at
   * where focus actually landed rather than trusting the call.
   */
  function restoreFocus(remembered) {
    if (remembered && remembered.focus && remembered !== document.body
        && document.contains(remembered)) {
      remembered.focus();
      if (document.activeElement === remembered) return;
    }
    const fallback = document.getElementById('chat-textarea')
                  || document.getElementById('main-content');
    if (fallback && fallback.focus) fallback.focus();
  }

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
    restoreFocus(ret);
    // So a dialog can clean up after itself however it was dismissed. The
    // camera needs this: Escape closes the dialog, and without a signal the
    // webcam would stay live -- and its light stay on -- until the tab closed.
    el.dispatchEvent(new CustomEvent('modalclosed'));
    return true;
  };

  /* The accent colour, applied to a page that is already open.
   *
   * Here rather than in the settings script for the same reason as the password
   * box below: the assistant can be asked to make the app blue from the chat,
   * where that script has never run. The server renders these same properties
   * inline on `<html>` for a fresh page; this is the live path.
   *
   * `textHex` is what to write ON the accent -- the server works it out, because
   * getting the contrast wrong makes the user's own messages unreadable and
   * there is no reason for two implementations of it to drift. */
  window.__applyAccent = function (hex, textHex) {
    const root = document.documentElement;
    const vars = ['--accent', '--accent-btn', '--user-bubble', '--focus',
                  '--accent-dim', '--user-bubble-text'];
    if (!hex) { vars.forEach((v) => root.style.removeProperty(v)); return; }
    root.style.setProperty('--accent', hex);
    root.style.setProperty('--accent-btn', hex);
    root.style.setProperty('--user-bubble', hex);
    root.style.setProperty('--focus', hex);
    root.style.setProperty('--accent-dim', 'color-mix(in srgb, ' + hex + ' 18%, transparent)');
    if (textHex) root.style.setProperty('--user-bubble-text', textHex);
  };

  /* ── The parent password box ────────────────────────────────────────────
   *
   * Lives here rather than in the settings panel's own script, because there
   * are now two places that ask for it: the buttons in Settings, and the
   * assistant when it is asked to turn child mode on or off. The panel's
   * markup is fetched only when the panel is opened, so a copy in there was
   * unreachable from the chat.
   *
   * `wantsConfirm` marks the prompts that *set* a password rather than check
   * one; those ask for it twice. A password box hides its own typos, and a
   * mistake here locks the parent out of their own settings for a day.
   */
  window.__passwordPrompt = function (titleText, bodyText, wantsConfirm, onConfirm) {
    const modal = document.getElementById('password-modal');
    if (!modal) return;
    const q = (id) => document.getElementById(id);
    const input = q('password-input');
    const twice = q('password-confirm-input');
    const err = q('password-modal-error');
    q('password-modal-title').textContent = titleText;
    q('password-modal-text').textContent = bodyText;
    q('password-modal-note').hidden = !wantsConfirm;
    err.hidden = true;
    err.textContent = '';
    input.value = '';
    twice.value = '';
    twice.hidden = !wantsConfirm;

    const confirmBtn = q('password-confirm');
    const cancelBtn = q('password-cancel');

    function finish() {
      confirmBtn.removeEventListener('click', submit);
      cancelBtn.removeEventListener('click', finish);
      input.removeEventListener('keydown', onKey);
      twice.removeEventListener('keydown', onKey);
      window.__closeModal();
    }
    async function submit() {
      const password = input.value.trim();
      if (!password) { fail('Please type a password.'); return; }
      if (!twice.hidden && twice.value.trim() !== password) {
        fail('The two passwords are not the same. Try again.');
        twice.value = '';
        twice.focus();
        return;
      }
      const result = await onConfirm(password);
      if (result && result.ok) {
        finish();
        if (result.reload) location.reload();
      } else if (result && result.reason === 'no_key') {
        fail('Set up an AI first: once child mode is on, API keys are locked.');
      } else if (result && result.reason === 'password') {
        fail('That password is not right.');
      } else if (result) {
        fail('That did not work. Please try again.');
      }
    }
    function fail(message) { err.textContent = message; err.hidden = false; }
    // Enter submits, from either box. Typing a password and pressing Enter is
    // what everybody does, and it did nothing at all here.
    function onKey(e) { if (e.key === 'Enter') { e.preventDefault(); submit(); } }

    confirmBtn.addEventListener('click', submit);
    cancelBtn.addEventListener('click', finish);
    input.addEventListener('keydown', onKey);
    twice.addEventListener('keydown', onKey);
    modal.addEventListener('modalclosed', finish, { once: true });
    window.__openModal(modal, input);
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
  /* Put a project made just now into the Projects menu.
   *
   * The menu is rendered with the page, so a project the assistant created a
   * moment ago was not in it until the next navigation -- while the reply on
   * screen said "it is in your list". It was not, and the one thing the user
   * cannot do is go and look on disk.
   *
   * Added rather than rebuilt: the menu carries unread and working marks that
   * would flicker if it were thrown away and redrawn every two and a half
   * seconds. The list is short and the order is by name, as the server sends
   * it, so a new one simply goes where it belongs.
   */
  function addNewProjects(data) {
    const menu = document.getElementById('sessions-menu');
    if (!menu) return;
    const have = new Set(
      [...menu.querySelectorAll('[data-session-id]')].map((b) => b.dataset.sessionId));
    // Gone ones go too. The menu only ever grew, so a project removed
    // anywhere -- here, in Settings, or through the assistant -- stayed on this
    // list until the page was reloaded, and pressing it led to a dead session.
    const alive = new Set(data.map((s) => s.id));
    menu.querySelectorAll('[data-session-id]').forEach((b) => {
      if (!alive.has(b.dataset.sessionId)) b.remove();
    });
    const fresh = data.filter((s) => !have.has(s.id));
    if (!fresh.length) return;

    const empty = menu.querySelector('.sessions-empty');
    if (empty) empty.remove();
    const separator = menu.querySelector('.sessions-sep');
    for (const s of fresh) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.nav = '/sessions/' + s.id;
      button.dataset.sessionId = s.id;
      const dot = document.createElement('span');
      dot.className = 'session-dot';
      dot.setAttribute('aria-hidden', 'true');
      dot.hidden = true;
      const label = document.createElement('span');
      label.className = 'session-label';
      label.textContent = s.name;
      const status = document.createElement('span');
      status.className = 'sr-only session-status';
      button.appendChild(dot);
      button.appendChild(label);
      button.appendChild(status);
      // Above the rule, with the projects; below it is "New empty project".
      menu.insertBefore(button, separator);
    }
  }

  async function refreshActivity() {
    let data;
    try { data = await fetch('/api/sessions/status').then((r) => r.json()); } catch (e) { return; }
    const seen = lastSeen();
    const byId = {};
    data.forEach((s) => { byId[s.id] = s; });
    addNewProjects(data);

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
    /* A turn that started without this page asking for one.
     *
     * That happens when a command the assistant handed over finishes: the job
     * wakes the session, the assistant reads the output and replies. Nothing
     * on this page requested it, so nothing was listening -- the reply landed
     * in the database and the user, who had been told "I'll tell you when it's
     * done", was told nothing until they reloaded.
     *
     * The poller already knows which sessions are working. If this one is and
     * we are not watching, start watching. */
    const mine = data.find((s) => s.id === currentSessionId);
    if (mine && mine.running && !running && typeof reattach === 'function') {
      reattach();
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
  // family -> pitch, filled in by the chat page from the table the server
  // sends. Kept out here because the ticking lives out here, and the settings
  // page never reaches the code that parses it.
  let toolNotes = {};
  let audioCtx = null;
  let tickTimer = 0;

  /* Voice always has the floor.
   *
   * Two reasons, and both matter. A tick under a spoken reply is a tick over
   * the words somebody is relying on to know what happened -- and for the one
   * user this ticking exists for, those words are the whole interface. And
   * while dictation is running the microphone is open, so a tick is not merely
   * heard, it is recorded and handed to a transcriber as if it were speech.
   *
   * Held rather than stopped: the ticking picks up where it was as soon as the
   * voice is done, because the work it is reporting on has not paused.
   */
  let voiceHolds = 0;

  function holdSoundsForVoice() {
    voiceHolds += 1;
    clearTimeout(tickTimer);
    tickTimer = 0;
  }

  function releaseSoundsForVoice() {
    voiceHolds = Math.max(0, voiceHolds - 1);
    // Only if there is still work to report on. `ticking` rather than the
    // turn's own flag, which is declared below the early return this file makes
    // on any page that is not a chat.
    if (!voiceHolds && ticking) startTicks();
  }

  // Whether a turn asked for ticking. Separate from whether a tick is scheduled
  // right now, which voice takes away and gives back.
  let ticking = false;

  function voiceHasTheFloor() { return voiceHolds > 0; }

  function ctx() {
    if (voiceHasTheFloor()) return null;
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
  function cueTick(freq) { blip(freq || 520, 0, 0.05, 0.05); }

  /* One key press.
   *
   * A short burst of filtered noise rather than a tone, because that is what a
   * key sounds like and a tone at this speed sounds like an alarm. The pitch
   * wanders a little each time -- identical clicks in a row read as a machine
   * fault rather than as somebody typing.
   */
  function clack(at, gain) {
    const ac = ctx();
    if (!ac) return;
    const t0 = ac.currentTime + at;
    const len = Math.max(1, Math.floor(ac.sampleRate * 0.011));
    const buffer = ac.createBuffer(1, len, ac.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < len; i++) {
      // Decaying noise: all the energy at the start, like a struck thing.
      data[i] = (Math.random() * 2 - 1) * (1 - i / len) ** 2;
    }
    const source = ac.createBufferSource();
    source.buffer = buffer;
    const band = ac.createBiquadFilter();
    band.type = 'bandpass';
    band.frequency.setValueAtTime(1500 + Math.random() * 1100, t0);
    band.Q.setValueAtTime(1.1, t0);
    const amp = ac.createGain();
    amp.gain.setValueAtTime(Math.max(0.0001, gain * soundVolume), t0);
    source.connect(band).connect(amp).connect(ac.destination);
    source.start(t0);
  }

  // A few keys, unevenly spaced. Evenly spaced clicks sound like a clock.
  function cueTyping() {
    const n = 1 + Math.floor(Math.random() * 3);
    for (let i = 0; i < n; i++) clack(i * (0.055 + Math.random() * 0.07), 0.16);
  }

  // Done thinking. Two quick rising notes, much lighter than the end-of-turn
  // pair -- it marks a phase, not a finish.
  function cueThoughtDone() { blip(700, 0, 0.07, 0.10); blip(1050, 0.06, 0.11, 0.10); }

  /* What the assistant is doing right now, for the ear.
   *
   * It used to be one tick at one pitch for everything, which says "still
   * alive" and nothing else. Now the sound says which kind of work: each tool
   * family has its own pitch (the same one its finished-chip plays, so the two
   * agree), thinking is a low pulse, and writing a reply sounds like somebody
   * typing -- which is exactly what is happening, and the one phase where
   * something new is arriving every moment.
   */
  let workPhase = '';

  function setWorkPhase(phase) {
    if (phase === workPhase) return;
    workPhase = phase;
    // The cadence changes with the phase. Not while voice has the floor: it
    // will start again at the new phase's pace when the voice is done.
    if (ticking && !voiceHasTheFloor()) startTicks();
  }

  function phaseTick() {
    if (workPhase === 'writing') { cueTyping(); return; }
    if (workPhase === 'thinking') { cueTick(300); return; }
    cueTick(toolNotes[workPhase] || 520);
  }

  /* How soon, and how often, per phase.
   *
   * Writing starts almost at once: for somebody who cannot see the screen this
   * is not a "still working" nag, it is the reply arriving, and it should be
   * heard from the first word. Thinking waits a couple of seconds -- a quick
   * thought needs no announcing, a long one is exactly the silence people ask
   * about. Everything else keeps the long wait it always had, so an ordinary
   * turn full of quick tools stays silent.
   */
  const TICK = {
    writing: { after: 260, every: 480 },
    thinking: { after: 2_000, every: 3_000 },
  };

  function tickPace() {
    return TICK[workPhase] || { after: SOUND.tickAfterMs, every: SOUND.tickEveryMs };
  }

  function startTicks() {
    ticking = true;
    clearTimeout(tickTimer);
    tickTimer = 0;
    if (!soundTicks || voiceHasTheFloor()) return;
    tickTimer = setTimeout(function repeat() {
      phaseTick();
      tickTimer = setTimeout(repeat, tickPace().every);
    }, tickPace().after);
  }

  function stopTicks() {
    ticking = false;
    clearTimeout(tickTimer);
    tickTimer = 0;
    workPhase = '';
  }

  window.__previewSounds = function (volume) {
    if (typeof volume === 'number') soundVolume = volume;
    cueDone();
    setTimeout(cueError, 900);
  };

  // ── Making a project by hand ──────────────────────────────────────────────
  //
  // The other way round from how this app is meant to work: instead of saying
  // what you want and having it set up, you name a folder. For somebody who
  // already knows what they are doing, or who has a folder of code they want
  // this pointed at. Kept at the bottom of the menu and called "empty" so it
  // does not read as the obvious route to somebody who does not know yet.

  (function () {
    const openBtn = document.getElementById('new-project-btn');
    const modal = document.getElementById('new-project-modal');
    if (!openBtn || !modal) return;

    const nameEl = document.getElementById('new-project-name');
    const folderRow = document.getElementById('new-project-folder-row');
    const folderEl = document.getElementById('new-project-folder');
    const errorEl = document.getElementById('new-project-error');
    const createBtn = document.getElementById('new-project-create');
    const cancelBtn = document.getElementById('new-project-cancel');
    const ownEl = document.getElementById('new-project-own-folder');
    const browseBtn = document.getElementById('new-project-browse');
    const missingBox = document.getElementById('new-project-missing');
    const missingPath = document.getElementById('new-project-missing-path');
    const makeBtn = document.getElementById('new-project-make');
    const backBtn = document.getElementById('new-project-back');
    const actionsBox = document.getElementById('new-project-actions');
    let returnTo = null;
    let pickerChecked = false;

    function showError(text) {
      errorEl.textContent = text || '';
      errorEl.hidden = !text;
    }

    function refreshWhere() {
      if (!folderRow) return;
      folderRow.hidden = !(ownEl && ownEl.checked);
    }

    // Whether this desktop has a folder chooser the server can open. Asked once,
    // the first time the dialog is opened rather than on every page load: most
    // people never open this at all.
    async function checkPicker() {
      if (pickerChecked || !browseBtn) return;
      pickerChecked = true;
      try {
        const resp = await fetch('/api/files/folder-picker');
        const data = await resp.json();
        browseBtn.hidden = !(data && data.available);
      } catch (e) { /* stays hidden; typing the path still works */ }
    }

    async function browse() {
      browseBtn.disabled = true;
      const said = browseBtn.textContent;
      browseBtn.textContent = 'Choosing…';
      try {
        const resp = await fetch('/api/files/folder-picker', { method: 'POST' });
        const data = await resp.json().catch(() => null);
        if (!resp.ok) {
          showError((data && data.detail) || 'The folder chooser would not open.');
          return;
        }
        // An empty path means they closed it without choosing, which is not
        // something to say anything about.
        if (data && data.path) {
          folderEl.value = data.path;
          showError('');
          announce('Folder chosen: ' + data.path);
        }
      } catch (e) {
        showError('The folder chooser would not open.');
      } finally {
        browseBtn.disabled = false;
        browseBtn.textContent = said;
        folderEl.focus();
      }
    }

    function open() {
      returnTo = document.activeElement;
      showError('');
      nameEl.value = '';
      if (folderEl) folderEl.value = '';
      if (ownEl) ownEl.checked = false;
      stopAsking();
      refreshWhere();
      checkPicker();
      modal.hidden = false;
      modal.classList.add('shown');
      nameEl.focus();
    }

    function close() {
      modal.hidden = true;
      modal.classList.remove('shown');
      if (returnTo && returnTo.isConnected) returnTo.focus();
    }

    // Asking about a folder that is not there, rather than making it. The
    // normal buttons step aside while the question is on screen so there is
    // only ever one thing to answer.
    function askAboutFolder(path) {
      missingPath.textContent = path;
      missingBox.hidden = false;
      actionsBox.hidden = true;
      makeBtn.focus();
      announce('There is no folder called ' + path + ' yet. Make that folder, or change it.');
    }

    function stopAsking() {
      missingBox.hidden = true;
      actionsBox.hidden = false;
    }

    async function create(makeFolder) {
      const name = nameEl.value.trim();
      if (!name) { showError('Give it a name first.'); nameEl.focus(); return; }
      const own = !!(ownEl && ownEl.checked);
      const folder = own && folderEl ? folderEl.value.trim() : '';
      if (own && !folder) {
        showError('Say which folder, or untick the box.');
        folderEl.focus();
        return;
      }
      createBtn.disabled = true;
      makeBtn.disabled = true;
      showError('');
      try {
        const resp = await fetch('/api/sessions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, folder, make_folder: !!makeFolder }),
        });
        const data = await resp.json().catch(() => null);
        // 409 means the folder is not there. Not an error -- a question.
        if (resp.status === 409) { askAboutFolder(folder); return; }
        if (!resp.ok) {
          stopAsking();
          showError((data && data.detail) || 'That did not work.');
          return;
        }
        window.location.href = '/sessions/' + data.id;
      } catch (e) {
        stopAsking();
        showError('That did not work. Please try again.');
      } finally {
        createBtn.disabled = false;
        makeBtn.disabled = false;
      }
    }

    openBtn.addEventListener('click', open);
    cancelBtn.addEventListener('click', close);
    createBtn.addEventListener('click', () => create(false));
    makeBtn.addEventListener('click', () => create(true));
    backBtn.addEventListener('click', () => {
      stopAsking();
      if (folderEl) { folderEl.focus(); folderEl.select(); }
    });
    if (ownEl) {
      ownEl.addEventListener('change', () => {
        // A different folder is a different question, so an unanswered one goes.
        stopAsking();
        refreshWhere();
        // Ticking it is asking to name a folder, so put the cursor where the
        // folder goes rather than leaving it on the box that was just ticked.
        if (ownEl.checked && folderEl) folderEl.focus();
      });
    }
    if (browseBtn) browseBtn.addEventListener('click', browse);
    modal.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); close(); }
      // Enter submits, except on the two buttons that are not "create it" --
      // pressing Enter on Choose used to make the project instead of opening
      // the chooser, which is the one moment it must not.
      if (e.key === 'Enter' && e.target !== createBtn && e.target !== browseBtn
          && e.target !== cancelBtn && e.target !== makeBtn && e.target !== backBtn) {
        e.preventDefault();
        create(false);
      }
    });

    // Typing a new path answers the question by replacing it.
    if (folderEl) folderEl.addEventListener('input', stopAsking);

    // Dropping a folder fills the path in. The same gesture as attaching one to
    // a message, which is the only way most people here would think to say
    // where something is.
    if (folderEl) {
      folderEl.addEventListener('dragover', (e) => { e.preventDefault(); folderEl.classList.add('drop-target'); });
      folderEl.addEventListener('dragleave', () => folderEl.classList.remove('drop-target'));
      folderEl.addEventListener('drop', (e) => {
        e.preventDefault();
        folderEl.classList.remove('drop-target');
        const uri = (e.dataTransfer.getData('text/uri-list') || e.dataTransfer.getData('text/plain') || '')
          .split('\n')[0].trim();
        const path = fileUriToPath(uri) || (uri.startsWith('/') ? uri : '');
        if (path) folderEl.value = path.replace(/\/+$/, '');
      });
    }
  })();

  // ── Settings, over the conversation ───────────────────────────────────────
  //
  // Settings used to be somewhere you went, which ended whatever you were in
  // the middle of: the draft in the composer, the scroll position, the reply
  // being read aloud. It is now a panel over the chat, and the chat keeps
  // working while it is open -- which is the whole point, so nothing here traps
  // focus or steals the keyboard.

  (function () {
    // Two triggers, one on the bar and one in the corner, of which only one is
    // ever visible -- which is which is a question for the stylesheet, not for
    // this.
    const triggers = [
      document.getElementById('settings-btn'),
      document.getElementById('settings-fab'),
    ].filter(Boolean);
    const openBtn = triggers[0];
    const panel = document.getElementById('settings-panel');
    const body = document.getElementById('settings-panel-body');
    const closeBtn = document.getElementById('settings-close');
    if (!triggers.length || !panel || !body) return;

    function visibleTrigger() {
      // `offsetParent` is null for a `position: fixed` element whether it is
      // shown or not, so it cannot answer this -- and the corner button is
      // fixed. `getClientRects()` is empty only when the element really is not
      // being rendered.
      return triggers.find((t) => t.getClientRects().length > 0) || openBtn;
    }

    let loaded = false;
    let loading = false;

    function isOpen() { return !panel.hidden; }

    async function open() {
      if (isOpen() || loading) return;
      if (!loaded) {
        loading = true;
        visibleTrigger().setAttribute('aria-busy', 'true');
        try {
          const resp = await fetch('/settings/body', { headers: { 'X-Panel': '1' } });
          if (!resp.ok) throw new Error('settings body ' + resp.status);
          body.innerHTML = await resp.text();
          loaded = true;
          // Once only: several of the handlers in there are delegated on
          // `document`, so initialising twice would double every click.
          if (window.__initSettings) window.__initSettings();
        } catch (e) {
          // The page still exists and still works. Falling back to it is a far
          // better outcome than a user who cannot reach their API key.
          window.location.href = '/settings';
          return;
        } finally {
          loading = false;
          triggers.forEach((t) => t.removeAttribute('aria-busy'));
        }
      }
      panel.hidden = false;
      document.body.classList.add('settings-open');
      // After the layout has settled, not before: the panel takes room from the
      // chat and the composer moves, and the two floating buttons are placed
      // against where the composer is.
      requestAnimationFrame(placeFloatingButtons);
      triggers.forEach((t) => t.setAttribute('aria-expanded', 'true'));
      // Into the panel, so a keyboard or screen reader user lands on what just
      // appeared rather than being left wherever they were.
      requestAnimationFrame(() => {
        const first = panel.querySelector('h2, [tabindex], button, input, select');
        if (first) { first.setAttribute('tabindex', first.tabIndex < 0 ? '-1' : first.tabIndex); first.focus(); }
      });
      announce('Settings opened. The chat is still here behind it.');
    }

    function close(returnFocus) {
      if (!isOpen()) return;
      panel.hidden = true;
      document.body.classList.remove('settings-open');
      requestAnimationFrame(placeFloatingButtons);
      triggers.forEach((t) => t.setAttribute('aria-expanded', 'false'));
      if (returnFocus !== false) visibleTrigger().focus();
      announce('Settings closed.');
    }

    triggers.forEach((t) => t.addEventListener('click', () => (isOpen() ? close() : open())));
    if (closeBtn) closeBtn.addEventListener('click', () => close());
    // So the child-mode marker, and the assistant's own answers, can send
    // somebody to the right place rather than describing where it is.
    window.__openSettings = open;

    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape' || !isOpen()) return;
      // Let a modal inside the panel have the key first -- Escape should shut
      // the rename box, not the whole panel out from under it.
      if (panel.querySelector('.modal-backdrop:not([hidden])')) return;
      close();
    });

    // Clicking the conversation is a clear enough "I am done here".
    document.addEventListener('pointerdown', (e) => {
      if (!isOpen() || panel.contains(e.target)) return;
      if (triggers.some((t) => t.contains(e.target))) return;
      if (e.target.closest('#chat-form, #messages')) close(false);
    });
  })();

  /* ── Child mode: the marker, and the one way to change it ────────────────
   *
   * Both directions need the parent's password -- one to set it, the other to
   * prove who is asking -- and it is only ever typed into the box. The
   * assistant can raise the question (`set_child_mode`), and the settings panel
   * has buttons for it, but this is the only code that does it, so there is one
   * place where the rule lives. */
  (function () {
    const badge = document.getElementById('child-badge');
    if (badge) {
      badge.addEventListener('click', () => {
        if (window.__openSettings) window.__openSettings();
      });
    }

    window.__markChildMode = function (on) {
      if (badge) badge.hidden = !on;
      document.body.classList.toggle('child-mode', !!on);
    };

    window.__askChildMode = function (on) {
      if (!window.__passwordPrompt) return;
      const post = (url, body) => fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then((r) => r.json()).catch(() => ({ ok: false, reason: 'network' }));

      // Turning it on for the first time *sets* the password, so it is asked
      // for twice; switching off only checks one that already exists. The
      // server knows which, and says so on the page.
      const setting = on && !document.body.classList.contains('has-parent-password');
      if (on) {
        window.__passwordPrompt(
          'Turn on child mode',
          setting
            ? 'Choose a parent password. You will need it to change the AI or turn child mode off again.'
            : 'Enter the parent password you set before.',
          setting,
          async (password) => {
            const r = await post('/api/child/enable', { password });
            return r.ok ? { ok: true, reload: true } : { ok: false, reason: r.reason };
          },
        );
      } else {
        window.__passwordPrompt(
          'Turn off child mode', 'Enter the parent password.', false,
          async (password) => {
            const r = await post('/api/child/disable', { password });
            return r.ok ? { ok: true, reload: true } : { ok: false, reason: r.reason };
          },
        );
      }
    };

  })();

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
    // Every message goes above the "what I am doing" row, never below it.
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

  /* How to put money on the account, drawn by the app rather than said by the
   * assistant.
   *
   * The assistant says it too, and that copy matters -- somebody being read to
   * needs the words. But the address has to be exactly right, and a retold
   * address is a guessed address: console.cloud.google.com is a real page, a
   * plausible one, and not where the button is. A parent who loses an evening
   * to the wrong page does not conclude that the model misremembered. They
   * conclude the whole thing is beyond them, and that is the end of it.
   *
   * So the link here is a link. Nobody has to type it, which also makes this
   * reachable for somebody working entirely by voice. */
  function appendFunds(action) {
    const wrap = document.createElement('div');
    wrap.className = 'message fund';
    const card = document.createElement('div');
    card.className = 'fund-card';

    const who = action.name || 'this';
    const title = document.createElement('h3');
    title.className = 'fund-title';
    /* Whose job this is, said to whoever is actually holding the keyboard. A
     * child cannot fix this and should not be left feeling they broke it. */
    title.textContent = document.body.classList.contains('child-mode')
      ? 'Ask a grown-up to put money on the ' + who + ' account'
      : 'Making pictures needs money on the ' + who + ' account';
    card.appendChild(title);

    const why = document.createElement('p');
    why.className = 'fund-why';
    why.textContent = 'Nothing is broken. Pictures are paid for separately '
      + 'from everything else, so the rest of the app carries on working.';
    card.appendChild(why);

    if (action.url) {
      const go = document.createElement('p');
      go.className = 'fund-go';
      go.appendChild(link(action.url, 'Open ' + (action.url_label || action.url)
        + '  →'));
      card.appendChild(go);
    }

    const steps = document.createElement('ol');
    steps.className = 'fund-steps';
    (action.steps || []).forEach(function (parts) {
      const li = document.createElement('li');
      (parts || []).forEach(function (part) {
        li.appendChild(part.href ? link(part.href, part.text)
          : document.createTextNode(part.text || ''));
      });
      steps.appendChild(li);
    });
    card.appendChild(steps);

    if (action.count) {
      const count = document.createElement('p');
      count.className = 'fund-count';
      count.textContent = action.count;
      card.appendChild(count);
    }

    wrap.appendChild(card);
    messages.appendChild(wrap);
    scrollToBottom();
    announce(title.textContent + '. The steps are on the screen.');

    function link(href, text) {
      const a = document.createElement('a');
      a.href = href;
      // A new tab, because the app is the page underneath and navigating away
      // from a conversation to reach a billing page loses the conversation.
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = text;
      return a;
    }
  }

  /* The AI changed, mid-conversation, because a queued switch came due. Said
   * in the transcript rather than as a passing status line: it is a fact about
   * the conversation from here on, and somebody scrolling back tomorrow to
   * work out why the replies changed character should find it. */
  function announceSwitch(name) {
    const wrap = document.createElement('div');
    wrap.className = 'message switched';
    const note = document.createElement('div');
    note.className = 'switched-note';
    note.textContent = 'Now using ' + name + '.';
    wrap.appendChild(note);
    messages.appendChild(wrap);
    const label = document.querySelector('#model-btn .model-label');
    if (label) label.textContent = name;
    announce('Now using ' + name + '.');
    scrollToBottom();
  }

  /* "May I open this file, which is not in your project?"
   *
   * The only thing in this app that stops and waits for a person. Everything
   * inside a project happens without asking, which is the whole design -- so
   * when this does appear it means something genuinely outside that, and it is
   * worth reading. That is also why it names the file rather than describing
   * the category of thing it is. */
  let permissionAsked = '';
  function askPermission(request) {
    const modal = document.getElementById('permission-modal');
    if (!modal || !request || !request.id) return;
    if (permissionAsked === request.id && !modal.hidden) return;
    permissionAsked = request.id;

    document.getElementById('permission-verb').textContent = request.verb || 'open';
    document.getElementById('permission-path').textContent = request.path || '';
    document.getElementById('permission-folder-why').textContent =
      'Anything in ' + (request.folder || 'that folder') + ' from now on, without '
      + 'asking again. Best if it is going to need several files from there — '
      + 'being asked twenty times is how people stop reading the question.';

    const send = async (answer, password) => {
      window.__closeModal();
      permissionAsked = '';
      try {
        const resp = await fetch('/api/sessions/' + sessionId + '/permission', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: request.id, answer,
                                 parent_password: password || '' }),
        });
        if (!resp.ok) throw new Error('refused');
      } catch (e) {
        showError('That answer did not reach the app. It has carried on without '
          + 'opening the file.');
      }
    };

    /* In child mode, yes is the grown-up's to give. The dialog says so on the
     * buttons before either is pressed -- a child who presses "yes" and only
     * then meets a password box has been told no in the most annoying way
     * available. Saying no costs nothing and never asks: stopping something
     * has to be the cheap answer, or a child has effectively been told they
     * may not refuse. */
    const locked = !!request.locked;
    document.querySelectorAll('#permission-modal .permission-lock')
      .forEach((el) => { el.hidden = !locked; });

    const allow = (answer) => {
      if (!locked) { send(answer); return; }
      window.__closeModal();
      if (!window.__passwordPrompt) return;
      window.__passwordPrompt('A grown-up, please',
        'Opening a file outside this project needs the parent password.',
        false, async (password) => {
          const check = await fetch('/api/child/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password }),
          }).then((r) => r.json()).catch(() => ({ ok: false }));
          if (!check.ok) return { ok: false, reason: 'password' };
          await send(answer, password);
          return { ok: true };
        });
    };
    document.getElementById('permission-once').onclick = () => allow('once');
    document.getElementById('permission-always').onclick = () => allow('always');
    document.getElementById('permission-no').onclick = () => send('no');

    /* Focus lands on "just this once" rather than on the recommendation. The
     * safest of the three should be the one Enter reaches, even where it is
     * not the one we suggest. */
    window.__openModal(modal, document.getElementById('permission-once'));
    announce('The AI is asking to open a file outside your project: '
      + (request.name || request.path || ''));
  }

  /* A page that was opened, or reloaded, while a question was on the screen.
   * The turn is still sitting there waiting, so the question has to come back
   * -- losing it would leave the app frozen with nothing to explain why. */
  async function recoverPermission() {
    try {
      const pending = await fetch('/api/sessions/' + sessionId + '/permission')
        .then((r) => (r.ok ? r.json() : null));
      if (pending && pending.id) askPermission(pending);
    } catch (e) {}
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

  /* ── What the assistant is doing, right now ───────────────────────────────
   *
   * There were two of these, which is one too many. A big card at the bottom
   * said "Thinking…" while, directly above it, the thinking block said the same
   * thing -- and the card looked like something you could open and was not. So
   * the card is gone, and the one live thing is a chip on the end of the
   * activity strip saying what is running: the same place the finished work
   * appears, in the same shape, so there is one thing to look at rather than
   * two saying the same thing differently.
   *
   * Thinking has its own row (see below) and writing needs nothing -- the words
   * are appearing on screen, which is a better indicator than any label.
   */
  let liveChip = null;
  let liveSpin = 0;

  function setLiveWork(text) {
    const strip = activityStrip();
    if (!liveChip) {
      liveChip = document.createElement('span');
      liveChip.className = 'chip chip-live';
      // Announced politely, and only when the words change -- the guard below
      // is what stops a screen reader narrating every file that is opened.
      liveChip.setAttribute('role', 'status');
      liveChip.setAttribute('aria-live', 'polite');
      const glyph = document.createElement('span');
      glyph.className = 'chip-glyph';
      glyph.setAttribute('aria-hidden', 'true');
      const words = document.createElement('span');
      words.className = 'chip-text';
      liveChip.appendChild(glyph);
      liveChip.appendChild(words);
      strip.el.appendChild(liveChip);
      liveSpin = startSpinner(glyph);
      scrollToBottom();
    }
    strip.el.appendChild(liveChip);   // always the last chip
    const words = liveChip.querySelector('.chip-text');
    if (words.textContent === text) return;
    words.textContent = text;
    scrollToBottom();
  }

  /* A rule across the conversation where the user pressed Stop.
   *
   * Stopping left no trace at all, so a cut-short turn looked exactly like a
   * finished one -- and the next thing anybody did was wait for a reply that
   * was never coming. A rule rather than a message, because it did not happen
   * in the conversation; it happened to it. The server writes the same mark
   * against the last row, so it is still there tomorrow.
   */
  function markBrokeOff() {
    if (!messages) return;
    const rule = document.createElement('div');
    rule.className = 'broke-off';
    rule.setAttribute('role', 'separator');
    rule.textContent = 'You stopped it here';
    messages.appendChild(rule);
    announce('Stopped. Say what you would like it to do next.');
    scrollToBottom();
  }

  function clearLiveWork() {
    liveSpin = stopSpinner(liveSpin);
    if (liveChip && liveChip.parentNode) liveChip.remove();
    liveChip = null;
  }

  /* ── Thinking, while it happens ───────────────────────────────────────────
   *
   * The reasoning was streaming past unseen: the composer said "Thinking…" and
   * that was all, and the block itself only appeared if you reloaded the
   * conversation afterwards. So the live view and the reloaded one disagreed
   * about whether the assistant had thought at all.
   *
   * Now it is the same block in both, and you watch it happen: a turning mark
   * while the thought is running, and the moment it ends the mark becomes a
   * tick and the row settles into the record. That transition is the point --
   * without it there is no way to tell a thought still going from one that
   * finished, which for somebody working by ear is the difference between
   * waiting and being finished with.
   */
  let thinkingRow = null;
  let thinkingMark = null;
  let thinkingWords = null;
  let thinkingSpin = 0;

  function noteThinking(text) {
    if (!messages) return;
    if (!thinkingRow) {
      thinkingRow = document.createElement('details');
      thinkingRow.className = 'thinking thinking-live';
      const summary = document.createElement('summary');
      thinkingMark = document.createElement('span');
      thinkingMark.className = 'thinking-mark';
      thinkingMark.setAttribute('aria-hidden', 'true');
      const label = document.createElement('span');
      label.className = 'thinking-label';
      label.textContent = 'Thinking…';
      summary.appendChild(thinkingMark);
      summary.appendChild(label);
      thinkingWords = document.createElement('div');
      thinkingWords.className = 'thinking-text';
      thinkingRow.appendChild(summary);
      thinkingRow.appendChild(thinkingWords);
      messages.appendChild(thinkingRow);
      thinkingSpin = startSpinner(thinkingMark);
      scrollToBottom();
    }
    if (text) thinkingWords.textContent += text;
  }

  function finishThinking() {
    if (!thinkingRow) return;
    thinkingSpin = stopSpinner(thinkingSpin);
    thinkingRow.classList.remove('thinking-live');
    thinkingMark.textContent = '✓';
    const label = thinkingRow.querySelector('.thinking-label');
    if (label) label.textContent = 'Thinking';
    // Nothing was said, so there is nothing to keep. Some models stream a
    // `reasoning` event with no text in it at all.
    if (!thinkingWords.textContent.trim()) thinkingRow.remove();
    else if (soundTicks) cueThoughtDone();
    thinkingRow = null;
    thinkingMark = null;
    thinkingWords = null;
  }

  let statusText = null;
  let statusGlyph = null;
  let statusSpin = 0;

  // The composer's own line, for things the composer is doing -- attaching a
  // file, or failing to. What the *assistant* is doing goes in the
  // conversation; see setLiveWork above.
  function setStatus(text) {
    // The status bar is a live region and `content` events arrive per token,
    // so this is called with "Writing a reply..." hundreds of times a turn.
    // Rewriting the node with the same string can make a screen reader
    // re-announce it, so an unchanged status is left strictly alone.
    if (!statusText) {
      statusBar.textContent = '';
      statusGlyph = document.createElement('span');
      // Decoration. The words beside it say the same thing, and a screen
      // reader reading a cycling character ten times a second is a torment.
      statusGlyph.className = 'status-glyph';
      statusGlyph.setAttribute('aria-hidden', 'true');
      statusText = document.createElement('span');
      statusText.className = 'status-text';
      statusBar.appendChild(statusGlyph);
      statusBar.appendChild(statusText);
    }
    if (statusText.textContent === text && !statusBar.hidden) return;
    statusText.textContent = text;
    if (statusBar.hidden) {
      statusBar.hidden = false;
      statusSpin = startSpinner(statusGlyph);
    }
  }

  function clearStatus() {
    statusSpin = stopSpinner(statusSpin);
    statusBar.hidden = true;
    if (statusText) statusText.textContent = '';
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
    browser: 'Checking the website',
    capture: 'Looking at the screen',
    create_project: 'Creating project',
    open_project: 'Opening project',
    rename_project: 'Renaming project',
    delete_projects: 'Getting those projects together',
    list_projects: 'Listing projects',
    assign_project: 'Moving project',
    preview: 'Opening your project',
    game: 'Building the game',
    show_settings: 'Checking the settings',
    set_appearance: 'Changing how it looks',
    set_voice: 'Changing the voice',
    set_sounds: 'Changing the sounds',
    set_child_mode: 'Child mode',
    set_model: 'Choosing the AI',
    set_dictation_quality: 'Adjusting dictation',
    draw: 'Drawing a picture',
  };

  // ── What the assistant is doing, while it does it ─────────────────────────
  //
  // The assistant works for a minute and then a reply appears, and nothing in
  // between says that anything happened. This is that minute, made visible: a
  // row of chips that fills in as each piece of work lands, and stays afterwards
  // as the record of what went on between two messages.
  //
  // Grouped into families rather than shown one tool at a time. "Read 4 files"
  // is a thing a person can hold in their head; four separate lines saying
  // `read` are noise, and the user is not supposed to know what a tool is.
  const TOOL_FAMILY = {
    write: 'write', edit: 'write',
    bash: 'run', preview: 'run', game: 'run',
    read: 'look', grep: 'look', glob: 'look',
    webfetch: 'web', websearch: 'web',
    task: 'think',
    browser: 'see', capture: 'see',
    create_project: 'project', open_project: 'project', rename_project: 'project',
    delete_projects: 'project', list_projects: 'project', assign_project: 'project',
    show_settings: 'setting', set_appearance: 'setting', set_voice: 'setting',
    set_sounds: 'setting', set_child_mode: 'setting',
    set_model: 'setting', set_dictation_quality: 'setting',
    draw: 'write',
  };

  // Shape and sound per family, from the server so there is only one table.
  // See agent_server/activity.py.
  const FAMILY = (function () {
    try { return JSON.parse(document.getElementById('activity-families').textContent); }
    catch (e) { return {}; }
  })();
  // Handed out to the ticking, which lives above this and cannot see FAMILY.
  // The pitch a family ticks at while it works is the pitch its chip plays when
  // it finishes, so the two agree.
  for (const [family, spec] of Object.entries(FAMILY)) toolNotes[family] = spec.note;

  function familyOf(name) { return TOOL_FAMILY[name] || 'run'; }

  // A short note per family. Quiet and single -- the working sound has to be
  // ignorable, because it repeats. The two-note flourish is saved for the end
  // of the whole turn, where it means something.
  function cueTool(family) {
    if (!soundCues) return;
    const spec = FAMILY[family];
    blip(spec ? spec.note : 640, 0, 0.09, 0.10);
  }

  let activity = null;

  function activityStrip() {
    if (activity && activity.wrap.isConnected) return activity;
    // Same shape as the one the template renders for a conversation loaded from
    // the database: a shut disclosure whose summary is the chips.
    const wrap = document.createElement('details');
    wrap.className = 'did';
    const summary = document.createElement('summary');
    summary.className = 'did-summary';
    const el = document.createElement('span');
    el.className = 'activity';
    // Not a live region. A sighted user watches it fill in; a screen reader
    // gets one tidy sentence at the end of the turn instead of a running
    // commentary of every file that was opened.
    el.setAttribute('aria-hidden', 'true');
    summary.appendChild(el);
    const body = document.createElement('div');
    body.className = 'did-body';
    wrap.appendChild(summary);
    wrap.appendChild(body);
    activity = { wrap, el, body, counts: {}, chips: {}, items: [] };
    // At the end, always.
    //
    // It used to go in *before* the assistant's bubble, and the bubble was one
    // bubble for the whole turn -- so every chip for a turn piled into a single
    // strip pinned above the first thing the assistant said, and the words all
    // ran together underneath it as one wall of text. You could not tell which
    // sentence caused which work, and on a long turn the chips were a screen
    // and a half above where you were reading.
    //
    // Now the turn reads in the order it happened: said this, did that, said
    // the next thing. Which is also exactly how it reads when the conversation
    // is loaded back from the database -- the two used to disagree.
    messages.appendChild(wrap);
    return activity;
  }

  function noteToolDone(ev) {
    const name = ev.name;
    const family = familyOf(name);
    const strip = activityStrip();
    strip.counts[family] = (strip.counts[family] || 0) + 1;
    const n = strip.counts[family];
    const spec = FAMILY[family] || FAMILY.run;

    // The record of this one call, in the shape the builder wants -- the same
    // shape the server puts in `data-items` for a conversation loaded back.
    const diff = ev.diff || '';
    strip.items.push({
      family: family,
      glyph: spec.glyph,
      tool: name,
      label: shortLabel(name, ev.title || ''),
      full: (ev.title || '').trim(),
      diff: diff.slice(0, MAX_DIFF_CHARS),
      clipped: diff.length > MAX_DIFF_CHARS,
      failed: !!ev.is_error,
      ms: ev.duration_ms || 0,
    });
    strip.body.dataset.items = JSON.stringify(strip.items);
    // Rebuilt if it is already open -- somebody can leave a group open and
    // watch the list fill in while the turn runs.
    if (strip.wrap.open) {
      strip.body.dataset.built = '';
      buildDid(strip.body);
    }

    let chip = strip.chips[family];
    if (!chip) {
      chip = document.createElement('span');
      chip.className = 'chip chip-' + family;
      chip.innerHTML = '<span class="chip-glyph"></span><span class="chip-text"></span>';
      chip.querySelector('.chip-glyph').textContent = spec.glyph;
      strip.chips[family] = chip;
      strip.el.appendChild(chip);
    }
    chip.querySelector('.chip-text').textContent =
      (n === 1 ? spec.one : spec.many.replace('{n}', n));

    // A call that failed still counts towards "read 4 files" -- the work was
    // attempted, and a turn where everything failed would otherwise show no
    // chips and read as though nothing had happened. But "wrote a file", when
    // the write was refused, is a lie, so say how many did not work.
    const failures = strip.items.filter((x) => x.failed).length;
    if (failures) {
      if (!strip.failChip) {
        strip.failChip = document.createElement('span');
        strip.failChip.className = 'chip chip-failed';
        strip.el.appendChild(strip.failChip);
      }
      strip.failChip.textContent = failures + ' failed';
      strip.el.appendChild(strip.failChip);
    }

    // Retriggered by removing and re-adding, so the tenth file lands as
    // visibly as the first.
    chip.classList.remove('chip-pop');
    void chip.offsetWidth;
    chip.classList.add('chip-pop');
    // Last, however many kinds of work turn up.
    if (strip.failChip) strip.el.appendChild(strip.failChip);
    cueTool(family);
    scrollToBottom();
  }

  /* ── What it actually did, when you ask ───────────────────────────────────
   *
   * The chips say "read 4 files". Opening the group says which four, and shows
   * the diff for anything that changed. Everything is shut to begin with: a
   * user who does not want to know what a tool is never has to find out, and
   * this is meant to be an agent a professional could work in, where "what did
   * it change" is the first question.
   *
   * Built here rather than in the template, and only when a group is first
   * opened, so a long conversation is not carrying every diff in the page from
   * the moment it loads. The same builder does the live case, so the two cannot
   * drift apart the way the ordering did.
   */
  const PATH_TOOLS = { read: 1, write: 1, edit: 1 };
  // Matches agent_server/activity.py's MAX_DIFF_CHARS. Every diff in a
  // conversation held in the page at once is a lot of page, and this is a
  // summary of the work rather than the record of it -- the whole thing is in
  // the project's own history, which is what git is there for.
  const MAX_DIFF_CHARS = 6000;

  // Matches agent_server/activity.py's short_label.
  function shortLabel(tool, title) {
    title = (title || '').trim();
    if (PATH_TOOLS[tool] && title.indexOf('/') !== -1) {
      return title.slice(title.lastIndexOf('/') + 1);
    }
    return title || tool || 'did something';
  }

  function tookHowLong(ms) {
    if (!ms || ms < 1000) return '';
    return ms < 10000 ? (ms / 1000).toFixed(1) + 's' : Math.round(ms / 1000) + 's';
  }

  // A unified diff, coloured. Deliberately not a full diff viewer: the lines
  // are already labelled by their first character, and reading it is what
  // matters, not being able to act on it.
  function renderDiff(text, clipped) {
    const pre = document.createElement('pre');
    pre.className = 'diff';
    const lines = text.split('\n');
    for (const line of lines) {
      const row = document.createElement('span');
      const c = line[0];
      row.className = 'diff-line' + (
        line.startsWith('@@') ? ' diff-hunk'
          : c === '+' ? ' diff-add'
            : c === '-' ? ' diff-del' : '');
      row.textContent = line || ' ';
      pre.appendChild(row);
    }
    if (clipped) {
      const more = document.createElement('span');
      more.className = 'diff-line diff-hunk';
      more.textContent = '… the rest is in the project’s own history';
      pre.appendChild(more);
    }
    return pre;
  }

  function buildDid(body) {
    if (!body || body.dataset.built === '1') return;
    body.dataset.built = '1';
    let items = [];
    try { items = JSON.parse(body.dataset.items || '[]'); } catch (e) { items = []; }
    body.textContent = '';
    if (!items.length) {
      body.textContent = 'Nothing was recorded for this.';
      return;
    }
    const list = document.createElement('ol');
    list.className = 'did-list';
    for (const it of items) {
      const li = document.createElement('li');
      li.className = 'did-item chip-' + it.family + (it.failed ? ' did-failed' : '');

      const head = document.createElement('div');
      head.className = 'did-head';
      const glyph = document.createElement('span');
      glyph.className = 'did-glyph';
      glyph.setAttribute('aria-hidden', 'true');
      glyph.textContent = it.glyph || '';
      const what = document.createElement('span');
      what.className = 'did-what';
      what.textContent = it.label || '';
      // The whole path as the tooltip: the line shows the part you recognise,
      // and the rest is there when you need to know which of two same-named
      // files it was.
      if (it.full && it.full !== it.label) what.title = it.full;
      head.appendChild(glyph);
      head.appendChild(what);
      if (it.failed) {
        const bad = document.createElement('span');
        bad.className = 'did-bad';
        bad.textContent = 'failed';
        head.appendChild(bad);
      }
      const took = tookHowLong(it.ms);
      if (took) {
        const t = document.createElement('span');
        t.className = 'did-ms';
        t.textContent = took;
        head.appendChild(t);
      }
      li.appendChild(head);
      if (it.diff) li.appendChild(renderDiff(it.diff, it.clipped));
      list.appendChild(li);
    }
    body.appendChild(list);
  }

  // Built on the way open, once. `toggle` fires for every details on the page
  // as the user works through a conversation, and most are never opened.
  document.addEventListener('toggle', (e) => {
    const d = e.target;
    if (d.tagName === 'DETAILS' && d.classList.contains('did') && d.open) {
      buildDid(d.querySelector('.did-body'));
    }
  }, true);

  /* Click anywhere in an opened block to shut it again.
   *
   * These open into something screenfuls long, and the only way to close one
   * was the little summary line at the top -- which by the time you have read
   * to the bottom is a long way back up the page. Now the whole thing is its
   * own close button, which is what a person tries first anyway.
   *
   * Not on: anything you can press or type into, and not when you were
   * selecting text, which is the other reason to click inside a block of code.
   * A selection ending here means the mouse went down somewhere else, so this
   * runs on mouseup rather than click. */
  const SHUTS_ON_CLICK = '.did, .thinking, .summary-note';

  document.addEventListener('click', (e) => {
    const block = e.target.closest && e.target.closest(SHUTS_ON_CLICK);
    if (!block || !block.open) return;
    // The summary is the ordinary toggle and already does this.
    if (e.target.closest('summary')) return;
    if (e.target.closest('button, a, input, select, textarea, label, [role="button"]')) return;
    const selection = window.getSelection();
    if (selection && !selection.isCollapsed) return;
    // A diff inside the block scrolls on its own, and a click on its scrollbar
    // targets the diff. Dragging that to read further must not shut the thing
    // you are reading. A scrollbar is the only place a click lands outside the
    // element's own content box.
    const t = e.target;
    if (t.clientWidth && (e.offsetX > t.clientWidth || e.offsetY > t.clientHeight)) return;

    /* Hold the page still. Shutting a tall block removes its whole height at
     * once, so everything below jumps up by that much -- including whatever
     * the user was looking at. Keeping the summary line where it was on screen
     * makes the block look like it folded away rather than the page lurching. */
    const summary = block.querySelector('summary');
    const before = summary ? summary.getBoundingClientRect().top : 0;
    block.open = false;
    if (summary && chatScroller) {
      const after = summary.getBoundingClientRect().top;
      chatScroller.scrollTop += after - before;
      // Off the top after all that (the block began above the fold): bring the
      // line the user just closed back where they can see it.
      if (summary.getBoundingClientRect().top < 0) {
        summary.scrollIntoView({ block: 'center', behavior: window.__scrollBehavior() });
      }
    }
  });

  // One sentence, for the announcer and for anyone who cannot see the chips.
  function activitySentence() {
    if (!activity || !Object.keys(activity.counts).length) return '';
    const parts = Object.entries(activity.counts).map(([family, n]) => {
      const spec = FAMILY[family] || FAMILY.run;
      return n === 1 ? spec.one : spec.many.replace('{n}', n);
    });
    let said = parts.length === 1
      ? 'I ' + parts[0] + '.'
      : 'I ' + parts.slice(0, -1).join(', ') + ' and ' + parts[parts.length - 1] + '.';
    const failures = activity.items.filter((x) => x.failed).length;
    if (failures) {
      said += failures === 1 ? ' One of those failed.' : ' ' + failures + ' of those failed.';
    }
    return said;
  }

  function finishActivity() {
    if (!activity) return;
    if (!Object.keys(activity.counts).length) {
      activity.wrap.remove();
    } else {
      activity.el.classList.add('activity-done');
      // On the summary rather than the chips: the chips are hidden from a
      // screen reader, and this is the thing it lands on.
      activity.wrap.querySelector('.did-summary')
        .setAttribute('aria-label', activitySentence() + ' Open for details.');
    }
    activity = null;
  }

  // ── The working glyph ─────────────────────────────────────────────────────
  //
  // A character that cycles, which is the cheapest possible "something is
  // happening": no layout, no compositing, one text node. Stopped entirely for
  // anyone who has asked for less motion -- for them it simply sits still.
  const SPINNER = ['\u2735', '\u2736', '\u2737', '\u2738', '\u2739', '\u273a', '\u2739', '\u2738', '\u2737', '\u2736'];
  const stillPlease = window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let spinAt = 0;
  // A step every quarter second, not every tenth. At 110ms it was a flicker
  // rather than a movement -- too fast to read as one shape turning into
  // another, which is the only thing that makes it say "working" rather than
  // "something is wrong with the screen".
  const SPIN_MS = 240;

  // Returns its own handle rather than sharing one. Two things can be spinning
  // at once -- the assistant working, and a file being attached while it does
  // -- and with a single shared timer the second one starting silently stopped
  // the first.
  function startSpinner(el) {
    if (!el) return 0;
    el.textContent = SPINNER[0];
    if (stillPlease) return 0;
    return setInterval(() => {
      spinAt = (spinAt + 1) % SPINNER.length;
      el.textContent = SPINNER[spinAt];
    }, SPIN_MS);
  }

  function stopSpinner(handle) {
    if (handle) clearInterval(handle);
    return 0;
  }

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
    if (name === 'task') {
      return a.description ? ' ' + clip(a.description, 28) : '';
    }
    if (name === 'create_project' || name === 'open_project' ||
        name === 'rename_project' || name === 'assign_project') {
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
  // Not constants any more: the assistant can be asked to change the voice or
  // the speed, and the change has to reach the page that is already open.
  let ttsVoice = view.dataset.ttsVoice;
  let ttsSpeed = parseFloat(view.dataset.ttsSpeed || '1.25');
  let ttsVolume = parseFloat(view.dataset.ttsVolume || '0.75');

  /* The settings the chat itself holds in variables, brought back in line with
   * the server. See `refreshSettings`, which fetches and calls this.
   *
   * Only what actually moved, and never while the thing it controls is in use:
   * changing the read-aloud volume mid-sentence would step on a slider the user
   * may have their hand on, and re-announcing the button state every turn would
   * make a screen reader say "read aloud, pressed" after every reply. */
  function applyChatSettings(s) {
    soundCues = !!s.sound_cues;
    soundTicks = !!s.sound_ticks;
    const vol = parseFloat(s.sound_volume);
    if (Number.isFinite(vol)) soundVolume = vol;

    if (s.tts_voice) ttsVoice = s.tts_voice;
    const speed = parseFloat(s.tts_speed);
    if (Number.isFinite(speed)) ttsSpeed = speed;
    const loud = parseFloat(s.tts_volume);
    if (Number.isFinite(loud)) ttsVolume = loud;

    if (!!s.tts_auto !== ttsAutoEnabled) {
      ttsAutoEnabled = !!s.tts_auto;
      const btn = document.getElementById('tts-btn');
      if (btn) {
        btn.setAttribute('aria-pressed', ttsAutoEnabled ? 'true' : 'false');
        btn.classList.toggle('tts-off', !ttsAutoEnabled);
      }
      // Switched off while it was talking: stop, rather than finish the reply
      // the user has just asked you to stop reading.
      if (!ttsAutoEnabled) stopSpeech();
    }
    window.__markChildMode(!!s.child_mode);
  }
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
        holdSoundsForVoice();
        try {
          await playToEnd(audio);
        } finally {
          releaseSoundsForVoice();
        }
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
  let pendingAction = null;

  function handleEvent(ev) {
    switch (ev.type) {
      case 'reasoning':
        setWorkPhase('thinking');
        noteThinking(ev.text);
        break;
      case 'content':
        finishThinking();
        // A new stretch of words after some work means a new message, not more
        // of the last one. That is how it is stored, and how it reads back.
        if (!assistantEl) {
          finishActivity();
          assistantEl = bubble('assistant');
          assistantBuffer = '';
        }
        assistantBuffer += ev.text;
        turnText += ev.text;
        assistantEl.textContent = assistantBuffer;
        renderMarkdown(assistantEl);
        scrollToBottom();
        clearLiveWork();
        setWorkPhase('writing');
        break;
      case 'tool_start':
        finishThinking();
        closeSegment();
        setLiveWork(statusForTool(ev.name, ev.args));
        setWorkPhase(familyOf(ev.name));
        break;
      case 'tool_end':
        noteToolDone(ev);
        if (ev.open_session) {
          pendingOpen = ev.open_session;
          appendAction(ev.open_session, ev.open_session_name);
        }
        // Something for the user to answer. Held until the turn ends rather
        // than thrown up mid-sentence: a dialog that lands while the assistant
        // is still explaining what it is for covers the explanation.
        if (ev.action) pendingAction = ev.action;
        clearLiveWork();
        setWorkPhase('thinking');
        break;
      case 'compacting':
        finishThinking();
        closeSegment();
        setLiveWork('Summarising our conversation\u2026');
        setWorkPhase('summarising');
        break;
      case 'permission':
        /* A file outside the project, and the turn is sitting inside a tool
         * call waiting for the answer. Straight up, not held until the end of
         * the turn like the other dialogs -- nothing else is going to happen
         * until this is answered, so holding it back would be showing somebody
         * a spinner and waiting for them to answer a question they cannot
         * see. */
        askPermission(ev);
        break;
      case 'waiting':
        /* The assistant has said its piece and is now sitting on a command
         * that has not finished. Two things have to happen here.
         *
         * The stretch of words is closed, because what comes next is a
         * separate message -- that is how it is stored, and a reload would
         * otherwise show two bubbles where the live page showed one.
         *
         * And the strip says what is being waited for. The user has just been
         * told "I'll say when it's done"; without this the app looks like it
         * has finished and gone quiet, which is the exact impression the whole
         * feature exists to prevent. */
        finishThinking();
        closeSegment();
        setLiveWork(ev.for || 'Waiting for that to finish…');
        setWorkPhase('running');
        break;
      case 'compacted':
        /* The same marker the server renders when the page is loaded back.
         * Live, summarising showed only as a status line that then vanished --
         * so the conversation appeared to lose two hours of itself with
         * nothing on screen to say why, until a reload put the note there. */
        if (ev.ok && ev.summary) appendSummary(ev.summary);
        /* And a switch the user queued some time ago has just happened, since
         * summarising is the moment it costs nothing. They asked for it and
         * carried on working, possibly an hour ago -- a reply arriving from a
         * different AI with nothing said is the sort of thing people notice
         * and mistrust. */
        if (ev.switched_to) announceSwitch(ev.switched_to);
        break;
      case 'done':
        finishAssistant(ev);
        break;
      case 'error':
        // Anything said this turn, not just the stretch in progress. With the
        // reply split into several messages, a failure after the first one had
        // been finished left `assistantBuffer` empty -- so this took the user's
        // message back out from under a reply that was still on the screen.
        if (turnText.trim()) finishAssistant(ev);
        else {
          showError(ev.message);
          // The message stays. An error arriving down this stream means the
          // server accepted it, wrote it to the conversation and then failed
          // trying to answer -- so taking the bubble off the screen made the
          // app disagree with its own database, and the message came back the
          // next time the page loaded. Worse, someone who had dictated three
          // sentences watched them disappear with nothing to send again.
          pendingUserMsg = null;
        }
        endTurn();
        break;
      case 'aborted':
        markBrokeOff();
        endTurn();
        break;
      case 'stream_end':
        endTurn();
        break;
      // A message the user typed while this turn was running has just been
      // folded in. It is a real message now, so the "waiting" mark comes off --
      // and it stays where it is, which is where the assistant actually read
      // it: after everything it had already done.
      case 'queued_message':
        closeSegment();
        finishActivity();
        // Moved to the end before the mark comes off, because the end is where
        // it was actually read -- it went on screen the moment it was typed,
        // which was some way back up the turn, and leaving it there would say
        // the assistant saw it before work it had not done yet. This is also
        // where the row lands in the database, so a reload agrees.
        messages.querySelectorAll('.message.pending').forEach((wrap) => {
          messages.appendChild(wrap);
          unpend(wrap);
        });
        scrollToBottom();
        break;
      case 'attached':
      // A mark saying the run has written everything up to this point to the
      // database. Only a client re-attaching needs it; a live turn is watching
      // it happen and has drawn it already.
      case 'saved':
        break;
    }
  }

  let assistantEl = null;
  // The stretch of words being written right now. `turnText` is everything the
  // assistant has said this turn, across all of them, which is what gets
  // announced once at the end.
  let assistantBuffer = '';
  let turnText = '';
  let lastSaidEl = null;
  let pendingUserMsg = null;

  /* Finish the stretch of words currently being written.
   *
   * Called when the assistant stops talking and starts working, and again at
   * the end of the turn. An empty one -- the assistant reached straight for a
   * tool without saying anything -- leaves nothing behind. */
  function closeSegment() {
    if (!assistantEl) return null;
    const el = assistantEl;
    const text = assistantBuffer;
    assistantEl = null;
    assistantBuffer = '';
    if (!text.trim()) {
      const msg = el.closest('.message');
      if (msg && msg.parentNode) msg.remove();
      return null;
    }
    // From the raw text, not from the element: by now the element holds
    // already-rendered HTML, and re-rendering its textContent would strip every
    // markdown construct out of it.
    el.innerHTML = window.md.render(text);
    upgradeFileRefs(el);
    addLinkPreviews(el);
    speak(text, el.closest('.bubble'));
    lastSaidEl = el;
    return el;
  }

  // Take back the user's message when a send failed, so the conversation reads
  // as if it was never sent. Called together with removeEmptyAssistant().
  //
  // Only for a send the server never accepted. Once it has been accepted the
  // message is in the conversation on disk, and removing the bubble makes the
  // screen disagree with what a reload will show.
  //
  // The words go back in the composer, because they are the user's and this app
  // is used by people who cannot simply type them again. Never over something
  // they have started writing since.
  function revertFailedTurn(text) {
    if (pendingUserMsg && pendingUserMsg.parentNode) pendingUserMsg.remove();
    pendingUserMsg = null;
    if (text && textarea && !textarea.value.trim()) {
      textarea.value = text;
      // The composer's own input handler resizes the box and saves the draft,
      // so the words also survive a reload rather than only a retry.
      textarea.dispatchEvent(new Event('input'));
      textarea.focus();
    }
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
    closeSegment();
    if (turnText.trim()) {
      // Announce the finished reply once, for the whole turn rather than each
      // stretch of it. Skipped when read-aloud is on, because Kokoro is saying
      // the same words and two voices over each other is worse than either.
      if (!ttsAutoEnabled) announce(turnText);
      // Only for a turn slow enough that the user may have looked away, and
      // never when read-aloud is on -- the reply speaking is itself the signal.
      if (soundCues && !ttsAutoEnabled && turnStartedAt &&
          Date.now() - turnStartedAt >= SOUND.minTurnMs) {
        cueDone();
      }
    }
    if (ev.type === 'done' && turnStartedAt) {
      const secs = Math.max(1, Math.round((Date.now() - turnStartedAt) / 1000));
      const note = document.createElement('span');
      note.className = 'worked-note';
      note.textContent = secs === 1 ? 'Worked for 1 second' : 'Worked for ' + secs + ' seconds';
      // On the last thing it said, which is where the eye ends up.
      const bubble = lastSaidEl ? lastSaidEl.closest('.bubble') : null;
      if (bubble) bubble.appendChild(note);
    }
    turnStartedAt = 0;
    turnText = '';
    lastSaidEl = null;
    finishThinking();
    stopTicks();
    clearLiveWork();
    maybeAutoOpen();
    runPendingAction();
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
    /* The button arrives with the tool result, so anything the assistant says
     * afterwards lands underneath it and buries it. Moved to the end of the
     * turn, it is the last thing on the page and the first thing the keyboard
     * reaches -- which is also exactly what the reply above it now describes. */
    const row = button.closest('.message.action');
    if (row && row !== messages.lastElementChild) messages.appendChild(row);
    button.focus({ preventScroll: true });
    scrollToBottom();
    announce(button.textContent.trim() + '. Press Enter to open it.');
  }

  /* A question the assistant raised for the user to answer.
   *
   * Deliberately the only route by which any of this happens. The assistant can
   * work most of the settings page directly, because none of it does damage and
   * all of it is undoable by asking again -- but removing projects and changing
   * child mode are not that, and a model that has misheard "delete the old
   * website ones" gathers up the wrong list with total confidence. So for those
   * two, its tool ends at a proposal and the person at the keyboard decides.
   *
   * At the end of the turn, so the reply explaining the box has been said
   * before the box covers it. */
  function runPendingAction() {
    const action = pendingAction;
    pendingAction = null;
    if (!action) return;
    if (action.kind === 'child_mode') {
      if (window.__askChildMode) window.__askChildMode(!!action.on);
    } else if (action.kind === 'delete_projects') {
      askRemoveProjects(action.sessions || []);
    } else if (action.kind === 'add_funds') {
      // Not a question, so not a dialog. It goes into the conversation and
      // stays there, to be read at whatever pace and shown to whoever has the
      // card -- which may be somebody who is not in the room yet.
      appendFunds(action);
    }
  }

  function askRemoveProjects(sessions) {
    const modal = document.getElementById('remove-modal');
    if (!modal || !sessions.length) return;
    const list = document.getElementById('remove-list');
    const err = document.getElementById('remove-modal-error');
    const confirmBtn = document.getElementById('remove-confirm');
    const cancelBtn = document.getElementById('remove-cancel');
    const n = sessions.length;

    document.getElementById('remove-modal-title').textContent =
      n === 1 ? 'Remove this project?' : 'Remove these ' + n + ' projects?';
    err.hidden = true;
    list.textContent = '';
    // Every name, not a count. "27 projects" is not something anybody can agree
    // to, and the one they would have kept is always somewhere in the list.
    for (const s of sessions) {
      const li = document.createElement('li');
      li.textContent = s.name;
      list.appendChild(li);
    }
    // Says how many, so it is still an answerable question with your eyes shut.
    confirmBtn.textContent = n === 1 ? 'Remove it' : 'Remove all ' + n;

    function finish() {
      confirmBtn.removeEventListener('click', go);
      cancelBtn.removeEventListener('click', finish);
      window.__closeModal();
    }
    async function go() {
      confirmBtn.disabled = true;
      let result;
      try {
        result = await fetch('/api/sessions/remove', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ids: sessions.map((s) => s.id) }),
        }).then((r) => r.json());
      } catch (e) {
        result = null;
      }
      confirmBtn.disabled = false;
      if (!result || !result.ok) {
        err.textContent = 'That did not work. Nothing was removed.';
        err.hidden = false;
        return;
      }
      finish();
      const gone = (result.removed || []).length;
      showToast(gone === 1 ? 'Removed 1 project.' : 'Removed ' + gone + ' projects.');
      announce(gone === 1 ? 'One project removed.' : gone + ' projects removed.');
      // The menu at the top still lists every one of them. The poller would
      // catch up within a couple of seconds; taking them out now means the
      // list is never briefly a lie about what the user just did.
      const menu = document.getElementById('sessions-menu');
      if (menu) {
        for (const s of sessions) {
          const row = menu.querySelector('[data-session-id="' + s.id + '"]');
          if (row) row.remove();
        }
      }
    }

    confirmBtn.addEventListener('click', go);
    cancelBtn.addEventListener('click', finish);
    modal.addEventListener('modalclosed', finish, { once: true });
    // Onto "Keep them". The safe answer is the one the keyboard lands on, and
    // this box can appear without anybody having asked for it by hand.
    window.__openModal(modal, cancelBtn);
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
    // A turn that was stopped or failed mid-thought still leaves the thought
    // where it got to, marked finished rather than left turning forever.
    finishThinking();
    stopTicks();
    // Before the announcement below, so the chips have settled and the
    // sentence describes a finished turn rather than one in progress.
    const did = activitySentence();
    finishActivity();
    running = false;
    sendBtn.hidden = false;
    stopBtn.hidden = true;
    clearLiveWork();
    refreshSettings();
    refreshPlay();
    // One sentence for the whole turn. Someone listening gets "I read 3 files
    // and ran a command" rather than nothing at all, which is what they got
    // before, and rather than a running commentary, which would be worse.
    if (did) announce(did);
  }

  function beginTurn() {
    running = true;
    sendBtn.hidden = true;
    stopBtn.hidden = false;
    refreshPlay();
    turnStartedAt = Date.now();
    startTicks();
  }

  /* A message typed while the assistant is still working.
   *
   * It used to be dropped on the floor: the send handler saw a turn running and
   * simply returned, so you typed, pressed Send, and watched your words vanish.
   * The server has always been able to hold one and fold it in at the next
   * boundary; nothing here ever asked it to.
   *
   * It goes into the conversation straight away, marked as not sent yet, with
   * an Undo that takes it back and puts the words in the box. When the
   * assistant reaches a boundary and picks it up, the mark comes off and it
   * becomes an ordinary message, in the place in the turn where it was actually
   * read -- after whatever the assistant had already done by then.
   */
  async function queueMessage(text) {
    let queueId = null;
    try {
      const resp = await fetch('/api/sessions/' + sessionId + '/queue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });
      const data = await resp.json().catch(() => null);
      if (!resp.ok || !data || !data.queue_id) throw new Error('not queued');
      queueId = data.queue_id;
    } catch (e) {
      // It never reached the queue, so it is not waiting anywhere. Give the
      // words back rather than leaving a message on screen that nobody has.
      textarea.value = text;
      textarea.dispatchEvent(new Event('input'));
      showError('That could not be sent. Your message is back in the box.');
      return;
    }
    appendPending(text, queueId);
  }

  function appendPending(text, queueId) {
    // appendUser hands back the whole message, not its content.
    const wrap = appendUser(text);
    wrap.classList.add('pending');
    wrap.dataset.queueId = queueId;

    const note = document.createElement('div');
    note.className = 'pending-note';
    const said = document.createElement('span');
    said.textContent = 'Waiting — it is still working';
    const undo = document.createElement('button');
    undo.type = 'button';
    undo.className = 'pending-undo';
    // "Undo", not "revert". Everybody knows what undo means.
    undo.textContent = 'Undo';
    undo.setAttribute('aria-label', 'Undo this message and put it back in the box');
    undo.addEventListener('click', () => undoPending(wrap));
    note.appendChild(said);
    note.appendChild(undo);
    wrap.querySelector('.bubble').appendChild(note);
    announce('Waiting to send. Press Undo to take it back.');
    scrollToBottom();
  }

  async function undoPending(wrap) {
    const queueId = wrap.dataset.queueId;
    try {
      const resp = await fetch(
        '/api/sessions/' + sessionId + '/queue/' + queueId, { method: 'DELETE' });
      const data = await resp.json().catch(() => null);
      if (!resp.ok) {
        // It went in while the user was reaching for the button. Leave it: it
        // is a real message now and taking it off the screen would be a lie.
        showError('Too late — it has already been read.');
        unpend(wrap);
        return;
      }
      wrap.remove();
      textarea.value = (data && data.message) || '';
      textarea.dispatchEvent(new Event('input'));
      textarea.focus();
      announce('Taken back. Your message is in the box again.');
    } catch (e) {
      showError('That could not be undone.');
    }
  }

  function unpend(wrap) {
    if (!wrap) return;
    wrap.classList.remove('pending');
    delete wrap.dataset.queueId;
    const note = wrap.querySelector('.pending-note');
    if (note) note.remove();
  }

  async function sendMessage(text, images) {
    text = (text || '').trim();
    if (!text) return;
    if (running) { queueMessage(text); return; }
    if (!hasKey) {
      window.location.href = '/settings';
      return;
    }
    pendingUserMsg = appendUser(text);
    // No bubble yet. One is made the moment the assistant actually says
    // something, so a turn that starts by reading four files does not sit there
    // showing an empty speech bubble while it works.
    assistantEl = null;
    assistantBuffer = '';
    turnText = '';
    lastSaidEl = null;
    beginTurn();
    // Whether the server took the message. Everything about how a failure is
    // handled turns on it: before, nothing happened and the message is the
    // user's to send again; after, it is in the conversation on disk and the
    // turn is running, whatever this page can still see of it.
    let accepted = false;
    try {
      const resp = await fetch('/api/sessions/' + sessionId + '/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, images: images || [] }),
      });
      if (!resp.ok) {
        showError('I could not send that message. Please try again.');
        revertFailedTurn(text);
        endTurn();
        return;
      }
      accepted = true;
      await readSSE(resp, handleEvent);
      // A stream can also just stop, with no error to catch and no `done` to
      // act on: the server shut down tidily, or something in between gave up on
      // a connection that had been quiet for four minutes. Reading it simply
      // ends. Without this the turn never finished on this side -- Stop stayed
      // on screen, the ticking carried on, and the page waited for a reply that
      // had already been written. Silent forever, which for somebody listening
      // rather than watching is the worst way there is to fail.
      if (running) { endTurn(); await recover(); }
    } catch (e) {
      if (!accepted) {
        showError('I could not send that message. Please try again.');
        revertFailedTurn(text);
        endTurn();
        return;
      }
      // The stream dropped part-way. The assistant is still working on the
      // other side of it, so this is a lost picture rather than a lost turn:
      // ending here left the user watching a dead screen while their answer
      // was being written, and it took their message off the page as well.
      pendingUserMsg = null;
      endTurn();
      await recover();
    }
  }

  /* Pick a turn back up after the connection to the app went away.
   *
   * It usually comes straight back -- the server was restarted, the machine
   * slept for a moment -- so this keeps trying for half a minute before it says
   * anything. The alternative is telling somebody who cannot reload a page that
   * they should reload the page.
   */
  const RECOVER_WAITS = [400, 1000, 2000, 4000, 8000, 15000];

  async function recover() {
    for (const wait of RECOVER_WAITS) {
      if (await reattach(true)) return true;
      await new Promise((done) => setTimeout(done, wait));
    }
    showError(
      'I lost my connection to the app. Nothing you said has been lost — it '
      + 'will all be here when the app is running again.'
    );
    return false;
  }

  // ── Play and stop ─────────────────────────────────────────────────────────
  //
  // The assistant is supposed to keep the project running, and mostly does.
  // But when it forgets, the user is reading that their game is ready with no
  // game anywhere and no terminal to start one from. So the same machinery
  // gets a button.

  const playBtn = document.getElementById('play-fab');
  const playWord = document.getElementById('play-fab-word');
  let playState = { command: '', running: false };
  let playBusy = false;

  function renderPlay() {
    if (!playBtn) return;
    const has = !!playState.command;
    playBtn.hidden = !has;
    // One button, two states, never both on screen at once. The glyph, the
    // word, the colour and what pressing it does all change together.
    const on = !!playState.running;
    playBtn.classList.toggle('running', on);
    if (playWord) playWord.textContent = on ? 'Stop' : 'Play';
    // Disabled rather than hidden while the assistant works: a button that
    // vanishes and comes back is harder to find again than one that greys out,
    // and someone navigating by keyboard loses their place entirely.
    // `running` is this page's own view of the turn; `playState.busy` is the
    // server's, which is what a page opened in the middle of one has to go on.
    playBtn.disabled = playBusy || running || playState.busy;
    playBtn.setAttribute('aria-label', on ? 'Stop your project' : 'Play your project');
    playBtn.title = on
      ? 'Stop — close your project and shut it down'
      : 'Play — open your project so you can use it';
    if (has) positionPlayBtn();
    renderPick();
  }

  // The mirror of the jump-to-newest button on the other side: just above the
  // composer, and just left of the conversation column. On a wide screen that
  // puts it out in the empty gutter; on a narrow one the gutter is gone and it
  // tucks against the edge instead.
  function positionPlayBtn() {
    const inner = document.querySelector('.chat-scroll-inner');
    const wrap = document.querySelector('.composer-wrap');
    if (!playBtn || !inner || !wrap || playBtn.hidden) return;
    const gap = 16;
    // Measured with the button laid out, never while it is display:none -- a
    // hidden element measures zero, and a zero-height button always looks like
    // it fits, so it would flicker back and forth every time this ran.
    playBtn.classList.remove('no-room');
    playBtn.classList.remove('play-fab-tight');
    const height = playBtn.offsetHeight;

    const contentLeft = inner.getBoundingClientRect().left;
    const composer = wrap.getBoundingClientRect();
    playBtn.style.bottom = (window.innerHeight - composer.top + gap) + 'px';

    // Down to the glyph alone when the gutter is too narrow for the word. The
    // pill is 118px and the gutter beside a 960px column on a 1150px window is
    // 95, so it sat on top of the conversation -- and the thing it covered was
    // the last line the assistant said.
    if (contentLeft - gap - playBtn.offsetWidth < gap) {
      playBtn.classList.add('play-fab-tight');
    }
    // The floor is the composer's own left edge, not the window's. With the
    // settings panel open the chat is pushed right by the width of the panel,
    // and measured against the window this button sat underneath it -- hidden
    // behind the one thing you are meant to be able to talk past.
    playBtn.style.left =
      Math.max(composer.left + gap, contentLeft - gap - playBtn.offsetWidth) + 'px';

    // On a narrow screen the settings sheet comes up from the bottom and pushes
    // the composer right up under the app bar, leaving no band between the two
    // to sit in -- placed there anyway it landed on top of the Projects menu.
    // Out of the way until there is room again, which is one press of Close.
    const bar = document.querySelector('.app-bar');
    const barBottom = bar ? bar.getBoundingClientRect().bottom : 0;
    if (composer.top - gap - height < barBottom + 8) playBtn.classList.add('no-room');
    positionPickBtn();
  }

  // Stacked directly on top of Play, in the same gutter, following the same
  // rules -- narrow to the glyph where the gutter is tight, and out of the way
  // entirely when Play itself has nowhere to be. Placed from Play's own
  // measured box rather than from a constant, so the two never overlap however
  // the pill has been sized.
  function positionPickBtn() {
    const pick = document.getElementById('pick-btn');
    const wrap = document.querySelector('.composer-wrap');
    const inner = document.querySelector('.chat-scroll-inner');
    if (!pick || !wrap || !inner || pick.hidden) return;
    const gap = 16;
    pick.classList.remove('no-room', 'pick-fab-tight');

    const composer = wrap.getBoundingClientRect();
    const contentLeft = inner.getBoundingClientRect().left;
    // Above Play when Play is there, and in Play's place when it is not --
    // pointing at a running project without a Stop button is not a state the
    // app can reach, but a button that vanishes because its neighbour did
    // would be a puzzle rather than a rule.
    const playVisible = playBtn && !playBtn.hidden
                        && !playBtn.classList.contains('no-room');
    const floor = window.innerHeight - composer.top + gap;
    pick.style.bottom = (playVisible ? floor + playBtn.offsetHeight + 10 : floor) + 'px';

    if (contentLeft - gap - pick.offsetWidth < gap) {
      pick.classList.add('pick-fab-tight');
    }
    pick.style.left =
      Math.max(composer.left + gap, contentLeft - gap - pick.offsetWidth) + 'px';

    const bar = document.querySelector('.app-bar');
    const barBottom = bar ? bar.getBoundingClientRect().bottom : 0;
    const top = window.innerHeight - parseFloat(pick.style.bottom) - pick.offsetHeight;
    if (top < barBottom + 8) pick.classList.add('no-room');
    positionPickHint();
  }

  /* The one-time line that says this exists at all.
   *
   * Kept in localStorage rather than in settings: it is a per-person, per-
   * screen nicety, and the cost of getting it wrong is somebody being told
   * once more than they needed to be, which is not a cost worth a database
   * column and a migration for.
   */
  const PICK_HINT_KEY = 'pick-hint-seen';

  function pickHintSeen() {
    try { return localStorage.getItem(PICK_HINT_KEY) === '1'; } catch (e) { return true; }
  }

  function dismissPickHint() {
    const hint = document.getElementById('pick-hint');
    if (hint) hint.hidden = true;
    try { localStorage.setItem(PICK_HINT_KEY, '1'); } catch (e) {}
  }

  function maybeShowPickHint() {
    const hint = document.getElementById('pick-hint');
    const pick = document.getElementById('pick-btn');
    if (!hint || !pick) return;
    // Only alongside the button it is about, and only while it is actually
    // reachable -- a hint pointing at something that is not on screen is worse
    // than no hint.
    const show = !pick.hidden && !pick.classList.contains('no-room')
                 && !pickHintSeen();
    if (hint.hidden !== !show) hint.hidden = !show;
    if (show) positionPickHint();
  }

  function positionPickHint() {
    const hint = document.getElementById('pick-hint');
    const pick = document.getElementById('pick-btn');
    const wrap = document.querySelector('.composer-wrap');
    if (!hint || !pick || !wrap || hint.hidden) return;
    const composer = wrap.getBoundingClientRect();
    hint.style.bottom = (window.innerHeight - composer.top + 16) + 'px';
    // Beside the button rather than under it, and clamped so a narrow window
    // never pushes it off the right-hand edge.
    const left = pick.getBoundingClientRect().right + 12;
    hint.style.left = Math.min(left, window.innerWidth - hint.offsetWidth - 16) + 'px';
  }

  // Both floating buttons hang off the composer, so both have to be put back
  // whenever it moves. A ResizeObserver is not enough on its own: opening the
  // settings sheet on a narrow screen moves the composer a long way up the page
  // without changing its size at all, and neither button heard about it -- so
  // both stayed down behind the sheet.
  function placeFloatingButtons() {
    positionScrollBottomBtn();
    positionPlayBtn();
  }

  async function refreshPlay() {
    if (!playBtn) return;
    const was = playState.running;
    try {
      const resp = await fetch('/api/sessions/' + sessionId + '/preview');
      if (!resp.ok) return;
      playState = await resp.json();
    } catch (e) { return; }
    // The assistant can start the project on its own, and when it does a window
    // appears and takes the focus. Someone who cannot see the screen has no way
    // to know that happened, so say it. Only on the change, and not when the
    // user pressed the button themselves -- they already know, and `callPlay`
    // has said so.
    if (playState.running && !was && !playBusy) {
      announce('Your project is now open in its own window.');
    }
    renderPlay();
  }

  async function callPlay(path, saying) {
    playBusy = true;
    renderPlay();
    try {
      const resp = await fetch('/api/sessions/' + sessionId + '/preview/' + path,
                               { method: 'POST' });
      const data = await resp.json().catch(() => null);
      if (!resp.ok) {
        showError((data && data.detail) || 'That would not start. Ask the assistant to look at it.');
      } else {
        announce(saying);
      }
    } catch (e) {
      showError('That would not start. Ask the assistant to look at it.');
    } finally {
      playBusy = false;
      await refreshPlay();
    }
  }

  if (playBtn) {
    playBtn.addEventListener('click', () => (playState.running
      ? callPlay('stop', 'Your project has been closed.')
      : callPlay('start', 'Your project is open.')));
    window.addEventListener('resize', positionPlayBtn);
    const wrap = document.querySelector('.composer-wrap');
    if (window.ResizeObserver && wrap) {
      new ResizeObserver(positionPlayBtn).observe(wrap);
    }
    // Coming back to this window is when the answer is most likely to have
    // gone stale, and nothing else asks between turns. Two different things
    // change out here: the project's window can be closed, and the project
    // itself can fall over.
    //
    // Those are NOT the same event and the buttons must not treat them as
    // one. Closing the window leaves the server running, exactly as it would
    // anywhere else -- Play still says Stop, because there is still something
    // to stop. Only pointing goes away, because pointing needs a page. "Why
    // is Stop still lit when I closed the window?" is a real question with a
    // real answer about front ends and back ends, and it is worth more than
    // the tidiness of making the button vanish.
    window.addEventListener('focus', () => { if (!picking) refreshPlay(); });
    refreshPlay();
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
    // Read before clearAttachments empties the list -- sendMessage does its
    // fetch a microtask later, by which point there would be nothing left.
    sendMessage(messageWithAttachments(textarea.value), attachedImagePaths());
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

  // Things the user pointed at, kept apart from attachments on purpose: the
  // model is told attachments are numbered and may be referred to by number,
  // and quietly slipping a non-file into that list would shift every number
  // the user can see.
  let points = [];

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
    attachmentsBox.hidden = attachments.length === 0 && points.length === 0;
    attachmentsBox.innerHTML = '';
    saveAttachments();
    renderPoints();
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
      const kind = a.isDir ? 'folder' : (looksLikeImage(a) ? 'image' : 'file');
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
    points = [];
    savePoints();
    renderAttachments();
  }


  /* ── Pointing at part of the running project ──────────────────────────────
   *
   * Press the button, the project's window comes to the front with a crosshair
   * on it, click the thing, and a chip appears here saying what you clicked.
   * What the AI receives is the element itself -- its tag, its text, its size,
   * the styles that actually decide how it looks -- plus the component name and
   * source file when the framework happens to expose them. Some do, some do
   * not, and the AI is told to go and search when they do not.
   *
   * The point of the whole thing is the sentence nobody has to write: "the blue
   * button", when there are four blue buttons, is where a beginner gets stuck.
   */

  const pickBtn = document.getElementById('pick-btn');
  const pointsKey = 'points:' + sessionId;
  let picking = false;

  function renderPick() {
    if (!pickBtn) return;
    // Absent, not disabled. Unlike Play -- which greys out mid-turn so it does
    // not move under a keyboard user's fingers -- this button only exists at
    // all while a project is open, so its arrival and departure are already
    // tied to something the user did.
    pickBtn.hidden = !playState.pickable;
    pickBtn.classList.toggle('busy', picking);
    const word = document.getElementById('pick-fab-word');
    if (word) word.textContent = picking ? 'Click it…' : 'Point at it';
    if (!pickBtn.hidden) positionPickBtn();
    maybeShowPickHint();
    pickBtn.setAttribute('aria-label', picking
      ? 'Waiting for you to click something in your project'
      : 'Point at something in your project');
  }

  async function startPicking() {
    if (picking) { await cancelPicking(); return; }
    dismissPickHint();
    picking = true;
    renderPick();
    announce('Your project is in front. Click the part you want to talk about, '
             + 'or press Escape.');
    let data = null;
    try {
      const resp = await fetch('/api/sessions/' + sessionId + '/pick', { method: 'POST' });
      data = await resp.json().catch(() => null);
      if (!resp.ok) {
        // Almost always the window was closed since the button last heard
        // anything. Say so plainly and go and ask, so the button that should
        // not have been there is gone by the time they read it.
        showError((data && data.detail) || 'Nothing is open to point at.');
        refreshPlay();
        return;
      }
    } catch (e) {
      showError('That did not work. Try pressing Play first.');
      return;
    } finally {
      picking = false;
      renderPick();
    }
    if (!data || !data.picked) {
      announce('Nothing was picked.');
      return;
    }
    points.push({ label: data.label, description: data.description });
    savePoints();
    renderAttachments();
    announce('Added ' + data.label + ' to your message.');
    textarea.focus();
  }

  async function cancelPicking() {
    picking = false;
    renderPick();
    try {
      await fetch('/api/sessions/' + sessionId + '/pick/cancel', { method: 'POST' });
    } catch (e) {}
  }

  function savePoints() {
    try { localStorage.setItem(pointsKey, JSON.stringify(points)); } catch (e) {}
  }

  function restorePoints() {
    let saved = [];
    try { saved = JSON.parse(localStorage.getItem(pointsKey) || '[]'); } catch (e) { return; }
    if (!Array.isArray(saved)) return;
    points = saved.filter((p) => p && p.description);
    if (points.length) renderAttachments();
  }

  function removePoint(index) {
    const [gone] = points.splice(index, 1);
    savePoints();
    renderAttachments();
    if (gone) announce(gone.label + ' removed.');
    (pickBtn && !pickBtn.hidden ? pickBtn : textarea).focus();
  }

  function renderPoints() {
    points.forEach((p, i) => {
      const chip = document.createElement('span');
      chip.className = 'attachment-chip point-chip';

      const icon = document.createElement('span');
      icon.className = 'point-chip-icon';
      icon.setAttribute('aria-hidden', 'true');
      icon.innerHTML =
        '<svg viewBox="0 0 24 24" width="18" height="18">' +
        '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" ' +
        'd="M6.5 3.2l11.7 8.1-4.9 1.1 2.7 5.5-2.6 1.3-2.7-5.5-3.4 3.6z"/></svg>';
      chip.appendChild(icon);

      const name = document.createElement('span');
      name.className = 'chip-name';
      // "You pointed at" rather than the bare text, because the text alone
      // ("Buy wheels") reads as something the user typed.
      name.textContent = p.label;
      chip.appendChild(name);

      const rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'rm';
      rm.innerHTML = '&times;';
      rm.setAttribute('aria-label', 'Remove ' + p.label);
      rm.addEventListener('click', () => removePoint(i));
      chip.appendChild(rm);

      attachmentsBox.appendChild(chip);
    });
  }

  if (pickBtn) {
    pickBtn.addEventListener('click', startPicking);
    // Leaving the page with the crosshair still on would strand it there.
    window.addEventListener('pagehide', () => { if (picking) cancelPicking(); });
  }
  const pickHintClose = document.getElementById('pick-hint-close');
  if (pickHintClose) pickHintClose.addEventListener('click', dismissPickHint);
  restorePoints();

  // By name, not by whether a thumbnail could be made: an image dragged in
  // from somewhere else on the computer has no thumbnail, because the endpoint
  // that serves those is confined to the attachments folder. The AI reads the
  // file itself and is under no such restriction.
  const IMAGE_EXTS = /\.(png|jpe?g|gif|webp|bmp|tiff?)$/i;
  function looksLikeImage(a) {
    return !a.isDir && (!!a.thumb || IMAGE_EXTS.test(a.name || '') || IMAGE_EXTS.test(a.path || ''));
  }

  function attachedImagePaths() {
    return attachments.filter(looksLikeImage).map((a) => a.path);
  }

  // Below whatever the user typed rather than above it. "Make this bigger" is
  // the message; the element is the footnote that says which "this". Putting
  // the footnote first buries the sentence that carries the actual request.
  function messageWithPoints(base) {
    if (!points.length) return base;
    const text = (base || '').trim();
    const blocks = points.map((p) => p.description).join('\n\n');
    return text ? text + '\n\n---\n\n' + blocks : blocks;
  }

  function messageWithAttachments(base) {
    base = messageWithPoints(base);
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
  // Set by Enter on the Talk button, answered in finishDictation().
  let sendWhenDictationEnds = false;

  /* The microphone being open is the other reason voice takes the floor: a tick
   * while dictation runs is not merely heard over the user, it is recorded and
   * handed to a transcriber as though it were a word they said.
   *
   * Everything that starts or stops listening goes through here, because there
   * are three ways out of dictation and only one of them is the tidy one. The
   * guard makes it safe to call twice, which two of those three do. */
  function setListening(on) {
    if (on === listening) return;
    listening = on;
    if (on) holdSoundsForVoice();
    else releaseSoundsForVoice();
  }
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
    /* Enter sends, from the Talk button as much as from the message box.
     *
     * You press Talk, you say your piece, and then the obvious key does the
     * obvious thing -- except it did not: focus was still on Talk, so Enter
     * activated the button and toggled the microphone back on. The only way
     * out was to find Send, which for somebody navigating by keyboard means
     * tabbing past every control on the row, and for somebody who cannot see
     * the screen means knowing it is there at all.
     *
     * Space still toggles, which is what Space does to a button everywhere. */
    micBtn.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      if (!listening) { form.requestSubmit(); return; }
      // Stop first, and send once the last words have actually arrived --
      // sending here would cut off the end of the sentence.
      sendWhenDictationEnds = true;
      stopDictation();
    });
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
      setListening(true);
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
      setListening(false);
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
    setListening(false);
    if (ws) { ws.onmessage = null; try { ws.close(); } catch (e) {} ws = null; }
    teardownAudio();
    window.__sttRecorder = null;
    if (micBtn) micBtn.classList.remove('recording');
    if (micLabel) micLabel.textContent = 'Talk';
    textarea.placeholder = 'Type or speak your message';
  }

  function finishDictation() {
    setListening(false);
    if (ws) { try { ws.close(); } catch (e) {} ws = null; }
    teardownAudio();
    window.__sttRecorder = null;
    if (micBtn) micBtn.classList.remove('recording');
    if (micLabel) micLabel.textContent = 'Talk';
    textarea.placeholder = 'Type or speak your message';
    // The composer's own line only. What the assistant is doing lives in the
    // conversation now and is not this function's business either way.
    clearStatus();
    resumePending();
    // Deliberately do NOT focus the textarea here: if the user toggled dictation
    // with Space (while not typing), stealing focus back would make the next
    // Space type a literal space instead of toggling again.
    textarea.scrollTop = textarea.scrollHeight;
    // Both routes out of dictation land here, and both land here only once the
    // transcript has been written into the box -- which is why "send when this
    // is done" is answered from here rather than from the key that asked.
    if (sendWhenDictationEnds) {
      sendWhenDictationEnds = false;
      setTimeout(() => form.requestSubmit(), 0);
    }
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

  /* Pick a running turn back up after a reload.
   *
   * This used to watch the stream and do nothing with it but set a status. So
   * refreshing mid-turn threw away everything the assistant had said and done
   * since the last thing it had saved -- and since it saves a round at a time,
   * that is the whole round you were watching. You were left with a spinner and
   * no account of the work, and the words only came back when the turn ended.
   *
   * It now replays the same events through the same handler a live turn uses,
   * so what you get back is what you would have seen. The server sends its
   * whole buffer for the run and marks the point it last saved to the database;
   * everything up to that mark is already on the page, drawn by the server, so
   * it is skipped and the rest is drawn here.
   */
  async function reattach(afterDrop) {
    let reached = false;
    try {
      const state = await fetch('/api/sessions/' + sessionId + '/state').then((r) => r.json());
      // Whether the app answered at all, which is a different question from
      // whether there was anything to watch. `recover` above keeps trying until
      // this is true, so it must not be set before the app has actually spoken.
      reached = true;
      if (!state.running) {
        // Nothing live to watch. On a page load that is the ordinary case and
        // there is nothing to do -- the server has just drawn the whole log.
        // After a stream dropped mid-turn it means the turn finished while
        // this page could not see it, so the end of it is missing from what is
        // on screen and has to be fetched.
        if (afterDrop) reloadMessages();
        return reached;
      }
      beginTurn();
      // The server has already dropped everything it had written to the
      // database by the time we asked, so what arrives is exactly the part the
      // page is missing. Straight through the ordinary handler.
      const resp = await fetch('/api/sessions/' + sessionId + '/attach');
      await readSSE(resp, (ev) => {
        if (ev.type === 'stream_end') { reloadMessages(); return; }
        handleEvent(ev);
      });
      // As above: this stream can end without saying so too, and `beginTurn`
      // has already put the page into its working state.
      if (running) endTurn();
    } catch (e) {
      // Whatever is on screen stays. But the page must not be left waiting on a
      // connection that has gone.
      if (running) endTurn();
    }
    return reached;
  }

  /* Rebuild the log from the server's own rendering of it.
   *
   * This used to walk the raw rows and draw them itself, which made it a third
   * renderer of the same conversation -- and it left out everything that is not
   * a message. So finishing a turn you had re-attached to replaced a log with
   * the tool work and the thinking in it with one that had neither: the account
   * of the turn vanished at the moment the turn ended.
   */
  async function reloadMessages() {
    try {
      const html = await fetch('/sessions/' + sessionId + '/body').then((r) => r.text());
      messages.innerHTML = html;
      dressMessages(messages);
    } catch (e) { /* leave what is on screen rather than blanking it */ }
    endTurn();
    scrollToBottom();
  }

  /* Everything the server's markup needs done to it in the browser: markdown
   * rendered, link cards fetched, and the copy and read-aloud buttons put in
   * the corner of each bubble.
   *
   * One function, taking a root, because the same markup arrives two ways --
   * with the page, and fetched again when the log has to be rebuilt -- and the
   * second way used to be a hand-written second renderer that quietly left out
   * the tool work and the thinking.
   */
  function dressMessages(root) {
    root.querySelectorAll('.content[data-markdown], .summary-text[data-markdown]')
      .forEach((el) => { renderFinal(el); addLinkPreviews(el); });
    // Copy is inserted last so it comes first in the DOM (and thus first in tab
    // order); the play button sits to its left visually.
    root.querySelectorAll('.message .bubble').forEach((el) => {
      addPlayButton(el);
      addCopyButton(el);
    });
  }

  dressMessages(document);
  /* A link the assistant wrote, pressed by the user.
   *
   * "Your site is running at http://localhost:8123" is an ordinary thing for it
   * to write and a perfectly reasonable thing to press. It used to open the
   * user's normal browser -- which, if the project had since been stopped,
   * showed a connection error and nothing else. Somebody who is not technical
   * has no way to know the page is fine and the server is off, let alone that
   * the fix is to find Play and press that first.
   *
   * So a link to this machine is not really a link: it is another way of saying
   * "show me my project", and it now does what Play does. Anything pointing out
   * to the web goes to the user's own browser, which is where a link out
   * belongs -- and in child mode, nowhere.
   *
   * Doing it here rather than by telling the assistant not to write addresses:
   * sometimes it genuinely needs to, a rule it has to remember is a rule it
   * will forget, and this is the app's job either way. */
  messages.addEventListener('click', async (e) => {
    const link = e.target.closest('a[href^="http"]');
    if (!link) return;
    e.preventDefault();
    const url = link.href;
    link.classList.add('link-opening');
    try {
      const resp = await fetch('/api/sessions/' + sessionId + '/open-link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
      const data = await resp.json().catch(() => null);
      if (!resp.ok) {
        showError((data && data.detail) || 'That link would not open.');
        return;
      }
      if (data.where === 'project') {
        announce(data.started
          ? 'Your project has been started, and is open in its own window.'
          : 'Your project is open in its own window.');
        refreshPlay();
      } else {
        announce('Opened in your browser.');
      }
    } catch (err) {
      showError('That link would not open.');
    } finally {
      link.classList.remove('link-opening');
    }
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

  /* Money, for somebody who is not counting in fractions of a cent. Four
   * decimal places is what the arithmetic produces and not what anyone can
   * read; below a penny the exact figure is noise, and saying so is the useful
   * answer. */
  function money(dollars) {
    if (!(dollars > 0)) return 'Free';
    if (dollars < 0.01) return 'under a cent';
    if (dollars < 1) {
      const cents = Math.round(dollars * 100);
      return cents + (cents === 1 ? ' cent' : ' cents');
    }
    return '$' + dollars.toFixed(2);
  }

  function renderModelMenu() {
    if (!modelData || !modelMenu) return;
    modelMenu.innerHTML = '';
    /* Said before the click rather than after it. Changing AI part-way through
     * a conversation is not free -- the new one has never seen any of it -- and
     * a menu that looks like a free preference setting is a menu that teaches
     * people it is one. */
    const note = document.createElement('div');
    note.className = 'model-menu-note';
    note.textContent = modelData.pending_name
      ? modelData.pending_name + ' takes over the next time this conversation '
        + 'is shortened. Pick again to change that.'
      : 'Changing AI mid-conversation costs something — the new one has to read '
        + 'everything so far. You will be told how much before anything happens.';
    modelMenu.appendChild(note);
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

  /* What switching would cost, and the three ways to pay it.
   *
   * This used to be a yes/no, and the app quietly picked whichever of the two
   * routes was cheaper without saying either number. That is a fine default and
   * the wrong amount of information: on a long conversation the bill can be
   * more than a day's ordinary use, and it turned up later on a statement
   * nobody was reading. It also made "I would rather use the other one" cost
   * money, when waiting for the next tidy-up costs nothing at all.
   *
   * The dialog opens before the quote arrives. Asking the server first would
   * mean a click that appears to do nothing for a moment, which on a slow
   * machine reads as a broken button. */
  async function chooseModel(m) {
    if (!modelData || m.id === modelData.current_model) return;
    pendingModel = m;
    const text = document.getElementById('model-warning-text');
    const tidy = document.getElementById('model-choice-tidy');
    const now = document.getElementById('model-choice-now');
    document.getElementById('model-switch-title').textContent =
      'Switch to ' + m.name + '?';
    text.textContent = 'Working out what that would cost…';
    tidy.hidden = true;
    now.disabled = true;
    document.getElementById('model-cost-now').textContent = '';
    document.getElementById('model-cost-tidy').textContent = '';
    window.__openModal(modelWarning, document.getElementById('model-switch-cancel'));

    let quote = null;
    try {
      quote = await fetch('/api/sessions/' + sessionId + '/model/quote?model='
        + encodeURIComponent(m.id)).then((r) => (r.ok ? r.json() : null));
    } catch (e) {}
    if (!pendingModel || pendingModel.id !== m.id) return;  // cancelled meanwhile

    now.disabled = false;
    if (!quote) {
      /* No numbers is not a reason to block the switch -- it is a reason to
       * stop pretending to know. */
      text.textContent = 'This conversation has to be read from the start by '
        + m.name + ', and that costs something. I could not work out how much.';
      document.getElementById('model-cost-now').textContent = '';
      return;
    }
    /* In words, not tokens. "250,000 tokens" is a unit this app's users have
     * no feel for, and the whole point of the sentence is to give them one.
     * Roughly three words to four tokens, which is close enough to be honest
     * and is why it says "about". */
    const words = Math.round(quote.context_tokens * 0.75);
    text.textContent = words < 2000
      ? 'This conversation is still short, so ' + m.name + ' can read it from '
        + 'the start for very little.'
      : 'This conversation has grown to about '
        + words.toLocaleString() + ' words, and ' + m.name
        + ' has not seen any of it yet.';
    document.getElementById('model-cost-now').textContent = money(quote.direct_cost);
    if (quote.can_tidy) {
      document.getElementById('model-cost-tidy').textContent = money(quote.compact_cost);
      tidy.hidden = false;
    }

    /* How far off the free option is. "It'll switch later" is not something
     * anybody can plan around; "about 40,000 words from now" is, and it is the
     * difference between waiting deliberately and wondering whether you have
     * to do something. */
    const why = document.getElementById('model-later-why');
    if (why && !why.dataset.base) why.dataset.base = why.textContent.trim();
    if (why) {
      const left = Math.round((quote.tokens_until_shortened || 0) * 0.75);
      why.textContent = why.dataset.base + (left > 500
        ? ' That is about ' + left.toLocaleString() + ' more words away.'
        : ' That is due very soon.');
    }
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
    if (cancel) cancel.addEventListener('click', () => {
      pendingModel = null;
      parentPassword = null;
      window.__closeModal();
    });
    ['now', 'tidy', 'later'].forEach((how) => {
      const button = document.getElementById('model-choice-' + how);
      if (button) button.addEventListener('click', () => switchTo(how));
    });
    async function switchTo(how) {
      if (!pendingModel) return;
      const m = pendingModel;
      pendingModel = null;
      window.__closeModal();
      setStatus(how === 'later'
        ? 'Setting ' + m.name + ' to take over later…'
        : 'Switching to ' + m.name + '…');
      // Spent on this one switch, whether or not it works. The next one asks
      // again, so a child cannot follow a parent's change with their own.
      const password = parentPassword;
      parentPassword = null;
      try {
        const resp = await fetch('/api/sessions/' + sessionId + '/model', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model: m.id, how, parent_password: password || '' }),
        });
        if (!resp.ok) {
          let msg = 'Could not switch model.';
          try { const d = await resp.json(); if (d && d.detail) msg = d.detail; } catch (e) {}
          clearStatus();
          showError(msg);
          return;
        }
        let finished = false;
        let queued = false;
        let failure = '';
        await readSSE(resp, (ev) => {
          if (ev.type === 'switch_status') {
            setStatus(ev.phase === 'compacting'
              ? 'Shortening the conversation…'
              : 'Switching to ' + m.name + '…');
          } else if (ev.type === 'switch_done') {
            finished = true;
          } else if (ev.type === 'switch_queued') {
            queued = true;
          } else if (ev.type === 'error') {
            // The stream has already sent 200 by this point, so a failure can
            // only arrive as an event. Without this the real reason was
            // discarded and every failure read as "please try again".
            failure = ev.message || '';
          }
        });
        if (queued) {
          /* Nothing has changed yet, so no reload -- the conversation carries
           * on with the AI it has. Said out loud because a choice whose whole
           * point is that nothing happens now needs some sign that it landed,
           * and the menu note carries it from here on. */
          clearStatus();
          if (modelData) {
            modelData.pending_name = m.name;
            renderModelMenu();
          }
          announce(m.name + ' will take over when the conversation is next shortened.');
          setStatus(m.name + ' takes over when this conversation is next shortened.');
          setTimeout(clearStatus, 8000);
        } else if (finished) {
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
    }
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
      if (zoomOut) zoomOut.addEventListener('click', () => window.__saveZoom(window.__readZoom() - 0.1));
      if (zoomIn) zoomIn.addEventListener('click', () => window.__saveZoom(window.__readZoom() + 0.1));
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
  recoverPermission();
})();
