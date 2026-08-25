"""The agent loop.

A turn is owned by the server, not by an HTTP request: clients subscribe to the
event stream over SSE, and closing the tab only unsubscribes. The loop persists
every step as it happens so the stored transcript always matches what was sent.

There are no permission prompts — everything is auto-approved. The only hard
stops are the destructive-command guard in `bash` and the doom-loop detector
below, both of which refuse and explain rather than ask.
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

from agent_server import database as db
from agent_server import image_support, jobs, parental
from agent_server.config import MAX_TOOL_RESULT_CHARS
from agent_server.conversation import (
    build_messages,
    normalize_tool_calls,
    parse_arguments,
    pending_tool_calls,
    tool_call_name,
)
from agent_server.providers import Provider, get_provider
from agent_server.providers.base import message_chars, observe_usage
from agent_server.system_prompt import session_system_prompt
from agent_server.tools.base import ToolContext, ToolResult, truncate
from agent_server.tools.registry import (
    allowed_tool_names,
    execute_tool,
    get_tool,
    tool_schemas,
)

log = logging.getLogger(__name__)

MAX_CODE_CHARS = 20_000

# session_id -> abort signal for the in-flight run.
_aborts: dict[str, asyncio.Event] = {}

# Tool calls currently executing, so a stop can interrupt them.
_tool_tasks: dict[str, set[asyncio.Task]] = {}


async def _stoppable(stream: AsyncIterator[dict], abort: asyncio.Event):
    """A provider stream that ends when Stop is pressed, not when it feels like it.

    The abort check used to sit inside the loop over this stream, which meant
    Stop could only be noticed between two events. A provider that accepts the
    request and then sends nothing at all produces no events, so there was
    nothing to check between: the loop sat inside a single `await` for the ten
    minutes of the client's timeout, Stop did nothing at all, and reloading the
    page brought back a Stop button and no way to send. The user's only way out
    was to close an app they were told never needs closing.

    That is not exotic. It is a proxy holding a connection open, a provider
    under load, a captive-portal wifi that swallowed the socket after the
    request went out -- and on a laptop, that is Tuesday.

    So the wait itself races the abort. Whichever finishes first wins, and the
    stream is closed on the way out so the connection goes with it rather than
    being left to the timeout.
    """
    iterator = stream.__aiter__()
    stopped = asyncio.ensure_future(abort.wait())
    try:
        while True:
            # Checked before waiting as well as while waiting. A stream whose
            # next event is already in hand resolves the race instantly and
            # would otherwise deliver one more event after Stop -- including
            # the case where Stop was pressed before the request even left.
            if abort.is_set():
                return
            nxt = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait(
                {nxt, stopped}, return_when=asyncio.FIRST_COMPLETED)
            if nxt not in done:
                nxt.cancel()
                with contextlib.suppress(BaseException):
                    await nxt
                return
            try:
                event = nxt.result()
            except StopAsyncIteration:
                return
            yield event
    finally:
        stopped.cancel()
        with contextlib.suppress(BaseException):
            await stopped
        # Closes the generator, and with it whatever socket it was reading.
        with contextlib.suppress(BaseException):
            await iterator.aclose()


def _track(session_id: str, task: asyncio.Task):
    _tool_tasks.setdefault(session_id, set()).add(task)
    task.add_done_callback(lambda t: _tool_tasks.get(session_id, set()).discard(t))


def request_abort(session_id: str) -> bool:
    # Whatever was handed over goes with it. A command still running after the
    # user has stopped the turn is one nobody asked for any more, and its output
    # arriving later would wake a conversation they had walked away from.
    jobs.cancel(session_id)
    event = _aborts.get(session_id)
    if event is None:
        return False
    event.set()
    for task in list(_tool_tasks.get(session_id, ())):
        task.cancel()
    return True


def is_running(session_id: str) -> bool:
    return session_id in _aborts


# Per-session history of recent tool rounds, for doom-loop detection. Each entry
# is the set of (name, args_json) keys issued on one assistant turn.
_doom_history: dict[str, list[set[tuple[str, str]]]] = {}
_doom_recorded: dict[str, str] = {}
DOOM_ROUNDS = 3
DOOM_ABORT_ROUNDS = 6


class _Run:
    __slots__ = ("done", "events", "inflight", "subscribers", "task")

    def __init__(self):
        self.events: list[dict] = []
        self.subscribers: set[asyncio.Queue] = set()
        self.task: asyncio.Task | None = None
        self.done = asyncio.Event()
        self.inflight: dict[str, dict] = {}


_runs: dict[str, _Run] = {}
_background: set[asyncio.Task] = set()
RUN_RETENTION_SEC = 300
MAX_BUFFERED_EVENTS = 5000

# Messages typed while a turn is running, flushed at the next turn boundary.
_queued: dict[str, list[dict]] = {}
# Sessions already compacted during the current run (don't re-trigger).
_compacted_this_run: set[str] = set()


def queue_message(session_id: str, text: str) -> str | None:
    if active_run(session_id) is None:
        return None
    entry = {"id": uuid.uuid4().hex[:8], "text": text}
    _queued.setdefault(session_id, []).append(entry)
    return entry["id"]


def unqueue_message(session_id: str, queue_id: str) -> str | None:
    entries = _queued.get(session_id) or []
    for index, entry in enumerate(entries):
        if entry["id"] == queue_id:
            entries.pop(index)
            return entry["text"]
    return None


async def _flush_queued(session_id: str) -> list[dict]:
    entries = _queued.pop(session_id, [])
    if not entries:
        return []
    combined = "\n\n".join(entry["text"] for entry in entries)
    return [await db.add_message(session_id, "user", combined)]


def _publish(run: _Run, event: dict):
    if event["type"] == "tool_start":
        run.inflight[event["tool_call_id"]] = event
    elif event["type"] == "tool_end":
        run.inflight.pop(event["tool_call_id"], None)
    run.events.append(event)
    if len(run.events) > MAX_BUFFERED_EVENTS:
        del run.events[: len(run.events) - MAX_BUFFERED_EVENTS]
    for queue in list(run.subscribers):
        queue.put_nowait(event)


async def _drive(session_id: str, handle: _Run, abort: asyncio.Event | None = None):
    try:
        async for event in run(session_id, abort):
            _publish(handle, event)
    except asyncio.CancelledError:
        _publish(handle, {"type": "error", "message": "Run cancelled."})
        raise
    except Exception as e:
        _publish(handle, {"type": "error", "message": f"{type(e).__name__}: {e}"})
    finally:
        _publish(handle, {"type": "stream_end"})
        handle.done.set()
        task = asyncio.create_task(_retire(session_id, handle))
        _background.add(task)
        task.add_done_callback(_background.discard)


async def _retire(session_id: str, handle: _Run):
    await asyncio.sleep(RUN_RETENTION_SEC)
    if _runs.get(session_id) is handle and not handle.subscribers:
        _runs.pop(session_id, None)
    handle.events.clear()
    handle.inflight.clear()


def forget_session(session_id: str):
    """Drop everything held in memory for a session that no longer exists."""
    from agent_server import browser
    from agent_server.system_prompt import clear_env_cache
    from agent_server.tools.file_ops import clear_read_cache

    clear_env_cache(session_id)
    clear_read_cache(session_id)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        task = loop.create_task(browser.close_session(session_id))
        _background.add(task)
        task.add_done_callback(_background.discard)
    run = _runs.pop(session_id, None)
    if run is not None and run.task is not None and not run.task.done():
        run.task.cancel()
    _queued.pop(session_id, None)
    _aborts.pop(session_id, None)
    _tool_tasks.pop(session_id, None)
    _doom_history.pop(session_id, None)
    _doom_recorded.pop(session_id, None)
    _compacted_this_run.discard(session_id)


async def shutdown(timeout: float = 5.0):
    tasks = [r.task for r in _runs.values() if r.task is not None and not r.task.done()]
    for session_id in list(_aborts):
        request_abort(session_id)
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)
    for task in tasks:
        if not task.done():
            task.cancel()
    _runs.clear()


def claim_turn(session_id: str) -> tuple[_Run, asyncio.Event] | None:
    """Take the session for a new turn, or None if a turn already has it.

    Claiming is separate from starting because the caller has to persist the
    user's message in between, and that is an await. The claim used to be made
    on the run task's first step instead -- a whole event-loop turn after the
    caller checked -- so two sends landing in that window (a double-tap on
    Send, a retried request, Enter pressed twice) both saw an idle session. The
    message was stored twice and asked of the model twice, and the second
    handle replaced the first in `_runs`, so the live reply streamed into a
    handle nobody was subscribed to while the user watched "This session is
    already working." and then nothing at all.

    The handle is published here rather than at start, so a second send in that
    same window has a run to queue against and subscribe to. Nothing may await
    between the check and the claim, which is why this is a plain function.
    """
    if session_id in _aborts:
        return None
    abort = asyncio.Event()
    _aborts[session_id] = abort
    handle = _Run()
    _runs[session_id] = handle
    return handle, abort


def release_turn(session_id: str, handle: _Run, abort: asyncio.Event):
    """Give a claim back, for a caller that claimed and then could not start."""
    if _aborts.get(session_id) is abort:
        _aborts.pop(session_id, None)
    if _runs.get(session_id) is handle:
        _publish(handle, {"type": "stream_end"})
        handle.done.set()
        _runs.pop(session_id, None)


async def _collect_background(session_id: str) -> None:
    """Write finished background commands into the conversation.

    Stored as tool results with no call id, which is what they are: nothing
    asked for them at this moment, they simply arrived. The transcript groups
    them with everything else the assistant did, and `build_messages` turns a
    result with nothing to answer into a plain note, because the wire format has
    no place for an unanswered one.
    """
    for job, result in jobs.take_finished(session_id):
        head = (f"`{job.command}` has finished (it took "
                f"{job.seconds:.0f} seconds).")
        await db.add_message(
            session_id, "tool", f"{head}\n\n{result.output}",
            tool_name="bash", tool_title=result.title, is_error=result.is_error,
        )


def wake(session_id: str) -> None:
    """Something the session was waiting on has landed. Go and read it.

    Called from a job's completion callback. A run already going will pick the
    output up at the top of its next round, so this only has to start one when
    the turn has already ended -- which is the whole point of handing the
    command over: the assistant said "I will tell you when it is done" and now
    has to be able to keep that promise.
    """
    if active_run(session_id) is not None:
        return
    log.info("waking %s: background work finished", session_id)
    start_run(session_id)


jobs.set_waker(wake)


def start_claimed_run(session_id: str, handle: _Run, abort: asyncio.Event) -> _Run:
    """Begin the run for a claim taken earlier by `claim_turn`."""
    handle.task = asyncio.create_task(_drive(session_id, handle, abort))
    return handle


def start_run(session_id: str) -> _Run:
    """Claim and start in one step. For callers with nothing to persist."""
    claimed = claim_turn(session_id)
    if claimed is None:
        existing = _runs.get(session_id)
        return existing if existing is not None else _Run()
    return start_claimed_run(session_id, *claimed)


def active_run(session_id: str) -> _Run | None:
    run = _runs.get(session_id)
    return run if run is not None and not run.done.is_set() else None


async def subscribe(
    session_id: str, replay: bool = True, since_last_save: bool = False
) -> AsyncIterator[dict]:
    """This run's events. `replay` includes what has already happened.

    `since_last_save` trims that replay to the part the database does not have
    yet, which is what a browser that reloaded mid-turn needs: everything before
    the last save is already on its page, rendered by the server from the rows.
    Trimmed here rather than by the client, because which events are on disk is
    something this side knows and the other side would have to be told twice.
    """
    run = _runs.get(session_id)
    if run is None:
        yield {"type": "stream_end"}
        return

    queue: asyncio.Queue = asyncio.Queue()
    run.subscribers.add(queue)
    backlog = list(run.events) if replay else []
    if since_last_save:
        saved = [i for i, e in enumerate(backlog) if e["type"] == "saved"]
        if saved:
            backlog = backlog[saved[-1] + 1:]

    try:
        if not replay:
            yield {"type": "attached", "inflight": list(run.inflight.values())}
        for event in backlog:
            yield event
            if event["type"] == "stream_end":
                return
        if run.done.is_set() and queue.empty():
            yield {"type": "stream_end"}
            return
        while True:
            event = await queue.get()
            yield event
            if event["type"] == "stream_end":
                return
    finally:
        run.subscribers.discard(queue)


async def run(session_id: str, abort: asyncio.Event | None = None) -> AsyncIterator[dict]:
    """Drive the session forward and yield UI events.

    Assumes any new user input has already been persisted. `abort` is the claim
    `start_run` already made on this session; without one, this call claims the
    session itself and refuses if a turn is already in flight.
    """
    if abort is None:
        if session_id in _aborts:
            yield {"type": "error", "message": "This session is already working."}
            return
        abort = asyncio.Event()
        _aborts[session_id] = abort

    # Every path from here on has to release the claim, including the early
    # returns below -- a claim left behind marks the session busy forever and
    # nothing can be sent to it again.
    try:
        session = await db.get_session(session_id)
        if session is None:
            yield {"type": "error", "message": "Session not found"}
            return

        provider = get_provider(session["provider"])
        if not provider.has_credentials():
            await db.revert_last_user_message(session_id)
            yield {
                "type": "error",
                "message": "No API key is set up yet. Add one in Settings to get started.",
            }
            return

        async for event in _run_turn(session_id, session, provider, abort):
            yield event
    finally:
        _aborts.pop(session_id, None)
        _compacted_this_run.discard(session_id)


async def _run_turn(session_id: str, session: dict, provider, abort: asyncio.Event):
    ctx = ToolContext(
        session_id=session_id,
        project_dir=session["project_dir"],
        provider=session["provider"],
        model=session["model"],
        abort=abort,
    )
    tools_count = 0
    touched_files = False
    log.info("turn start session=%s model=%s", session_id, session["model"])
    try:
        async for event in _loop(session, provider, ctx, abort):
            if event["type"] == "error":
                log.info("turn error session=%s", session_id)
                await db.revert_last_user_message(session_id)
            elif event["type"] == "tool_end":
                tools_count += 1
                touched_files = touched_files or event.get("name") in WRITING_TOOLS
            yield event
    except asyncio.CancelledError:
        raise
    except Exception as e:
        await db.revert_last_user_message(session_id)
        yield {"type": "error", "message": f"Agent error: {type(e).__name__}: {e}"}
    finally:
        log.info("turn end session=%s tools=%d", session_id, tools_count)
        if touched_files:
            await _refresh_preview(session_id)


# Tools after which what the user is looking at may be out of date. `bash` is
# in here because a build writes files too.
WRITING_TOOLS = {"write", "edit", "bash"}


async def _refresh_preview(session_id: str):
    """Show the user the new version without being asked.

    The assistant is told to call `preview` again after a change, and mostly
    will. But "mostly" leaves the user reading that their game is fixed while
    looking at the old one, with no way to tell the difference and no terminal
    to fix it from -- so the app reloads the window itself.

    A reload, not a restart: a dev server has already picked the change up, a
    static one serves whatever is on disk, and restarting would throw away
    whatever the page was in the middle of. Something that genuinely needs its
    process restarted is still the assistant's job, and it has been told so.
    """
    from agent_server import preview

    if not preview.is_running(session_id):
        return
    with contextlib.suppress(Exception):
        await preview.reload_window(session_id)


async def _loop(
    session: dict,
    provider: Provider,
    ctx: ToolContext,
    abort: asyncio.Event,
) -> AsyncIterator[dict]:
    session_id = session["id"]
    names = allowed_tool_names(session)
    tools = tool_schemas(names)
    # Optimistic, and corrected by the provider rather than by a table. Asked of
    # the model running *now*, so a conversation that began on a text-only model
    # and moved to one that sees pictures shows it the pictures it can now look
    # at.
    model = session.get("model") or ""
    sees_images = await image_support.accepts_images(model)
    screen_reader = await db.get_setting("uses_screen_reader", "0") == "1"

    async for event in _drain_pending(session, ctx):
        yield event
        if event["type"] == "error":
            return

    system_prompt = await session_system_prompt(session)

    while True:
        if abort.is_set():
            await db.mark_interrupted(session_id)
            yield {"type": "aborted"}
            return

        for row in await _flush_queued(session_id):
            yield {"type": "queued_message", "message_id": row["id"], "content": row["content"]}

        if session_id not in _compacted_this_run:
            usage = await db.get_session_usage(session_id)
            if usage["threshold"] and usage["context"] >= usage["threshold"]:
                from agent_server.compaction import compact_session, would_compact

                # Asked before announcing anything. Sitting just over the
                # threshold with nothing worth summarising is the ordinary state
                # right after a compaction, and without this check the user saw
                # "Summarising..." flash on every single turn for nothing.
                if await would_compact(session_id):
                    _compacted_this_run.add(session_id)
                    yield {"type": "compacting"}
                    result = await compact_session(session_id)
                    yield {"type": "compacted", **result}
                    if result.get("ok"):
                        continue
                    # Deliberately not a return. Compaction is housekeeping; the
                    # user asked a question. Ending the turn here leaves their
                    # message with nothing answering it, and the next thing they
                    # type piles in behind it -- a far worse outcome than one
                    # oversized request, which may well succeed anyway since the
                    # threshold sits below the model's real limit.
                    log.info("compaction failed for %s: %s", session_id,
                             result.get("reason", "unknown"))
                else:
                    _compacted_this_run.add(session_id)

        # Anything that was still running when the tool stopped waiting for it,
        # and has since finished. Written into the conversation before the next
        # request is built, so a download that landed mid-turn is read in the
        # same breath as everything else -- and so the transcript shows it where
        # it happened rather than not at all.
        await _collect_background(session_id)

        rows = await db.get_messages(session_id)
        house_rules, rules_changed = await parental.rules_for_session(
            await db.get_session(session_id) or {})
        messages = build_messages(
            system_prompt, await db.get_compactions(session_id), rows,
            sees_images, screen_reader, house_rules,
        )
        if rules_changed:
            # Last, so it is the freshest thing the model reads. The rules
            # themselves are already current -- this exists only so the shift in
            # behaviour does not arrive as an unexplained change of character
            # the child then asks about.
            messages.append({"role": "system", "content": parental.RULES_CHANGED})

        if not any(m["role"] != "system" for m in messages):
            yield {"type": "error", "message": "Nothing to send: the conversation is empty."}
            return

        carried_pictures = sees_images and any(
            isinstance(m.get("content"), list)
            and any(part.get("type") == "image_url" for part in m["content"])
            for m in messages
        )

        content = ""
        reasoning = ""
        partials: dict[int, dict] = {}
        usage: dict | None = None
        finish = "stop"
        failed = False
        refused_pictures = False

        async for event in _stoppable(
            provider.chat_completion(
                messages=messages,
                tools=tools,
                model=session["model"],
                thinking_effort=session.get("thinking_effort"),
            ),
            abort,
        ):
            if abort.is_set():
                break

            etype = event["type"]
            if etype == "content":
                content += event["text"]
                yield {"type": "content", "text": event["text"]}
            elif etype == "reasoning":
                reasoning += event["text"]
                yield {"type": "reasoning", "text": event["text"]}
            elif etype == "tool_calls":
                _accumulate(partials, event["deltas"])
            elif etype == "usage":
                usage = event["usage"]
            elif etype == "finish":
                finish = event["reason"]
            elif etype == "error":
                # A model that cannot take a picture says so before it bills a
                # token. Retry the same turn without them rather than handing
                # the user an error they cannot act on -- and remember it, so
                # this costs one request ever rather than one a turn.
                if (
                    carried_pictures
                    and not content.strip()
                    and not reasoning.strip()
                    and image_support.looks_like_a_refusal(event.get("message", ""))
                ):
                    refused_pictures = True
                    break
                failed = True
                if content.strip() or reasoning.strip():
                    await db.add_message(
                        session_id, "assistant", content,
                        reasoning_content=reasoning or None,
                        token_count=provider.count_tokens([{"role": "assistant", "content": content}]),
                    )
                yield {"type": "error", "message": event["message"]}
                break

        if refused_pictures:
            await image_support.remember_refusal(model)
            sees_images = False
            log.info("session=%s model=%s will not take pictures", session_id, model)
            continue

        if failed:
            return

        if abort.is_set():
            if content.strip() or reasoning.strip():
                await db.add_message(
                    session_id, "assistant", content,
                    reasoning_content=reasoning or None,
                    token_count=provider.count_tokens([{"role": "assistant", "content": content}]),
                )
            await db.mark_interrupted(session_id)
            yield {"type": "aborted"}
            return

        calls = normalize_tool_calls([partials[i] for i in sorted(partials)])

        message = await db.add_message(
            session_id,
            "assistant",
            content,
            reasoning_content=reasoning or None,
            tool_calls=calls or None,
            token_count=provider.count_tokens(
                [{"role": "assistant", "content": content, "reasoning_content": reasoning,
                  "tool_calls": calls}]
            ),
            usage=usage,
        )
        # Everything up to here is in the database, so a browser that reloads
        # will already have it from the server's own rendering. The mark tells
        # a re-attaching client where its page ends and the replay begins.
        yield {"type": "saved"}
        if usage:
            if usage.get("prompt_tokens"):
                observe_usage(session["model"], message_chars(messages), usage["prompt_tokens"])
            yield {"type": "usage", "usage": usage}

        if finish == "length":
            yield {"type": "error", "message": "Model hit its output limit. Ask it to continue."}
            return

        if not calls:
            # A command handed over earlier is still going. The turn is not
            # over: the assistant has just promised to say when it lands, and
            # it can only keep that promise from inside a turn somebody is
            # listening to.
            #
            # Waiting here rather than ending and being woken later is what
            # makes it reach the screen. A woken turn is frequently over in
            # half a second, so anything that has to notice one starting -- a
            # poller, a reconnect -- loses the race and the reply arrives only
            # on the next page load. One continuous run has no race in it.
            if jobs.waiting(session_id) and not abort.is_set():
                yield {"type": "waiting", "for": jobs.note(jobs.running(session_id))}
                await jobs.wait_for_one(session_id, abort)
                if abort.is_set():
                    await db.mark_interrupted(session_id)
                    yield {"type": "aborted"}
                    return
                continue

            yield {
                "type": "done",
                "reason": finish,
                "message_id": message["id"],
                "changes": await db.get_turn_changes(session_id),
            }
            return

        stop = False
        async for event in _drain_pending(session, ctx):
            yield event
            if event["type"] == "error":
                stop = True
        if stop:
            return


async def _drain_pending(session: dict, ctx: ToolContext) -> AsyncIterator[dict]:
    """Execute every unanswered tool call on the latest assistant turn."""
    session_id = session["id"]
    rows = await db.get_messages(session_id)
    assistant_row, pending = pending_tool_calls(rows)
    if assistant_row is None or not pending:
        return

    doomed, fatal = _doom_round(session_id, pending, assistant_row["id"])
    if fatal:
        for call in pending:
            name = tool_call_name(call)
            result = ToolResult.error(_doom_message(name, _last_output_for(rows, name)), "doom-loop")
            await _record(session_id, call, result, 0)
        _doom_history.pop(session_id, None)
        _doom_recorded.pop(session_id, None)
        log.warning("doom-loop abort session=%s", session_id)
        yield {
            "type": "error",
            "message": (
                f"Stopped: the model repeated the same tool call for {DOOM_ABORT_ROUNDS} "
                "rounds without making progress."
            ),
        }
        return

    index = 0
    while index < len(pending):
        batch: list[dict] = []
        while index < len(pending) and _parallel_safe(tool_call_name(pending[index])):
            batch.append(pending[index])
            index += 1

        if len(batch) > 1:
            async for event in _run_batch(session_id, ctx, batch, doomed):
                yield event
            continue

        call = batch[0] if batch else pending[index]
        if not batch:
            index += 1

        if ctx.abort.is_set():
            await _record(session_id, call, ToolResult.error("cancelled by user", "cancelled"))
            continue

        name = tool_call_name(call)
        args = parse_arguments(call)

        yield {"type": "tool_start", "tool_call_id": call["id"], "name": name, "args": args}

        if _doom_key(call) in doomed:
            result = ToolResult.error(
                _doom_message(name, _last_output_for(rows, name)), "doom-loop"
            )
            await _record(session_id, call, result, 0)
            yield _tool_end_event(call, name, result, 0)
            yield {"type": "saved"}
            continue

        began = time.monotonic()
        task = asyncio.create_task(execute_tool(name, args, ctx))
        _track(session_id, task)
        try:
            result = await task
        except asyncio.CancelledError:
            result = ToolResult.error("cancelled by user", "cancelled")
            await _record(session_id, call, result, 0)
            if not ctx.abort.is_set():
                raise
            yield _tool_end_event(call, name, result, 0)
            yield {"type": "saved"}
            continue
        elapsed_ms = int((time.monotonic() - began) * 1000)
        await _record(session_id, call, result, elapsed_ms)
        yield _tool_end_event(call, name, result, elapsed_ms)
        yield {"type": "saved"}


def _parallel_safe(name: str) -> bool:
    tool = get_tool(name)
    return bool(tool and tool.parallel_safe)


def _doom_key(call: dict) -> tuple[str, str]:
    return (tool_call_name(call), json.dumps(parse_arguments(call), sort_keys=True))


def _doom_round(session_id: str, calls: list[dict], assistant_id: str) -> tuple[set[tuple[str, str]], bool]:
    keys = {_doom_key(call) for call in calls}
    history = _doom_history.setdefault(session_id, [])
    if _doom_recorded.get(session_id) != assistant_id:
        history.append(keys)
        _doom_recorded[session_id] = assistant_id
        if len(history) > DOOM_ABORT_ROUNDS:
            history.pop(0)
    if len(history) < DOOM_ROUNDS:
        return set(), False
    refuse = set.intersection(*history[-DOOM_ROUNDS:])
    fatal = (
        len(history) >= DOOM_ABORT_ROUNDS
        and bool(set.intersection(*history[-DOOM_ABORT_ROUNDS:]))
    )
    return refuse, fatal


def _doom_message(name: str, last_output: str = "") -> str:
    echo = ""
    if last_output:
        clipped = last_output.strip()[:600]
        echo = f"\n\nThe result you already have, unchanged:\n{clipped}"
    return (
        f"<system-interrupt reason=\"tool_call_loop\">\n"
        f"The harness stopped this call: `{name}` has now run with identical "
        f"arguments in {DOOM_ROUNDS} consecutive rounds, so the result will not "
        f"change.{echo}\n\n"
        "Do something different: use what you already have, call the tool with "
        "different arguments, try another tool, or say what is blocking you. "
        "Repeating this call will be refused again.\n"
        "</system-interrupt>"
    )


def _last_output_for(rows: list[dict], name: str) -> str:
    for row in reversed(rows):
        if row.get("role") == "tool" and row.get("tool_name") == name:
            return row.get("content") or ""
    return ""


def _tool_end_event(call: dict, name: str, result: ToolResult, elapsed_ms: int) -> dict:
    code = result.code
    if len(code) > MAX_CODE_CHARS:
        code = code[:MAX_CODE_CHARS] + "\n... [truncated in view]"
    return {
        "type": "tool_end",
        "tool_call_id": call["id"],
        "name": name,
        "title": result.title,
        "output": truncate(result.output, 20_000, "preview"),
        "is_error": result.is_error,
        "diff": result.diff,
        "lang": result.lang,
        "code": code,
        "code_start": result.code_start,
        "duration_ms": elapsed_ms,
        "open_session": result.open_session,
        "open_session_name": result.open_session_name,
        # Deliberately live-only, and never written to the messages table. An
        # action is a question waiting for an answer; a saved one would come
        # back on every reload, asking again about a deletion the user settled
        # a week ago.
        "action": result.action,
    }


async def _run_batch(
    session_id: str,
    ctx: ToolContext,
    batch: list[dict],
    doomed: set[tuple[str, str]] | None = None,
) -> AsyncIterator[dict]:
    doomed = doomed or set()

    async def run(call: dict) -> tuple[ToolResult, int]:
        began = time.monotonic()
        if ctx.abort.is_set():
            return ToolResult.error("cancelled by user", "cancelled"), 0
        name = tool_call_name(call)
        if _doom_key(call) in doomed:
            return ToolResult.error(_doom_message(name), "doom-loop"), 0
        result = await execute_tool(name, parse_arguments(call), ctx)
        return result, int((time.monotonic() - began) * 1000)

    for call in batch:
        yield {
            "type": "tool_start",
            "tool_call_id": call["id"],
            "name": tool_call_name(call),
            "args": parse_arguments(call),
        }

    tasks = [asyncio.create_task(run(call)) for call in batch]
    for task in tasks:
        _track(session_id, task)
    call_of = dict(zip(tasks, batch, strict=True))

    outcomes: dict[str, tuple[ToolResult, int]] = {}
    interrupted = False
    waiting = set(tasks)
    while waiting:
        done, waiting = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            call = call_of[task]
            try:
                result, elapsed_ms = task.result()
            except asyncio.CancelledError:
                result, elapsed_ms = ToolResult.error("cancelled by user", "cancelled"), 0
                interrupted = interrupted or not ctx.abort.is_set()
            outcomes[call["id"]] = (result, elapsed_ms)
            yield _tool_end_event(call, tool_call_name(call), result, elapsed_ms)

    for call in batch:
        result, elapsed_ms = outcomes[call["id"]]
        await _record(session_id, call, result, elapsed_ms)

    if interrupted:
        raise asyncio.CancelledError


async def _record(session_id: str, call: dict, result: ToolResult, duration_ms: int = 0) -> dict:
    from agent_server.providers.base import estimate_tokens

    output = truncate(result.output, MAX_TOOL_RESULT_CHARS, spill=True)
    args = parse_arguments(call)
    path = args.get("filePath") or args.get("path") or ""
    return await db.add_message(
        session_id,
        "tool",
        output,
        tool_call_id=call["id"],
        tool_name=tool_call_name(call),
        is_error=result.is_error,
        token_count=estimate_tokens([{"role": "tool", "content": output}]),
        diff=result.diff,
        tool_title=result.title,
        duration_ms=duration_ms,
        file_path=path if result.diff else "",
        lang=result.lang,
        code=result.code,
        code_start=result.code_start,
        usage=result.usage,
        open_session=result.open_session,
        images=result.images,
    )


def _accumulate(partials: dict[int, dict], deltas: list[dict]):
    """Reassemble streamed tool calls, which arrive a fragment at a time.

    Not every provider numbers its fragments. Gemini sends no `index` at all,
    and defaulting that to 0 dropped every call of a turn into one slot -- a
    turn asking for two tools ran only the second, with the first one's
    arguments spliced onto the front of it.

    So an unnumbered fragment opens a new slot whenever it carries an `id`
    (a new id is a new call) and otherwise continues the one most recently
    opened, which is the only reading the stream supports.
    """
    for d in deltas:
        index = d.get("index")
        if index is None:
            if d.get("id") and d["id"] not in {s.get("id") for s in partials.values()}:
                index = max(partials, default=-1) + 1
            else:
                index = max(partials, default=0)
        slot = partials.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if d.get("id"):
            slot["id"] = d["id"]
        if d.get("name"):
            slot["name"] = d["name"]
        if d.get("arguments"):
            slot["arguments"] += d["arguments"]


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
