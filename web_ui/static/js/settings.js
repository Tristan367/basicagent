/* The Settings page's own behaviour.
 *
 * Lifted out of the template unchanged. It had no template variables in it, so
 * living inside `settings.html` bought nothing and cost three things: the
 * browser could not cache it, `node --check` never saw it, and four hundred
 * lines of JavaScript sat where nobody looks for JavaScript.
 */
let renameId = null;
let deleteId = null;

const renameModal = document.getElementById('rename-modal');
const renameInput = document.getElementById('rename-input');
document.addEventListener('click', (e) => {
    const rb = e.target.closest('[data-rename]');
    if (rb) {
        renameId = rb.dataset.rename;
        renameInput.value = rb.dataset.name || '';
        window.__openModal(renameModal, renameInput);
        renameInput.select();
    }
});
function closeRenameModal() {
    renameId = null;
    window.__closeModal();
}
document.getElementById('rename-cancel').addEventListener('click', closeRenameModal);
document.getElementById('rename-confirm').addEventListener('click', async () => {
    const name = renameInput.value.trim();
    if (!name || !renameId) return;
    await fetch('/api/sessions/' + renameId, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
    });
    location.reload();
});
renameInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') document.getElementById('rename-confirm').click();
});

const deleteModal = document.getElementById('delete-modal');
document.addEventListener('click', (e) => {
    const db = e.target.closest('[data-delete]');
    if (db) {
        deleteId = db.dataset.delete;
        const name = db.dataset.name || '';
        document.getElementById('delete-text').textContent =
            'Remove "' + name + '" from your projects? Your files are not deleted.';
        window.__openModal(deleteModal, document.getElementById('delete-cancel'));
    }
});
function closeDeleteModal() {
    deleteId = null;
    window.__closeModal();
}
document.getElementById('delete-cancel').addEventListener('click', closeDeleteModal);
document.getElementById('delete-confirm').addEventListener('click', async () => {
    if (!deleteId) return;
    await fetch('/api/sessions/' + deleteId, { method: 'DELETE' });
    location.reload();
});

function currentVersion() {
    const link = document.querySelector('link[href*="/static/css/style.css"]');
    if (!link) return null;
    const m = link.href.match(/[?&]v=([^&]+)/);
    return m ? m[1] : null;
}

const restartBtn = document.getElementById('restart-btn');
if (restartBtn) restartBtn.addEventListener('click', async () => {
    restartBtn.disabled = true;
    restartBtn.textContent = 'Restarting...';
    const oldVersion = currentVersion();
    try { await fetch('/api/restart', { method: 'POST' }); } catch (e) {}
    // Wait for the new server to come back (a fresh APP_VERSION), then reload.
    for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 500));
        try {
            const data = await fetch('/api/theme', { cache: 'no-store' }).then((r) => r.json());
            if (data.version && data.version !== oldVersion) {
                location.reload();
                return;
            }
        } catch (e) {}
    }
    location.reload();
});

// Every setting saves itself the moment you change it — no Save buttons.
let saveTimer;
function flashSaved() {
    const toast = document.getElementById('saved-toast');
    if (!toast) return;
    toast.hidden = false;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => { toast.hidden = true; }, 1400);
}
// Hear the cues at the volume currently on the slider, without having to start
// a long job and wait for it to finish just to find out if they are too loud.
(function () {
    const btn = document.getElementById('sound-preview-btn');
    if (!btn) return;
    btn.addEventListener('click', () => {
        const slider = document.querySelector('input[name="sound_volume"]');
        const vol = slider ? parseFloat(slider.value) : 0.4;
        if (window.__previewSounds) window.__previewSounds(vol);
    });
})();

// Most settings save themselves the moment you change them -- there is no Save
// button to hunt for and nothing to forget to press.
//
// A locked form cannot do that. It used to try anyway: in child mode the
// background save came back "locked", nothing on the page said so, and the
// change quietly did not happen. Locked forms hand off to the parent-password
// gate instead, which asks, submits properly, and reloads into a locked page.
document.querySelectorAll('form[data-autosave]').forEach((form) => {
    const locked = () => form.closest('#locked-area[data-locked]');
    const save = async () => {
        if (locked()) {
            if (window.__parentGate) window.__parentGate(form);
            return;
        }
        try {
            const resp = await fetch(form.action, { method: 'POST', body: new FormData(form) });
            if (resp.ok) flashSaved();
        } catch (e) {}
    };
    form.querySelectorAll('input, select, textarea').forEach((el) => {
        el.addEventListener('change', save);
    });
});

// Accent colour: green by default, or a custom colour of the user's choosing.
function applyAccent(hex) {
    const root = document.documentElement;
    const vars = ['--accent', '--accent-btn', '--user-bubble', '--focus', '--accent-dim'];
    if (!hex) {
        vars.forEach((v) => root.style.removeProperty(v));
    } else {
        root.style.setProperty('--accent', hex);
        root.style.setProperty('--accent-btn', hex);
        root.style.setProperty('--user-bubble', hex);
        root.style.setProperty('--focus', hex);
        root.style.setProperty('--accent-dim', 'color-mix(in srgb, ' + hex + ' 18%, transparent)');
    }
}
async function saveAccent(hex) {
    const fd = new FormData();
    fd.append('accent', hex);
    try { await fetch('/_settings/prefs', { method: 'POST', body: fd }); flashSaved(); } catch (e) {}
}
const accentPicker = document.getElementById('accent-picker');
const customSwatch = document.getElementById('custom-swatch');
document.querySelectorAll('.color-swatch').forEach((sw) => {
    sw.addEventListener('click', () => {
        document.querySelectorAll('.color-swatch').forEach((s) => {
            s.classList.remove('active');
            s.setAttribute('aria-pressed', 'false');
        });
        sw.classList.add('active');
        sw.setAttribute('aria-pressed', 'true');
        if (sw.dataset.accent === '') {
            accentPicker.hidden = true;
            applyAccent('');
            saveAccent('');
        } else {
            accentPicker.hidden = false;
            applyAccent(accentPicker.value);
            saveAccent(accentPicker.value);
            accentPicker.click();
        }
    });
});
if (accentPicker) {
    accentPicker.addEventListener('input', () => {
        applyAccent(accentPicker.value);
        saveAccent(accentPicker.value);
    });
}

// Theme buttons switch instantly without reloading, so keyboard focus stays put.
document.querySelectorAll('.theme-choice .theme-opt[data-theme]').forEach((btn) => {
    btn.addEventListener('click', () => {
        const theme = btn.dataset.theme;
        window.__shiftTheme(theme);
        document.querySelectorAll('.theme-choice .theme-opt').forEach((b) => {
            b.classList.toggle('active', b === btn);
            b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
        });
        const fd = new FormData();
        fd.append('theme', theme);
        fetch('/_settings/prefs', { method: 'POST', body: fd }).then(flashSaved).catch(() => {});
    });
});

// Show the speech speed as a number next to its slider (e.g. "1.25x").
(function () {
    const slider = document.querySelector('input[name="tts_speed"]');
    const out = document.getElementById('tts-speed-value');
    if (!slider || !out) return;
    const update = () => {
        const v = parseFloat(slider.value);
        out.textContent = (Number.isFinite(v) ? v : 1.25) + 'x';
    };
    slider.addEventListener('input', update);
    update();
})();

// Preview the selected voice: plays a short spoken sample.
(function () {
    const btn = document.getElementById('voice-preview-btn');
    const select = document.querySelector('select[name="tts_voice"]');
    const speedEl = document.querySelector('input[name="tts_speed"]');
    const volEl = document.querySelector('input[name="tts_volume"]');
    if (!btn || !select) return;
    let current = null;
    const PLAY_HTML = btn.innerHTML;
    const STOP_HTML = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">' +
        '<path fill="currentColor" d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg> Stop';
    function setPreviewState(playing) {
        btn.classList.toggle('playing', playing);
        btn.innerHTML = playing ? STOP_HTML : PLAY_HTML;
        btn.setAttribute('aria-label', playing ? 'Stop preview' : 'Preview this voice');
    }
    function stopPreview() {
        if (current) { try { current.pause(); } catch (e) {} }
        current = null;
        setPreviewState(false);
    }
    btn.addEventListener('click', () => {
        if (current) { stopPreview(); return; }
        const voice = select.value;
        const speed = speedEl ? speedEl.value : '1.25';
        const vol = volEl ? parseFloat(volEl.value) : 0.75;
        const a = new Audio('/api/tts/preview?voice=' + encodeURIComponent(voice) + '&speed=' + encodeURIComponent(speed));
        a.volume = Number.isFinite(vol) ? vol : 0.75;
        const done = () => { if (current === a) { current = null; setPreviewState(false); } };
        a.addEventListener('ended', done);
        a.addEventListener('error', done);
        current = a;
        setPreviewState(true);
        a.play().catch(done);
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && current) stopPreview();
    });
})();

// ── Text size (browser zoom), remembered between launches ─────────────────
(function () {
    const out = document.getElementById('zoom-out');
    const inc = document.getElementById('zoom-in');
    const reset = document.getElementById('zoom-reset');
    if (out) out.addEventListener('click', () => window.__applyZoom(window.__readZoom() - 0.1));
    if (inc) inc.addEventListener('click', () => window.__applyZoom(window.__readZoom() + 0.1));
    if (reset) reset.addEventListener('click', () => window.__applyZoom(1));
})();

// ── Back to top ────────────────────────────────────────────────────────────
(function () {
    const btn = document.getElementById('to-top-btn');
    if (!btn) return;
    btn.addEventListener('click', () => {
        const scroller = document.querySelector('.settings-scroll');
        if (scroller) scroller.scrollTo({ top: 0, behavior: window.__scrollBehavior() });
    });
})();

// ── Parental controls ──────────────────────────────────────────────────────
(function () {
    const modal = document.getElementById('password-modal');
    const confirmInput = document.getElementById('password-confirm-input');
    const title = document.getElementById('password-modal-title');
    const text = document.getElementById('password-modal-text');
    const note = document.getElementById('password-modal-note');
    const err = document.getElementById('password-modal-error');
    const input = document.getElementById('password-input');
    const confirmBtn = document.getElementById('password-confirm');
    const cancelBtn = document.getElementById('password-cancel');
    if (!modal) return;

    let action = null;   // function(password) -> Promise<{ok, reason?}>

    // `showForgotNote` marks the prompts that *set* a password rather than
    // check one, and those are the ones that ask for it twice.
    function openPrompt(titleText, bodyText, showForgotNote, onConfirm) {
        title.textContent = titleText;
        text.textContent = bodyText;
        note.hidden = !showForgotNote;
        err.hidden = true;
        err.textContent = '';
        input.value = '';
        confirmInput.value = '';
        confirmInput.hidden = !showForgotNote;
        action = onConfirm;
        window.__openModal(modal, input);
    }
    function closePrompt() {
        action = null;
        window.__closeModal();
    }
    confirmBtn.addEventListener('click', async () => {
        const password = input.value.trim();
        if (!password) { err.textContent = 'Please type a password.'; err.hidden = false; return; }
        if (!confirmInput.hidden && confirmInput.value.trim() !== password) {
            err.textContent = 'The two passwords are not the same. Try again.';
            err.hidden = false;
            confirmInput.value = '';
            confirmInput.focus();
            return;
        }
        const result = await action(password);
        if (result && result.ok) {
            closePrompt();
            if (result.reload) location.reload();
        } else if (result && result.reason === 'no_key') {
            err.textContent = 'Set up an AI first: once child mode is on, API keys are locked.';
            err.hidden = false;
        } else if (result && result.reason === 'password') {
            err.textContent = 'That password is not right.';
            err.hidden = false;
        }
    });
    cancelBtn.addEventListener('click', closePrompt);

    function post(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        }).then((r) => r.json()).catch(() => ({ ok: false, reason: 'network' }));
    }

    const enableBtn = document.getElementById('child-enable');
    if (enableBtn) enableBtn.addEventListener('click', () => {
        openPrompt('Turn on child mode', 'Choose a parent password. You will need it to change the AI or turn child mode off.', true, async (password) => {
            const r = await post('/api/child/enable', { password });
            if (r.ok) return { ok: true, reload: true };
            return { ok: false, reason: r.reason };
        });
    });

    const disableBtn = document.getElementById('child-disable');
    if (disableBtn) disableBtn.addEventListener('click', () => {
        openPrompt('Turn off child mode', 'Enter the parent password.', false, async (password) => {
            const r = await post('/api/child/disable', { password });
            if (r.ok) return { ok: true, reload: true };
            return { ok: false, reason: r.reason };
        });
    });

    const forgotBtn = document.getElementById('child-forgot');
    if (forgotBtn) forgotBtn.addEventListener('click', async () => {
        const r = await post('/api/child/forgot', {});
        if (r.ok) location.reload();
    });

    const resetBtn = document.getElementById('child-reset');
    if (resetBtn) resetBtn.addEventListener('click', () => {
        openPrompt('Set a new password', 'Choose a new parent password to take back control of child mode.', false, async (password) => {
            const r = await post('/api/child/reset', { password });
            if (r.ok) return { ok: true, reload: true };
            return { ok: false, reason: r.reason === 'waiting' ? 'password' : r.reason };
        });
    });

    // Locked settings: the password is asked for once per change, and buys
    // exactly that change.
    //
    // It used to unlock the whole area for as long as the page stayed open. A
    // parent who changed one thing and walked away left every locked setting
    // open behind them, which is the one moment the lock is actually for: a
    // child can otherwise put it straight back the way it was. So the fields
    // stay editable -- being asked before you are allowed to type is a worse
    // way to find out something is locked -- and the password is asked for on
    // submit, added to that one form, and never held anywhere. Saving reloads
    // the page, so the next change asks again.
    const lockedArea = document.getElementById('locked-area');

    // Ask for the password, then send this one form. Shared with the autosave
    // handler above, which is the other way a locked setting gets changed.
    window.__parentGate = function (form) {
        openPrompt('Parent password',
            'Enter the parent password to make this one change. It will be asked for again next time.',
            false, async (password) => {
                const r = await post('/api/child/verify', { password });
                if (!r.ok) return { ok: false, reason: 'password' };
                let h = form.querySelector('input[name="parent_password"]');
                if (!h) {
                    h = document.createElement('input');
                    h.type = 'hidden';
                    h.name = 'parent_password';
                    form.appendChild(h);
                }
                h.value = password;
                // A real submit, not fetch: the page reloads and comes back
                // locked, so the password exists for one request and then the
                // DOM holding it is gone.
                form.submit();
                return { ok: true };
            });
    };

    if (lockedArea && lockedArea.dataset.locked) {
        lockedArea.querySelectorAll('form').forEach((form) => {
            form.addEventListener('submit', (e) => {
                e.preventDefault();
                window.__parentGate(form);
            });
        });
    }
})();
