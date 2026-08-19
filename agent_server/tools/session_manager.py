"""The session manager's tools: create, list, open, rename, delete projects.

These run only in the home (manager) session. Each project is a real session
with its own working folder. The user never chooses a path — by default the
folder lives in the hidden projects directory; a specific `folder` is only used
when the user explicitly asked for one.
"""

import re

from agent_server import database as db
from agent_server import parental
from agent_server.config import PROJECTS_DIR, provider_for_model
from agent_server.model_catalog import effective_default_model
from agent_server.tools.base import ToolContext, ToolResult


def _slug(raw: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", raw.strip().lower()).strip("-")[:40] or "project"


def _profile(ctx: ToolContext) -> str:
    return parental.profile_for_session(ctx.session_id)


def _projects_dir(ctx: ToolContext):
    base = PROJECTS_DIR
    if _profile(ctx) == "child":
        base = base / "child"
    base.mkdir(parents=True, exist_ok=True)
    return base


async def _default_model() -> tuple[str, str]:
    """The (provider, model) new projects start with.

    Resolves to the cheapest recommended model the user has credentials for, so a
    beginner can never accidentally start on the most expensive model.
    """
    model = effective_default_model(await db.get_all_settings())
    if model.startswith("custom:"):
        return model, (await db.get_setting("custom_model_id", "")).strip() or ""
    return provider_for_model(model), model


async def _git_init(folder) -> bool:
    """Start every project as a git repository.

    The user will never type a git command, and mostly will not know the word.
    It is here so that "undo that" is a thing the assistant can actually do,
    and so a project has a history to look back through -- both of which have
    to be set up before the first change, not after something has gone wrong.

    Best-effort: a machine without git still gets a perfectly working project.
    """
    import asyncio
    import shutil

    if not shutil.which("git") or (folder / ".git").exists():
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "init", "-q",
            cwd=str(folder),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return await asyncio.wait_for(proc.wait(), timeout=10) == 0
    except (OSError, TimeoutError):
        return False


async def create_project(
    ctx: ToolContext, *, name: str, description: str = "", folder: str = "", **_
) -> ToolResult:
    title = f"create project {name[:40]}"
    name = (name or "").strip()
    if not name:
        return ToolResult.error("a project name is required", title)

    if (folder or "").strip():
        project_dir = str((folder or "").strip())
    else:
        project_dir = str(_projects_dir(ctx) / _slug(name))

    from pathlib import Path

    try:
        Path(project_dir).expanduser().mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return ToolResult.error(f"could not create the project folder: {e}", title)

    await _git_init(Path(project_dir).expanduser())

    provider, model = await _default_model()
    session = await db.create_session(
        name=name,
        description=(description or "").strip() or None,
        project_dir=project_dir,
        provider=provider,
        model=model,
        kind="project",
        profile=_profile(ctx),
    )
    return ToolResult(
        output=(
            f"Created project '{name}' (id {session['id']}) in {project_dir}. "
            f"A button to open it is now on screen; the user is NOT in it yet. "
            f"Tell them it is ready and that they can open it whenever they like."
        ),
        title=f"Created project '{name}'",
        open_session=session["id"],
    )


async def list_projects(ctx: ToolContext, **_) -> ToolResult:
    sessions = await db.list_sessions(profile=_profile(ctx))
    if not sessions:
        return ToolResult(output="There are no projects yet.", title="projects")
    lines = []
    for s in sessions:
        desc = f" — {s['description']}" if s.get("description") else ""
        lines.append(f"- {s['name']}{desc} (last worked on {s['last_active_at']})")
    return ToolResult(output="\n".join(lines), title=f"{len(sessions)} projects")


async def open_project(ctx: ToolContext, *, name: str, **_) -> ToolResult:
    title = f"open project {name[:40]}"
    name = (name or "").strip()
    if not name:
        return ToolResult.error("a project name is required", title)
    session = await db.get_session_by_name(name, profile=_profile(ctx))
    if session is None:
        names = [s["name"] for s in await db.list_sessions(profile=_profile(ctx))]
        available = ", ".join(names) if names else "(no projects yet)"
        return ToolResult.error(
            f"there is no project named '{name}'. Existing projects: {available}", title
        )
    return ToolResult(
        output=(
            f"'{session['name']}' is ready to open. A button for it is on screen; "
            f"the user is NOT in it yet, so do not talk as though they are."
        ),
        title=f"Opened '{session['name']}'",
        open_session=session["id"],
    )


async def rename_project(ctx: ToolContext, *, name: str, new_name: str, **_) -> ToolResult:
    title = f"rename project {name[:40]}"
    name = (name or "").strip()
    new_name = (new_name or "").strip()
    if not name or not new_name:
        return ToolResult.error("both the current and new names are required", title)
    session = await db.get_session_by_name(name, profile=_profile(ctx))
    if session is None:
        return ToolResult.error(f"there is no project named '{name}'", title)
    await db.update_session(session["id"], name=new_name)
    return ToolResult(
        output=f"Renamed project '{name}' to '{new_name}'.",
        title=f"Renamed '{name}'",
    )


async def delete_project(ctx: ToolContext, *, name: str, **_) -> ToolResult:
    title = f"delete project {name[:40]}"
    name = (name or "").strip()
    if not name:
        return ToolResult.error("a project name is required", title)
    session = await db.get_session_by_name(name, profile=_profile(ctx))
    if session is None:
        return ToolResult.error(f"there is no project named '{name}'", title)
    await db.delete_session(session["id"])
    # The files are deliberately left in place: deleting a project should never
    # delete the user's work by surprise.
    return ToolResult(
        output=(
            f"Removed project '{name}' from the list. Its files were left in place "
            f"at {session['project_dir']} in case the user wants them back."
        ),
        title=f"Removed '{name}'",
    )


async def set_theme(ctx: ToolContext, *, theme: str, **_) -> ToolResult:
    title = f"switch to {theme} mode"
    theme = (theme or "").strip().lower()
    if theme not in ("light", "dark"):
        return ToolResult.error("theme must be 'light' or 'dark'", title)
    await db.set_setting("theme", theme)
    return ToolResult(
        output=f"Switched the app to {theme} mode. Tell the user it is done.",
        title=f"Switched to {theme} mode",
    )
