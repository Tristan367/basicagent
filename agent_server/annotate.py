"""Pointing at part of the page instead of describing it.

The hardest sentence for a beginner to write is the one that identifies a
thing on screen. "The blue button" -- which blue button, there are four. "The
box at the top" -- the header, the nav, the hero, the search field? An adult
developer solves this by opening the inspector; a child solves it by giving up
and saying "it looks wrong" and hoping.

So: press a button, click the thing, and the description writes itself.

**What comes back, and what does not.** The element itself is always there --
its tag, id, classes, visible text, a selector, its size and the styles that
actually decide how it looks. That much is a fact about the DOM and no
framework can take it away. On top of that, some frameworks volunteer more:

    plain HTML   the page *is* the source, so its text and id find the line
    React 18     component chain, and the source file via `_debugSource`
    Vue 3        component chain, and the source file via `type.__file`
    React 19+    component chain only -- `_debugSource` was removed
    Next.js      the nearest *client* component; server components are not in
                 the browser at all and so cannot appear here

That last row is why this module reports what it found rather than promising
a shape. `source` is either a path or None, and the model is told to search
when it is None -- which is what a developer does with an unfamiliar codebase
anyway. A component name is enough: `grep -rn BuyButton` ends the search.

**Nothing is injected until it is asked for.** The script installs and sits
there inert. Arming it draws the banner and the highlight; picking or pressing
Escape takes them away again. The user's own page is never permanently altered,
which matters because it is a page they are trying to look at.
"""

from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger(__name__)

# How long an armed picker waits before it gives up on its own. Long enough to
# find the thing, short enough that a forgotten arming does not sit there.
PICK_TIMEOUT = 180.0

# Names React and Next put in the fiber tree that are not anyone's component.
# Substrings, because the real ones are `RedirectErrorBoundary`,
# `InnerLayoutRouter`, `SegmentViewNode` and a dozen more of the same shape.
_NOISE = (
    "Boundary", "Provider", "Router", "Suspense", "Fragment", "Root", "Portal",
    "Context", "Consumer", "Node", "Dev", "Hot", "Wrapper", "Anonymous",
    "Handler",
)

PICKER_JS = r"""
(() => {
  if (window.__pickerInstalled) return;
  window.__pickerInstalled = true;

  const NOISE = __NOISE__;
  const TOP = (() => { try { return window.top === window; } catch (e) { return true; } })();

  let armed = false, hi = null, banner = null, styleTag = null, current = null;

  // ── describing an element ────────────────────────────────────────────────

  function selectorFor(el) {
    if (el.id) return '#' + el.id;
    const parts = [];
    let node = el;
    for (let depth = 0; node && node.nodeType === 1 && depth < 5; depth++) {
      let piece = node.tagName.toLowerCase();
      if (node.id) { parts.unshift('#' + node.id); break; }
      const cls = (node.getAttribute('class') || '').trim().split(/\s+/)
        .filter((c) => c && !/^(ng|css|sc|jsx)-|^[a-z]+_[A-Za-z0-9]{4,}$/.test(c))
        .slice(0, 2);
      if (cls.length) piece += '.' + cls.join('.');
      else if (node.parentElement) {
        const sibs = Array.from(node.parentElement.children)
          .filter((s) => s.tagName === node.tagName);
        if (sibs.length > 1) piece += ':nth-of-type(' + (sibs.indexOf(node) + 1) + ')';
      }
      parts.unshift(piece);
      node = node.parentElement;
    }
    return parts.join(' > ');
  }

  /* Three answers, not two. A `<div>` fiber is not a component and the walk
     should step over it; `RedirectErrorBoundary` is a component, but the
     framework's, and the walk should stop there rather than step over it and
     carry on collecting internals for another thirty levels.

     Stopping is what does the real work. Any list of framework names is a list
     of the ones seen so far -- Next alone has dozens and renames them between
     versions -- but they all sit *above* the user's own tree, so the first one
     encountered marks the end of the part anybody cares about. */
  const SKIP = 0, TAKE = 1, STOP = 2;

  function classify(name) {
    if (!name || typeof name !== 'string' || !/^[A-Z]/.test(name)) return SKIP;
    return NOISE.some((n) => name.includes(n)) ? STOP : TAKE;
  }

  /* React keeps its tree on the DOM node under a per-build random suffix.
     Walking *up* from the clicked node's fiber gives the component that owns
     it, then its parent, and so on -- which is the chain a developer reads. */
  function fromReact(el) {
    const key = Object.keys(el).find((k) => k.startsWith('__reactFiber$'));
    if (!key) return null;
    const out = { framework: 'react', components: [], source: null };
    let fiber = el[key];
    for (let i = 0; fiber && i < 40; i++, fiber = fiber.return) {
      const t = fiber.type;
      const name = typeof t === 'function' ? (t.displayName || t.name)
                 : (t && typeof t === 'object' ? (t.displayName || (t.render && t.render.name)) : null);
      const verdict = classify(name);
      if (verdict === STOP && out.components.length) break;
      if (verdict === TAKE && out.components[out.components.length - 1] !== name) {
        out.components.push(name);
      }
      // React 18 and earlier only. React 19 dropped `_debugSource` entirely,
      // so on a current Next app this stays null and the model searches.
      if (!out.source && fiber._debugSource && fiber._debugSource.fileName) {
        out.source = fiber._debugSource.fileName;
      }
      if (out.components.length >= 4 && out.source) break;
    }
    return out.components.length || out.source ? out : null;
  }

  function fromVue(el) {
    let node = el, inst = null;
    for (let i = 0; node && i < 10 && !inst; i++, node = node.parentElement) {
      inst = node.__vueParentComponent || null;
    }
    if (!inst) return null;
    const out = { framework: 'vue', components: [], source: null };
    for (let i = 0; inst && i < 20; i++, inst = inst.parent) {
      const t = inst.type || {};
      const name = t.__name || t.name || t.displayName;
      const verdict = classify(name);
      if (verdict === STOP && out.components.length) break;
      if (verdict === TAKE && out.components[out.components.length - 1] !== name) {
        out.components.push(name);
      }
      if (!out.source && t.__file) out.source = t.__file;
      if (out.components.length >= 4 && out.source) break;
    }
    return out.components.length || out.source ? out : null;
  }

  function describe(el) {
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const framework = fromReact(el) || fromVue(el) || {};
    const clone = el.cloneNode(false);
    return {
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      classes: (el.getAttribute('class') || '').trim(),
      selector: selectorFor(el),
      openTag: clone.outerHTML.replace(/<\/[a-z-]+>$/i, ''),
      html: (el.outerHTML || '').slice(0, 600),
      text: (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 160),
      attrs: ['href', 'src', 'alt', 'placeholder', 'name', 'type', 'aria-label',
              'data-testid'].reduce((acc, a) => {
        const v = el.getAttribute(a);
        if (v) acc[a] = v.slice(0, 120);
        return acc;
      }, {}),
      rect: { w: Math.round(rect.width), h: Math.round(rect.height),
              x: Math.round(rect.left), y: Math.round(rect.top) },
      styles: {
        display: cs.display, position: cs.position,
        fontSize: cs.fontSize, fontWeight: cs.fontWeight, fontFamily: cs.fontFamily,
        color: cs.color, background: cs.backgroundColor,
        padding: cs.padding, margin: cs.margin, border: cs.border,
      },
      framework: framework.framework || null,
      components: framework.components || [],
      source: framework.source || null,
      url: location.href,
      frame: TOP ? '' : location.href,
    };
  }

  // ── the overlay, which exists only while picking ─────────────────────────

  function paint() {
    if (!TOP) return;
    styleTag = document.createElement('style');
    styleTag.textContent =
      '*{cursor:crosshair !important}' +
      '.__pick-ui{position:fixed;z-index:2147483647;pointer-events:none;' +
      'font:14px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}';
    document.documentElement.appendChild(styleTag);

    hi = document.createElement('div');
    hi.className = '__pick-ui';
    hi.style.cssText += ';border:2px solid #2f7cf6;background:rgba(47,124,246,.16);' +
      'border-radius:3px;box-shadow:0 0 0 1px rgba(255,255,255,.7);display:none';
    document.documentElement.appendChild(hi);

    banner = document.createElement('div');
    banner.className = '__pick-ui';
    banner.style.cssText += ';top:0;left:0;right:0;padding:11px 16px;text-align:center;' +
      'background:#2f7cf6;color:#fff;font-weight:600;letter-spacing:.01em';
    banner.textContent = 'Click the part you want to talk about — or press Esc';
    document.documentElement.appendChild(banner);
  }

  function unpaint() {
    [hi, banner, styleTag].forEach((n) => n && n.remove());
    hi = banner = styleTag = null;
  }

  function show(el) {
    current = el;
    if (!hi || !el) return;
    const r = el.getBoundingClientRect();
    hi.style.display = 'block';
    hi.style.top = r.top + 'px';
    hi.style.left = r.left + 'px';
    hi.style.width = r.width + 'px';
    hi.style.height = r.height + 'px';
  }

  // ── arming and disarming ─────────────────────────────────────────────────

  function pick(el) {
    if (!el || el.classList.contains('__pick-ui')) return;
    const payload = describe(el);
    disarm();
    try { window.__annotatePick(payload); } catch (e) {}
  }

  function onMove(e) { if (armed) show(e.target); }

  /* Picking on `mousedown` and stopping there is not enough: `mouseup` and
     `click` follow from the same press, and by then we have disarmed, so they
     reach the page. Point at a link and the page you were pointing at goes
     away underneath you. So the press is swallowed whole -- both remaining
     events, once, and then these come straight back off. */
  function swallow(e) {
    e.preventDefault();
    e.stopPropagation();
    document.removeEventListener(e.type, swallow, true);
  }
  function onDown(e) {
    if (!armed) return;
    e.preventDefault(); e.stopPropagation();
    document.addEventListener('mouseup', swallow, true);
    document.addEventListener('click', swallow, true);
    pick(e.target);
  }
  function onKey(e) {
    if (!armed) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      disarm();
      try { window.__annotatePick(null); } catch (err) {}
      return;
    }
    // Nothing here needs a mouse. Tab walks the focusable elements the way it
    // always does; the arrows walk the tree itself, which is the only way to
    // reach a div that nothing focuses.
    const start = current || document.activeElement || document.body;
    let next = null;
    if (e.key === 'ArrowUp') next = start.parentElement;
    else if (e.key === 'ArrowDown') next = start.firstElementChild;
    else if (e.key === 'ArrowLeft') next = start.previousElementSibling;
    else if (e.key === 'ArrowRight') next = start.nextElementSibling;
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(start); return; }
    else return;
    e.preventDefault();
    if (next && next.nodeType === 1 && !next.classList.contains('__pick-ui')) {
      show(next);
      if (next.scrollIntoView) next.scrollIntoView({ block: 'nearest' });
    }
  }
  function onFocus(e) { if (armed && e.target !== document.body) show(e.target); }
  // Scrolling down to find the thing you want to click is the normal way to
  // use a page. The highlight is positioned in viewport coordinates, so
  // without this it stays behind while the element it is marking moves away.
  function onScroll() { if (armed && current) show(current); }

  function disarm() {
    if (!armed) return;
    armed = false;
    current = null;
    unpaint();
    document.removeEventListener('mousemove', onMove, true);
    document.removeEventListener('mousedown', onDown, true);
    document.removeEventListener('keydown', onKey, true);
    document.removeEventListener('focusin', onFocus, true);
    window.removeEventListener('scroll', onScroll, true);
  }

  window.__pickerArm = function () {
    if (armed) return;
    armed = true;
    paint();
    document.addEventListener('mousemove', onMove, true);
    document.addEventListener('mousedown', onDown, true);
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('focusin', onFocus, true);
    window.addEventListener('scroll', onScroll, true);
  };
  window.__pickerDisarm = disarm;
})();
"""
# A literal substitution rather than `%`-formatting or an f-string: the script
# below is full of braces and percent signs that belong to JavaScript and CSS,
# and every one of them would have to be escaped for the sake of one value.
PICKER_JS = PICKER_JS.replace("__NOISE__", json.dumps(list(_NOISE)))


# One pending pick per project. A second arming replaces the first rather than
# queueing: the button says "point at something", and pressing it twice means
# the user changed their mind about which something.
_pending: dict[str, asyncio.Future] = {}


def deliver(session_id: str, payload: dict | None) -> None:
    """Hand a pick (or a cancellation) to whoever is waiting for it."""
    future = _pending.pop(session_id, None)
    if future is not None and not future.done():
        future.set_result(payload)


def waiting(session_id: str) -> bool:
    return session_id in _pending


async def wait_for_pick(session_id: str) -> dict | None:
    """Block until the user clicks something, presses Escape, or time passes."""
    old = _pending.pop(session_id, None)
    if old is not None and not old.done():
        old.set_result(None)

    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _pending[session_id] = future
    try:
        return await asyncio.wait_for(asyncio.shield(future), PICK_TIMEOUT)
    except (TimeoutError, asyncio.CancelledError):
        return None
    finally:
        if _pending.get(session_id) is future:
            _pending.pop(session_id, None)


def forget(session_id: str) -> None:
    """Drop any pending pick, because the window it belonged to is gone."""
    deliver(session_id, None)


# ── turning a pick into something a model can act on ────────────────────────

def summarise(pick: dict) -> str:
    """The one line the user sees on the chip in their message box."""
    text = (pick.get("text") or "").strip()
    if text:
        return text[:40] + ("…" if len(text) > 40 else "")
    for attr in ("aria-label", "alt", "placeholder", "name"):
        value = (pick.get("attrs") or {}).get(attr)
        if value:
            return value[:40]
    if pick.get("id"):
        return "#" + pick["id"]
    return "<" + (pick.get("tag") or "element") + ">"


def _shorten(path: str, project_dir: str) -> str:
    """A path inside the project, said the way the rest of the app says paths.

    React and Vue both hand back an absolute path from the build machine, which
    is 130 characters of somebody's home directory and then the six that matter.
    Relative to the project it is `src/BuyButton.jsx`, which is also exactly
    what the assistant needs to type to open it.
    """
    if not path or not project_dir:
        return path
    from pathlib import Path

    try:
        return str(Path(path).relative_to(Path(project_dir)))
    except ValueError:
        return path


def describe(pick: dict, project_dir: str = "") -> str:
    """What the model gets: the element, then whatever else was on offer.

    Deliberately flat text rather than JSON. The model reads this alongside a
    sentence the user typed, and a wall of braces in the middle of "make this
    bigger" reads as noise -- which is exactly what it must not be.
    """
    lines = ["The user pointed at this on the page:", ""]
    lines.append("  " + (pick.get("openTag") or "<" + (pick.get("tag") or "?") + ">"))

    text = (pick.get("text") or "").strip()
    if text:
        lines.append(f'  text: "{text}"')

    if pick.get("source"):
        lines.append("  file: " + _shorten(pick["source"], project_dir))
    components = pick.get("components") or []
    if components:
        lines.append("  component: " + " inside ".join(components))
    if components and not pick.get("source"):
        lines.append(f"  (no source file — search the project for {components[0]})")

    lines.append("  selector: " + (pick.get("selector") or "?"))

    rect = pick.get("rect") or {}
    styles = pick.get("styles") or {}
    shape = f"  {rect.get('w', '?')}×{rect.get('h', '?')} px"
    bits = [
        f"font {styles.get('fontSize')}" if styles.get("fontSize") else "",
        f"colour {styles.get('color')}" if styles.get("color") else "",
        f"background {styles.get('background')}" if styles.get("background") else "",
    ]
    trailing = ", ".join(b for b in bits if b)
    lines.append(shape + (", " + trailing if trailing else ""))

    if pick.get("url"):
        lines.append("  page: " + pick["url"])

    return "\n".join(lines)
