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

## Media in a reply: video, audio and pictures — from disk only

**Status: decided, not built.**

A picture already appears in the chat when the assistant writes a path on a line
of its own. Audio and video should work exactly the same way, and so should
anything the *user* attached: attach a sound file, and it is playable from your
own message.

**Only files on this computer. No `https://` sources of any kind** -- not video,
not audio, not even images. That was argued both ways and the simple rule won.

### Why nothing from the internet

The worry that started this was "what if the AI shows a child something
inappropriate". That is the right worry with the wrong mechanism, and naming the
real one settles it:

- **A model does not choose a bad video. It guesses a video ID.** Eleven
  invented characters; the ones that are not a 404 are a stranger's upload with
  no relation to the lesson. Not fixable by a better-behaved model, and it
  applies to any URL a model made up.
- **An embed is a doorway, not a video.** Even `youtube-nocookie.com/embed` ends
  on recommendations and a "Watch on YouTube" link. Embedding one video puts all
  of YouTube inside a homeschooling app -- precisely what these parents are
  avoiding.
- **A remote image is a request to a third party the user never made**, handing
  their address to whatever host the model named.

A proxy-and-allowlist scheme was considered for images and rejected. It works,
but it buys a marginal feature for a permanent piece of policy surface -- and
every argument for it starts with "the model found a picture of", which is the
part we do not want to trust. A file on disk has none of these questions, and
the agent can always download one deliberately first if it truly needs to.

### "Limit the internet in child mode"

Tempting, and mostly not possible. The agent has `bash`, `webfetch` and
`websearch`, and it needs them -- to install a package, to read current
documentation, to find something a child asked about. Cutting them off leaves an
agent that cannot do the job.

The line that *is* enforceable, and the one the rule above implements:
**the agent may read the internet; the child is never handed a doorway into
it.** Reading a page and telling a child what it says is supervised by the
model. A live, navigable third-party surface inside the app is supervised by
nothing.

### What has to happen when it is built

- **Read-aloud must skip a player.** `to_prose` already strips fenced code and
  turns a bare URL into "a link". A player is another thing not to read out --
  its name probably should be.
- **The agent has to be told.** The prompt has a section on showing a picture;
  this is another paragraph in it, in the same voice: what it does for the user,
  not what the syntax is.

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
object: **a lesson is a project, and the parent and the child are two sessions
in it.** The parent creates the project exactly as they would create their own,
talks to its agent about what they want taught, and then marks it visible in
child mode. Switch profiles, sit the child down, and they open it. No new kind
of thing to explain, and the parent has a place to go back to.

The plan itself is **`LESSON.md` in the project folder**. Session agents are
told never to change it, and always to look for one, because that is what the
parent wants covered today. A file rather than a database row: the parent can
have their own session edit it, it lives with the project, and it is the one
thing in this app a user might genuinely want to keep.

The authoring flow is the good part and should survive intact: the parent
*talks* about what they want their child to learn, the agent drafts the
objectives and the questions, the parent adjusts them in conversation. A parent
cannot author a lesson plan in a text box. They can absolutely describe one out
loud.

### Open questions

- **Read-only has to be enforced, not just asked for.** "Can you make it
  easier?" and today's agent rewrites the objectives without hesitating.
  `file_ops` resolves every path already, so refusing `write`/`edit` on
  `LESSON.md` for a child-profile session is small. `bash` can still clobber it
  and probably always will be able to -- the realistic guard is that a refused
  `edit` must not send the agent looking for a way round, which is a prompt
  line as much as a code one.
- **Who greets the child, and with what.** The child's home assistant should say
  a plan is waiting -- but the plan changes daily and a session's prompt is
  frozen on first use, so today's lesson cannot live in the prompt. It has to be
  discovered: `list_projects` reporting which projects have a `LESSON.md`, and a
  standing line in the prompt about what that means.
- **The first message in the project.** Hard-coded today, and identical whether
  the project is a bouncing-ball lesson or a spreadsheet. Generating it is
  better *when there is a lesson* -- there is something worth saying, so the
  wait earns itself -- and hard-coded is better when there is not, because an
  empty chat that spins for three seconds is worse than an instant line,
  especially for someone listening. The seed should be fixed text from the app,
  not a prompt the home assistant composes: otherwise its wording drifts and the
  project agent gets a game of telephone.
- **It makes the parent review real.** The review idea at the top of this file
  has nothing to compare against right now. Objectives give it one, and turn
  "was she engaged" into "did she get there". These two are one feature and
  should be designed together.

## Running the thing for the user

**Status: decided in principle -- the app owns the process, not the agent.**

A "Play" button was considered. The objection to it is real: press play, get a
tab; the agent works, press play again, get another tab; by the evening there
are thirty and none of them is obviously the current build.

But the objection applies just as much to the agent launching things, and
prompting cannot fix it -- **an agent that opened a tab with `xdg-open` has no
handle on it and physically cannot close it again.** Telling it to "close the
old tab first" instructs it to do something impossible, which is worse than not
telling it anything.

So the missing piece is not the button. It is that **the app must own what is
running**: one slot per project, one process, one window. Starting again means
killing the old one and replacing it. The agent declares how the project runs --
a command, and a URL if it serves one -- and the app does the launching. A play
button, if there is one, calls exactly the same thing and is greyed out while
the agent is mid-turn.

This app already runs in a Chromium instance it drives itself, which is what
makes the window half tractable: a window opened through that can also be
closed through it. A raw `xdg-open` cannot.

Two things fall out of this that are worth keeping straight:

- **`browser` is not the user's window.** It is headless -- an instrument for
  the agent to verify with. Driving a page in it shows the user nothing. The
  prompt now says so, because the two were obviously conflatable.
- **Not everything is a web page.** A pygame window has no tab and no URL, and
  the same one-slot process supervision covers it. Which is the argument for
  "declare a command" over anything web-specific.

## Built since this file was written

- **Confirmation before switching into a new project** — creating a project no
  longer teleports the user into it. The button that was already there is now
  the only way in, and keyboard focus lands on it.
- **Time awareness** — a gap of an hour or more is described in words on the
  outgoing request only ("3 days later"), never written into the stored
  message.
