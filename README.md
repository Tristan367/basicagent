# Assistant

A coding agent for people who are not programmers.

It is not a cut-down one. It has the same tools a professional coding assistant
has — it reads and writes files, runs commands, drives a real browser, searches
the web, keeps a git history. What is different is everything around them:
nobody types a command, finds a file, or approves a step. You say what you want
and it builds it, and then it runs it on your screen.

**Accessibility is the point, not a feature.** It is built to be usable by
someone who cannot see the screen, cannot use a mouse, has never opened a
terminal, or is eight years old. Everything can be spoken and everything can be
heard. The same decisions serve all of them, and they turn out to make a good
tool for everybody else too.

---

## Installing it

You need [Python](https://www.python.org/downloads/) 3.11, 3.12 or 3.13 and
about 3 GB of disk. Download this folder, then:

**Windows** — double-click **`install.bat`**. (On the Python installer's first
screen, tick *Add python.exe to PATH*.)

**Mac or Linux** —

```bash
git clone https://github.com/Tristan367/BasicCodingAgent.git
cd BasicCodingAgent
python3 install.py
```

That is the whole thing. It builds its own private Python environment, installs
everything, downloads the voices, and puts **Assistant** in your applications
menu (Start menu on Windows, Applications folder on a Mac) so nobody has to open
a terminal again.

If your computer's Python is too new for one of the parts, the installer finds a
suitable one that is already installed, or fetches one with
[uv](https://docs.astral.sh/uv/) if you have it — it does not just fail.

Options go on the end either way — `install.bat --minimal` on Windows,
`python3 install.py --minimal` elsewhere:

| | |
|---|---|
| `--minimal` | Skip the speech downloads (saves about 800 MB). The assistant can install them later — just ask it. |
| `--no-shortcut` | Do not add a menu entry or desktop icon. |

**Starting it:** click the icon. Or `./bin/basicagent` on a Mac or Linux;
`.venv\Scripts\python basicagent.py` on Windows.

**Updating it:** `git pull`, then run the installer again. Your projects and
settings live outside the folder, so nothing is lost.

**Removing it:** delete the folder, delete
`~/.local/share/basicagent` (`%APPDATA%\basicagent` on Windows), and delete the
menu entry the installer made.

### Connecting an AI

The app does not come with an AI. It connects to one you have an account with,
so you know exactly which model you are using and what it costs. Open
**Settings** the first time it runs; there is a walkthrough with every click
written out.

- **Google Gemini** has a free tier, so the whole app costs nothing to try.
  ([get a key](https://aistudio.google.com/apikey))
- **DeepSeek** is the cheapest paid option and very good at code. It is the default.
- **OpenRouter** is one key for many models.
- **Anything OpenAI-compatible** you run yourself — Ollama, vLLM, LM Studio —
  can be added as a custom endpoint.

---

## How it works

- **The home screen is a conversation.** A project-manager assistant greets you,
  knows your projects, and starts, opens, renames and removes them. You never
  click to make a project; you say what you want to build.
- **It also runs the app for you.** Light or dark, bigger text, a different
  colour, read-aloud and which voice, the sounds, child mode — all of it can be
  asked for out loud. The one thing it will not touch is an API key, because a
  key pasted into a chat is a key written into the conversation history.
- **Each project is its own assistant** with the full coding toolset, and it
  stays narrow on purpose: it builds the project and knows nothing about the app
  around it. Ask it to change the voice and it will send you back to the project
  manager, which can.
- **You see a conversation and one status line** — "Editing a file…", "Checking
  the website…". No tool transcript, no permission prompts.
- **Files live in a hidden folder** so nobody has to think about paths, and every
  project can be downloaded as a zip from Settings. Your work is yours.

### For families

Child mode makes the assistant kind, safe, and focused on teaching rather than
handing over answers; it locks the AI and API-key settings behind a parent
password; and a child's projects are kept entirely separate from the adult's. A
parent can also write a lesson into a project — objectives, and the questions
they want answered without help — and hand that project to the child.

---

## Where your data lives

`~/.local/share/basicagent/` (`%APPDATA%\basicagent` on Windows) holds the
database — API keys and every conversation — the projects folder, the log, and
the downloaded speech models. Override the location with `BASICAGENT_DATA_DIR`.

Nothing is sent anywhere except to the AI provider you connected, and only what
you are actually talking about. The speech — both dictation and read-aloud —
runs entirely on your own computer and needs no account and no internet.

## Security, honestly

This is a single-user tool with **no login**, and it is not going to grow one.
It runs shell commands as whoever started it and can read and write anything
that account can. Reaching it over a network means reaching a terminal on that
computer.

So it listens on `127.0.0.1` — your own machine only — and every launcher warns
loudly if you change that. If you want to use it from a tablet, put it behind a
VPN or an SSH tunnel rather than opening the port.

The agent's commands are auto-approved by design, with a guard against the
genuinely destructive (`rm -rf /` and its relatives). That is a deliberate
trade: a permission prompt is unusable for the person this app is for.

---

## Working on it

```
agent_server/
  main.py           FastAPI app, startup and shutdown
  agent.py          the conversation loop: stream, call tools, auto-compact
  conversation.py   database rows <-> provider wire format
  database.py       SQLite (one connection, WAL)
  system_prompt.py  the two prompts, frozen per session
  paths.py          where things live; importable before anything is installed
  downloads.py      fetching the speech models
  providers/        DeepSeek, Gemini, OpenRouter, custom OpenAI-compatible
  tools/            bash, browser, capture, edit, glob, grep, read, preview,
                    task, webfetch, websearch, write, and the manager's own
  routes/           chat and streaming, settings, sessions, files, speech
web_ui/             Jinja templates, CSS, and vanilla JS -- no build step
system_prompts/     agent.md and manager.md; edit these to tune behaviour
tests/              pytest, offline, no API key needed
```

```bash
.venv/bin/python -m pytest        # the whole suite, offline, free
.venv/bin/python -m ruff check .
./try-fresh.sh                    # meet the app as a new user does
```

Tests marked `live` hit a real provider and bill a real account. They are
excluded by default and opt in with `pytest -m live`.

The two system prompts are plain Markdown in `system_prompts/`, deliberately not
editable from the UI. `NOTES.md` records designed-but-unbuilt ideas and the
reasoning behind them.

## Licence

MIT. See [LICENSE](LICENSE).
