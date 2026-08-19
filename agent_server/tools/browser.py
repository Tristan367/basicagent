"""The `browser` tool: run a sequence of actions against a page and report.

One tool instead of eight. A flow is a flow -- go to the page, fill the form,
click submit, check what happened -- and doing that as four separate tool calls
costs four model round trips and four full-context re-reads to learn what the
first one did.

Every step is reported with its outcome, whatever the model asked for is
checked rather than described, and when something fails the things needed to
diagnose it (console, accessibility tree, screenshot) are gathered without
being asked.
"""

import json
import re
from pathlib import Path

from agent_server import browser as engine
from agent_server.browser import locate as _locate
from agent_server.tools.base import ToolContext, ToolResult

MAX_STEPS = 24

# Actions that name a target element. `press` is deliberately not here: without
# `at` it goes to the page's keyboard, which is the only way to send Escape or
# Tab to whatever has focus -- and requiring `at` made that branch unreachable.
_TARGETED = {"click", "fill", "hover", "select", "check", "uncheck", "upload"}


async def browser(
    ctx: ToolContext,
    *,
    steps: list | None = None,
    width: int = 0,
    height: int = 0,
    stop_on_error: bool = True,
    reset: bool = False,
    **_,
) -> ToolResult:
    # Validate before doing anything. `reset` used to run first, so a call that
    # was about to be rejected still threw away the session's cookies and
    # history -- losing a login the model then had to redo.
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except json.JSONDecodeError:
            return ToolResult.error("`steps` must be a list of objects, not a string", "browser")
    if steps is not None and not isinstance(steps, list):
        return ToolResult.error("`steps` must be a list of objects", "browser")
    # After coercion, so a literal "[]" is caught as empty rather than running
    # zero steps and reporting success.
    if not steps:
        return ToolResult.error(
            "`steps` is empty. Give at least one, e.g. "
            '[{"action": "goto", "url": "http://localhost:3000"}, {"action": "snapshot"}]',
            "browser",
        )
    if len(steps) > MAX_STEPS:
        return ToolResult.error(
            f"{len(steps)} steps is more than the {MAX_STEPS} allowed in one call. "
            "Split the flow -- the browser keeps its state between calls.",
            "browser",
        )

    if reset:
        await engine.reset_session(ctx.session_id)

    try:
        session = await engine.get_session(ctx.session_id, width, height)
    except engine.BrowserError as e:
        return ToolResult.error(str(e), "browser")

    async with session.lock:
        return await _run(ctx, session, steps, stop_on_error)


async def _run(ctx, session, steps, stop_on_error) -> ToolResult:
    import time

    lines: list[str] = []
    frames: list[str] = []
    failed_at = 0

    for number, step in enumerate(steps, 1):
        if ctx.abort.is_set():
            lines.append(f"{number:>2}. (stopped by user)")
            break
        if not isinstance(step, dict):
            lines.append(f"{number:>2}. invalid step: expected an object, got {type(step).__name__}")
            failed_at = number
            break

        action = str(step.get("action") or "").strip().lower()
        label = _label(action, step)
        began = time.monotonic()

        try:
            detail = await _perform(ctx, session, action, step)
            ok = True
        except engine.BrowserError as e:
            detail, ok = str(e), False
        except Exception as e:
            detail, ok = engine._brief(e), False

        elapsed = int((time.monotonic() - began) * 1000)
        status = "ok" if ok else "FAILED"
        lines.append(f"{number:>2}. {label:<52} {status:>6}  {elapsed:>5}ms")

        if isinstance(detail, dict):
            if detail.get("frame"):
                frames.append(detail["frame"])
                lines.append(f"      -> {detail['frame']}")
            if detail.get("frames"):
                frames.extend(detail["frames"])
                for path in detail["frames"]:
                    lines.append(f"      -> {path}")
            if detail.get("text"):
                lines.append(_indent(detail["text"]))
        elif detail:
            lines.append(_indent(str(detail)))

        # Console output is attributed to the step that provoked it.
        for entry in session.fresh():
            lines.append(f"      | {entry.render()}")

        if not ok:
            failed_at = number
            if stop_on_error:
                break

    report = await _report(ctx, session, lines, frames, failed_at, len(steps))
    title = _title(session, steps, failed_at)
    return ToolResult(output=report, is_error=bool(failed_at), title=title)


# ── Actions ─────────────────────────────────────────────────────────────────

async def _perform(ctx, session, action: str, step: dict):
    page = session.page
    at = str(step.get("at") or "")

    if action in _TARGETED and not at:
        raise engine.BrowserError(f"`{action}` needs `at` (what to act on)")
    target = _locate(page, at) if at else None
    timeout = int(step.get("timeout_ms") or 10_000)

    if action == "goto":
        url = str(step.get("url") or "")
        if not url:
            raise engine.BrowserError("`goto` needs `url`")
        # `until` is the documented spelling; `wait` was read here but never
        # appeared in the schema, so asking goto to wait for networkidle -- the
        # documented way -- silently did nothing.
        await page.goto(
            url,
            wait_until=step.get("until") or step.get("wait") or "load",
            timeout=int(step.get("timeout_ms") or 30_000),
        )
        return {"text": f"at {page.url}"}

    if action == "click":
        await target.click(timeout=timeout, button=step.get("button") or "left",
                           click_count=int(step.get("count") or 1))
        return ""

    if action == "fill":
        await target.fill(str(step.get("text") or ""), timeout=timeout)
        return ""

    if action == "press":
        key = str(step.get("key") or step.get("text") or "Enter")
        await (target.press(key, timeout=timeout) if target else page.keyboard.press(key))
        return ""

    if action == "hover":
        await target.hover(timeout=timeout)
        return ""

    if action == "select":
        value = step.get("value")
        await target.select_option(value if isinstance(value, list) else str(value or ""),
                                   timeout=timeout)
        return ""

    if action in ("check", "uncheck"):
        await (target.check(timeout=timeout) if action == "check"
               else target.uncheck(timeout=timeout))
        return ""

    if action == "upload":
        paths = step.get("paths") or ([step["path"]] if step.get("path") else [])
        resolved = [str(ctx.resolve(p)) for p in paths]
        missing = [p for p in resolved if not Path(p).exists()]
        if missing:
            raise engine.BrowserError(f"file not found: {', '.join(missing)}")
        await target.set_input_files(resolved, timeout=timeout)
        return {"text": f"uploaded {len(resolved)} file(s)"}

    if action == "scroll":
        to = step.get("to", "bottom")
        if target is not None:
            await target.scroll_into_view_if_needed(timeout=timeout)
        elif to == "top":
            await page.evaluate("window.scrollTo(0, 0)")
        elif to == "bottom":
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        else:
            try:
                pixels = int(to)
            except (TypeError, ValueError):
                raise engine.BrowserError(
                    f"`scroll` takes to: top, bottom, or a number of pixels -- not {to!r}"
                ) from None
            await page.evaluate(f"window.scrollBy(0, {pixels})")
        return ""

    if action == "wait":
        if at:
            state = step.get("state") or "visible"
            await target.first.wait_for(state=state, timeout=timeout)
            return {"text": f"{at} is {state}"}
        if step.get("until"):
            await page.wait_for_load_state(step["until"], timeout=timeout)
            return {"text": f"load state {step['until']}"}
        await page.wait_for_timeout(min(int(step.get("ms") or 500), 15_000))
        return ""

    if action in ("back", "forward", "reload"):
        await getattr(page, "go_back" if action == "back"
                      else "go_forward" if action == "forward" else "reload")()
        return {"text": f"at {page.url}"}

    if action == "resize":
        await page.set_viewport_size({
            "width": int(step.get("width") or engine.DEFAULT_VIEWPORT[0]),
            "height": int(step.get("height") or engine.DEFAULT_VIEWPORT[1]),
        })
        return ""

    if action == "snapshot":
        tree = await engine.snapshot(session, at)
        return {"text": _cap(tree, 6000)}

    if action == "eval":
        js = str(step.get("js") or "")
        if not js:
            raise engine.BrowserError("`eval` needs `js`")
        value = await page.evaluate(js)
        return {"text": _cap(json.dumps(value, default=str, indent=2, ensure_ascii=False), 3000)}

    if action == "network":
        return {"text": _network_report(session, step)}

    if action == "shoot":
        path, _data = await engine.capture(
            session, ctx.session_id, at=at, full_page=bool(step.get("full_page"))
        )
        return {"frame": path}

    if action == "record":
        count = max(2, min(int(step.get("count") or 4), engine.MAX_FRAMES))
        interval = max(0, min(int(step.get("interval_ms") or 400), 5_000))
        shots = []
        for index in range(count):
            if index:
                await session.page.wait_for_timeout(interval)
            shots.append(await engine.capture(session, ctx.session_id, at=at))
        return {"frames": [p for p, _ in shots]}

    if action == "expect":
        return await _expect(session, step, at, timeout)

    known = ("goto click fill press hover select check uncheck upload scroll wait "
             "back forward reload resize snapshot eval network shoot record expect")
    raise engine.BrowserError(f"unknown action '{action}'. Available: {known}")


def _network_report(session, step: dict) -> str:
    """The page's recent requests, newest last.

    Every request and response is captured, so `network` is the tab that says
    whether a click actually hit the endpoint, and with what status. `filter`
    narrows to matching URLs (a substring, or `/regex/`), and `count` limits the
    number shown.
    """
    entries = session.network
    filt = str(step.get("filter") or "")
    if filt:
        if len(filt) >= 2 and filt[0] == filt[-1] == "/":
            pattern = re.compile(filt[1:-1])
            entries = [e for e in entries if pattern.search(e.url)]
        else:
            low = filt.lower()
            entries = [e for e in entries if low in e.url.lower()]

    count = max(1, min(int(step.get("count") or 200), 500))
    shown = entries[-count:]
    lines = [e.render() for e in shown]
    if len(entries) > len(shown):
        lines.insert(0, f"... ({len(entries) - len(shown)} earlier requests omitted)")
    return "\n".join(lines) if lines else "(no requests recorded yet)"


async def _expect(session, step: dict, at: str, timeout: int):
    """An assertion. This is what makes the tool a test rather than a look.

    A model cannot narrate its way past a failed assertion, which is the only
    thing that makes "verify before you claim it works" enforceable.
    """
    page = session.page

    if step.get("visible") or (at and "visible" not in step and _bare(step)):
        selector = str(step.get("visible") or at)
        await _locate(page, selector).first.wait_for(state="visible", timeout=timeout)
        return {"text": f"{selector} is visible"}

    if step.get("hidden"):
        selector = str(step["hidden"])
        await _locate(page, selector).first.wait_for(state="hidden", timeout=timeout)
        return {"text": f"{selector} is hidden"}

    if "text" in step:
        wanted = str(step["text"])
        scope = _locate(page, at) if at else page.locator("body")
        body = await scope.inner_text(timeout=timeout)
        if wanted not in body:
            raise engine.BrowserError(
                f"expected the text {wanted!r} but it is not on the page. "
                f"Visible text begins: {_cap(body.strip(), 300)!r}"
            )
        return {"text": f"found {wanted!r}"}

    if "url" in step:
        wanted = str(step["url"])
        if wanted not in page.url:
            raise engine.BrowserError(f"expected a URL containing {wanted!r}, but it is {page.url}")
        return {"text": f"url is {page.url}"}

    if "count" in step:
        if not at:
            raise engine.BrowserError("`expect count` needs `at` -- how many of what?")
        wanted = int(step["count"])
        found = await _locate(page, at).count()
        if found != wanted:
            raise engine.BrowserError(f"expected {wanted} of {at}, found {found}")
        return {"text": f"{found} matched"}

    if step.get("console_clean"):
        problems = session.errors()
        if problems:
            listed = "\n".join(f"  {p.render()}" for p in problems[:10])
            raise engine.BrowserError(
                f"the console reported {len(problems)} problem(s):\n{listed}"
            )
        return {"text": "console is clean"}

    raise engine.BrowserError(
        "`expect` needs one of: visible, hidden, text, url, count, console_clean"
    )


def _bare(step: dict) -> bool:
    return not any(k in step for k in ("hidden", "text", "url", "count", "console_clean"))


# ── Reporting ───────────────────────────────────────────────────────────────

async def _report(ctx, session, lines, frames, failed_at, total) -> str:
    parts = ["\n".join(lines)]

    if failed_at:
        # Gathered without being asked, because a failure the model has to make
        # three more calls to understand is a failure reported badly.
        parts.append(await _diagnostics(ctx, session, frames))

    parts.append(f"Page: {session.page.url}")
    if not failed_at:
        parts.append(f"{total} step(s) completed.")
    return "\n\n".join(p for p in parts if p)


async def _diagnostics(ctx, session, frames) -> str:
    chunks = ["--- diagnostics for the failed step ---"]

    problems = session.errors()
    if problems:
        chunks.append("Console problems:\n" + "\n".join(
            f"  {p.render()}" for p in problems[-10:]
        ))

    try:
        tree = await engine.snapshot(session)
        chunks.append("Accessibility tree:\n" + _indent(_cap(tree, 3000)))
    except engine.BrowserError:
        pass

    try:
        path, _ = await engine.capture(session, ctx.session_id)
        frames.append(path)
        chunks.append(f"Screenshot at failure: {path}")
    except engine.BrowserError:
        pass

    return "\n\n".join(chunks)


def _label(action: str, step: dict) -> str:
    at = step.get("at") or ""
    if action == "goto":
        return f"goto {step.get('url', '')}"
    if action == "fill":
        return f"fill {at} = {str(step.get('text', ''))[:24]!r}"
    if action == "expect":
        for key in ("visible", "hidden", "text", "url", "count", "console_clean"):
            if key in step:
                return f"expect {key} {str(step[key])[:34]}"
        return f"expect visible {at[:34]}"
    if action == "eval":
        return f"eval {str(step.get('js', ''))[:40]}"
    if action == "record":
        return f"record {step.get('count', 4)} frames"
    return f"{action} {at}"[:52]


def _title(session, steps, failed_at) -> str:
    actions = ", ".join(dict.fromkeys(
        str(s.get("action", "?")) for s in steps if isinstance(s, dict)
    ))
    outcome = f" (failed at step {failed_at})" if failed_at else ""
    return f"{actions[:60]}{outcome}"


def _indent(text: str, prefix: str = "      ") -> str:
    return "\n".join(prefix + line for line in str(text).splitlines())


def _cap(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (+{len(text) - limit:,} more characters)"


async def close_browser():
    await engine.close_all()
