# Deferred ideas

Things worth building that are not built, with enough of the reasoning to pick
them up later. Not a roadmap — nothing here is committed to.

## Parent review of a child's work

**Status: designed, not built. Wanted.**

Child mode already exists, and this app turns out to fit homeschooling well —
that was not the original goal, but it is a good fit and worth optimising for.

The shape we settled on: rather than exporting a transcript, a parent presses
something like **"review your child's work today"** and gets a *fresh* session
that has read everything the child did that day, across all their projects,
with the thinking and tool calls stripped out. The parent then talks to it:
"was she engaged?", "what did she actually learn?", "did you just do it for her?"

Why a conversation rather than a generated report: a report answers the
questions we anticipated, and a parent's real questions are ones we did not.

Why a **fresh** session: the session that did the teaching is grading its own
work, and will flatter both itself and the child.

Context is not a concern in practice. A day of child-mode conversation with
thinking and tool output removed is realistically 10–40k tokens even across
several projects. If a heavy day ever overflowed, summarise per session first
and review over the summaries.

Exporting a conversation as a file was considered and rejected. Feeding a
transcript to a different coding agent does not work — different system prompt,
different tools, and the receiving model cannot interpret the tool calls. The
only real use was showing a parent the work, and letting them *ask* is better.

## Memory about the family

**Status: deliberately deferred. Unresolved.**

The idea: the app remembers durable facts about the household — names, who is
in the family, that one child will engage with anything framed around bears —
so a parent does not have to re-explain them every session. It would be written
during a parent review ("next time, use a bear analogy") and read back later.

Why it is not built: **injecting it into the system prompt is the wrong
mechanism**, especially once it grows. A file that silently expands into every
request is a cost and correctness problem, and nobody has decided what happens
when it gets big. Some other retrieval shape is probably right, and nobody has
designed it yet.

Two constraints that should survive whatever design wins:

- **The parent must be able to see and edit it.** A file an AI quietly
  accumulates about a child is unsettling; a short visible list they can
  correct is a feature.
- **Preferences and context only, never assessments.** "Enjoys bears", "gets
  frustrated by long instructions" — yes. "Struggles with maths" — no. That
  kind of line calcifies into a label a child never escapes.

## Built since this file was written

- **Confirmation before switching into a new project** — creating a project no
  longer teleports the user into it. The button that was already there is now
  the only way in, and keyboard focus lands on it.
- **Time awareness** — a gap of an hour or more is described in words on the
  outgoing request only ("3 days later"), never written into the stored
  message.
