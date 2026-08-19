"""Seed a session that exercises every rendering path, for eyeballing the UI.

There is no way to check that headings, tables, lists, code blocks, file
windows and inline images all still look right without a conversation that
contains all of them, and asking a real model to produce one costs money and
comes out different every time.

    python tools/seed_demo.py ~/.local/share/basicagent

Creates a project called "Formatting demo" with a matching folder on disk, so
the file windows and the image actually resolve. Safe to re-run: it replaces
the session rather than adding another.
"""
import datetime
import pathlib
import sqlite3
import struct
import sys
import zlib

data = pathlib.Path(sys.argv[1])
db = data / "agent.db"
proj = data / "projects" / "formatting-demo"
(proj / "src").mkdir(parents=True, exist_ok=True)

(proj / "app.js").write_text("\n".join([
    "// Budget tracker entry point",
    "import { render } from './src/ui.js';",
    "",
    "const state = { total: 0, entries: [] };",
    "",
    "export function addExpense(name, amount) {",
    "  state.entries.push({ name, amount });",
    "  state.total += amount;",
    "  return state.total;",
    "}",
    "",
    "render(state);",
]))
(proj / "src" / "ui.js").write_text("\n".join(f"export const line{i} = {i};" for i in range(1, 40)))

# a real PNG: a 240x120 gradient


def png(w, h):
    raw = b"".join(b"\x00" + bytes(
        v for x in range(w) for v in (int(255*x/w), 90, int(255*(1-x/w)))
    ) for _ in range(h))
    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))
(proj / "chart.png").write_bytes(png(240, 120))

MARKDOWN = """Here is everything the formatter can do, so you can see it all at once.

# Heading one
## Heading two
### Heading three

Ordinary paragraph text with **bold**, *italic*, ***both***, ~~strikethrough~~, and `inline code` in the middle of a sentence. A [link to somewhere](https://example.com) as well.

## A table

| Model | Speed | Cost per million | Good at |
| --- | --- | --- | --- |
| DeepSeek V4 Pro | Fast | $0.87 | Everyday coding, cheapest |
| Gemini 3.7 Flash | Very fast | Free to start | Pictures and long documents |
| Claude Opus 5 | Careful | $25.00 | The hardest problems |
| GPT-5 Mini | Fast | $2.00 | A good all-rounder |

## Lists

- First item
- Second item with `code` inside
- Third item
  - A nested one
  - And another

1. Step one
2. Step two
3. Step three

## Quotes and rules

> A blockquote, which the assistant sometimes uses to quote an error message
> back at you before explaining what it means.

---

## Code

```python
def total(entries):
    \"\"\"Add up every expense.\"\"\"
    running = 0
    for entry in entries:
        running += entry["amount"]

    return running
```

```bash
python3 -m http.server 8000
```

```
a fenced block with no language set at all
so it should render as plain text
```

## A window into a real file

app.js:5-11

And a longer one, to check the gutter widens for two digits:

src/ui.js:8-22

## A picture

chart.png

That is everything."""

now = datetime.datetime.now(datetime.UTC).isoformat()
c = sqlite3.connect(db)
c.execute("DELETE FROM messages WHERE session_id='fmt-demo'")
c.execute("DELETE FROM sessions WHERE id='fmt-demo'")
c.execute("INSERT INTO sessions (id,name,description,project_dir,provider,model,kind,profile,created_at,last_active_at)"
          " VALUES ('fmt-demo','Formatting demo','Every markdown feature at once',?,'deepseek','deepseek-v4-pro','project','parent',?,?)",
          (str(proj), now, now))
c.execute("INSERT INTO messages (session_id,role,content,created_at,code_start) VALUES ('fmt-demo','user',?,?,1)",
          ("Show me everything you can render, so I can check it looks right.", now))
c.execute("INSERT INTO messages (session_id,role,content,created_at,code_start) VALUES ('fmt-demo','assistant',?,?,1)",
          (MARKDOWN, now))
c.commit()
print("seeded session 'fmt-demo' with project at", proj)
