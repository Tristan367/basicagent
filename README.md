# Assistant

A coding agent for people who are not technical. The same full-strength backend
as a power-user coding agent — every tool, every model — behind the simplest
possible interface: one chat, one status line, speech in and speech out.

The **accessibility** is the point. Everything can be done by voice and heard
aloud, the text is large, and the AI is told to talk in plain language and
explain anything technical it says.

## Running it

```bash
uv venv && uv pip install -r requirements.txt
cp .env.example .env        # optional; the key can also be saved in Settings
ln -s "$PWD/bin/basicagent" ~/.local/bin/basicagent
basicagent                  # starts the server and opens the browser
```

On first run, open **Settings** and add an API key for the AI you use. The app
does not bundle an AI: it connects to one you already pay for, so you know
exactly which model you're using and what it costs.

- **Google Gemini** — has a free tier, so it costs nothing to try the whole app.
  ([get a key](https://aistudio.google.com/apikey))
- **DeepSeek** — the cheapest paid option and very good at coding; the default.
- **Anthropic** (Claude) and **OpenRouter** (one key, many models) also work, as
  does any OpenAI-compatible endpoint you add yourself (Ollama, vLLM, LM Studio).

## How it works

- The **home page is a session**: a "session manager" AI that greets you, knows
  your projects, and starts/opens/renames/removes them. You never click to make
  a project — you just tell it what you want to build.
- Each project is its own AI session with the full coding-agent tool set, but the
  user only ever sees a conversation and a single status line ("Editing a file…",
  "Checking the website…"). No tool-call transcript, no permission prompts —
  everything is auto-approved, with a small guard against truly destructive
  commands like `rm -rf /`.
- Projects live in a hidden folder by default so the user never has to think
  about paths; the manager can still put one anywhere they ask.

```
agent_server/
  agent.py          the conversation loop: stream, call tools, auto-compact
  conversation.py   DB rows <-> provider wire format
  database.py       SQLite (one connection, WAL)
  system_prompt.py  hard-coded prompts (agent + manager), frozen per session
  providers/        DeepSeek, Gemini, Anthropic, OpenRouter, custom OpenAI-compatible
  tools/            bash, browser, capture, edit, explore, glob, grep, read,
                    task, webfetch, websearch, write, session-manager tools
  routes/           home chat, project chat, settings, chat/streaming, speech
web_ui/             Jinja templates, CSS, ~one file of vanilla JS
system_prompts/     agent.md and manager.md — edit these to tune behaviour
tests/              pytest; `python -m pytest` (nothing here bills an API)
```

## Tests

```bash
.venv/bin/python -m pytest        # fast, offline, no API key needed
```

Tests marked `live` hit a real provider and bill a real account; they are
excluded by default and opt-in with `pytest -m live`.

## Where your data lives

`~/.local/share/basicagent/` — the database (keys and transcripts) and the hidden
`projects/` folder. Override with `BASICAGENT_DATA_DIR`.

## The system prompts

The two prompts are plain Markdown files in `system_prompts/` — `agent.md` for
project sessions and `manager.md` for the home session. They are deliberately
not editable in the UI; tune them here.

## Accessibility notes

- Dictation uses faster-whisper, installed by pip with the other dependencies.
  It downloads its own model on first use and needs no system packages, so it
  behaves the same on Linux, macOS and Windows. Pick how accurate you want it
  to be in Settings. The mic is on by default.
- Read-aloud uses Kokoro (`kokoro-v1.0.onnx` + `voices-v1.0.bin` in
  `~/models/tts`); toggle it from the chat.
- The UI is built for screen readers and keyboard use; every control has a
  proper label and focus outline.

## Caveats

- Single-user tool with no authentication. It runs arbitrary shell commands and
  can read/write anywhere your account can. Bind it to `127.0.0.1` (default).
- Vision (the `browser`/`capture` `ask` feature) and read-aloud are optional and
  simply unavailable if their models are not installed.
