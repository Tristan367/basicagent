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

## Media in a reply: video, audio, and pictures from the web

Deferred, and worth doing.

A picture already appears in the chat when the assistant writes a path on a
line of its own. Three things extend the same idea, in rising order of how much
they change:

1. **Picture links.** The same treatment for an `https://` image URL, not only
   a path on this computer. Almost free: `imageBlock` already builds the
   element, the only difference is where `src` points. The one real question is
   whether an image the model chose should be fetched from wherever it says --
   it is a request to a third party the user did not make, and on a school
   network that is worth thinking about before it ships.

2. **Audio.** A `<audio controls>` for a sound file, local or linked. Small,
   and obviously right for a child making a game with sound effects.

3. **Video, and YouTube.** A `<video controls>` for a file. A YouTube link is
   the interesting one, and the reason to bother: a child who is bored of
   reading can be shown the thing instead. It needs an iframe embed
   (`youtube-nocookie.com/embed/ID`), which is a third-party frame inside the
   app -- so it wants a decision about whether that frame may run at all, and
   in child mode probably a stricter one.

Whatever ships has to be told to the model, or it will never use it: the agent
prompt has a section on showing a picture, and this is another paragraph in it.
Keep that paragraph in the same voice -- *what it does for the user*, not what
the syntax is.

Read-aloud has to skip them. `to_prose` strips fenced code and turns a bare URL
into "a link"; a player is another thing that must not be read out, and its
title probably should be.

### The line to draw, and why

The worry that stalled this was "what if the AI shows a child something
inappropriate". That is the right worry but the wrong mechanism, and naming the
real one settles the design:

- **A model does not choose a bad video. It guesses a video ID.** Eleven
  characters, invented, and the ones that are not a 404 are a stranger's
  upload with no relation to the lesson. That is the actual exposure, it is not
  fixed by a better-behaved model, and it applies to any URL the model made up.
- **An embed is a doorway, not a video.** Even `youtube-nocookie.com/embed`
  ends on recommendations and a "Watch on YouTube" link. Embedding one video
  puts all of YouTube inside a homeschooling app. That is precisely what the
  parents this is for are avoiding, and they are right.
- **A remote image is a request to a third party the user did not make** --
  their address given to whatever host the model named. Smaller, but the same
  shape.

So the rule is about **provenance, not media type**:

> A player is for a file on this computer, or for something a *person* chose.
> Never for a URL the model invented.

Which gives, in order of what to build:

1. **Audio and video for local files.** `<audio controls>` / `<video controls>`
   for a path, exactly like a picture path today. No provenance problem at all
   -- the agent made or fetched the file deliberately. Obviously right for a
   child making a game with sound. Build this first; it is the cheap half.
2. **Remote images, proxied.** Fetch server-side and re-serve from our own
   origin rather than putting a foreign URL in `src`. Kills the address leak,
   gives one place to enforce a child-mode rule, and makes local and remote a
   single code path.
3. **YouTube: a link card, not an embed.** Title, channel, thumbnail, opens in
   the real browser. The family's own controls then apply, and we have not
   built a video portal. An actual embed, if ever, is a parent-password
   setting that is off by default.

The exception worth building toward: **a video a parent put in a lesson is
fine to embed**, because a human chose it. That inverts the whole problem, and
it is an argument for the lesson-plan idea below rather than against players.

### "Limit the internet in child mode"

Tempting, and mostly not possible. The agent has `bash`, `webfetch` and
`websearch`, and it needs them -- to install a package, to read current
documentation, to find a picture of a shark a child asked for. Cutting them off
leaves an agent that cannot do the job.

The line that *is* enforceable: **the agent may read the internet; the child is
never handed a doorway into it.** Reading a page and telling a child what it
says is supervised by the model. A live, navigable third-party surface inside
the app is not supervised by anything. That distinction is defensible to a
parent and it is the one to hold.

## Lessons a parent writes and a child opens

**Status: designed here, not built. The strongest idea in this file.**

Two things a child does in this app, and they are not the same thing:

1. **Making games.** This is what a child will actually want, and it is the
   best teaching vehicle there is -- the feedback loop is immediate, the
   artifact is theirs, and "make the ball bounce faster" is a change they can
   ask for in their own words and see happen. It is also the one kind of
   software a non-technical person can judge for themselves.
2. **What the parent wants taught.** Objectives, a subject for the day,
   questions the child should be able to answer afterwards *without* asking the
   AI.

The instinct is to build these as two separate features. They should be one
object: **a lesson is attached to a project, not a project of its own.** The
parent says "I want her to make a bouncing-ball game, and to come out of it
understanding loops and coordinates." That produces a child project that is a
game project, with objectives the agent knows about. The child opens it and
makes a game; the teaching rides along inside the thing they wanted to do
anyway. Two competing lists collapse into one.

The authoring flow is the good part of the original idea and should survive
intact: the parent *talks* to the home assistant about what they want their
child to learn, it drafts the objectives and the questions, the parent adjusts
them in conversation. A parent cannot author a lesson plan in a text box. They
can absolutely describe one out loud.

Four things that have to be solved:

- **The child's agent must not be able to edit the lesson.** Today a project's
  agent has `write` and `edit` over the whole folder, so "can you make it
  easier" would have it cheerfully rewrite the objectives. And the lesson is
  partly *instructions to the agent* -- "do not give her these four answers" --
  which only works if it is trusted, which only holds if the child cannot
  change it. Read for the child, write for the parent. `file_ops` already
  resolves every path, so one denied path is a small change.
- **Handing it over.** Child projects live under their own directory and the
  child has a separate home session. A parent-authored lesson has to be
  published into the child's list -- probably a flag on the project rather than
  moving files.
- **The child has to be told it is there.** Otherwise it is a folder nobody
  opens. The child's home assistant should say so on arrival, in its own words:
  "your mum left you something to do today."
- **It makes the parent review real.** The deferred review feature above has
  nothing to compare against right now. Objectives give it one, and turn "was
  she engaged" into "did she get there". These two features are one feature and
  should be designed together.

## Built since this file was written

- **Confirmation before switching into a new project** — creating a project no
  longer teleports the user into it. The button that was already there is now
  the only way in, and keyboard focus lands on it.
- **Time awareness** — a gap of an hour or more is described in words on the
  outgoing request only ("3 days later"), never written into the stored
  message.
