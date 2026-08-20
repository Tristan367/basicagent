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

## Buttons the assistant can put in the chat

**Status: designed, not built. Wanted, with one change from the original idea.**

The idea: the assistant offers a button in the chat, the user clicks it, and
something happens. "How do I turn on dark mode?" -- rather than describing where
the control is, it hands them a button that takes them there.

That is worth building, and it is the missing half of a voice-first app: a
person who cannot navigate a settings page can still be *taken* to the right
part of it.

**The change: a button goes somewhere, it does not do something.** The original
idea was a button that runs whatever the assistant wants, behind a confirmation
dialog. That is the wrong shape, for a specific reason -- the user trusts the
button because of the label, and the label and the action are written by the
same fallible thing. A model that has misunderstood, or that has read a file
with instructions in it, produces "Click here to see your photos" over a
different action entirely, and the confirmation dialog only repeats the label
back. A destination cannot lie in the same way: the user arrives somewhere and
can see what it is before doing anything.

So: `show_button(label, goes_to)`, where `goes_to` comes from a fixed list the
app knows -- a named section of Settings, a project, the lesson handoff below.
Landing there should focus the actual control and scroll it to the middle, so
what happens next is obvious.

Nothing is lost by this. When the assistant should *do* something, it should
just do it -- it already has tools -- and a button that only navigates needs no
confirmation dialog at all.

## Working the app by voice alone

**Status: partly built (`set_theme`), the rest designed.**

The goal that makes this app what it claims to be: every setting reachable by
asking. Light and dark, accent colour, zoom, read-aloud on and off, which voice,
volume and speed, microphone, renaming and deleting projects.

One tool rather than a dozen -- `change_setting(name, value)` over the same
named settings the manager prompt already lists -- because a dozen schemas cost
context on every single request for something used once a week.

**The trap, and it is the important part.** Settings tools must respect the
parental lock, or they *are* the way around it. A child asking the assistant to
turn child mode off, switch the model, or reveal a key has to be refused exactly
as the Settings page refuses it. The gate already exists; the tool has to be
routed through the same one rather than reaching for `set_setting` itself.

See "Settings should not be a page" below: the hole this leaves is that Settings
is the one screen in the app where nobody is listening.

## Settings should not be a page

**Status: designed, the groundwork done. Wanted.**

Settings is a destination, so going there ends the conversation: the draft in the
composer, the scroll position, the thing you were halfway through explaining.
And it is the one screen in this app where nobody is listening -- which in an app
whose whole premise is "everything can be done by talking" is a hole rather than
a layout preference.

**The shape: a panel that slides over the chat, never a destination.** The chat
stays mounted underneath and stays usable -- no navigation, nothing lost. Wide
screens get roughly 480px down the right; narrow ones a bottom sheet over about
seventy percent, with the conversation still visible above it. Escape closes it.
Focus moves in on open and back to the Settings button on close, and it is
deliberately **not** a focus trap: Tab must be able to leave, because the point
is that the chat still works while it is open.

### Who is listening while it is open

The awkward bit, and the reason this is not just a CSS change. A project's AI
does not know how the app works and never will -- that separation is deliberate
and worth keeping. So a question asked with the panel open has nowhere obvious
to go.

Two answers, and the cheaper one is probably right:

1. **Give the manager the settings tools, and let people ask wherever they are.**
   "Turn on dark mode" is answered by the manager doing it. A project's AI, asked
   the same thing, offers the button that takes them to the manager (see the
   buttons section above). The panel is then simply for people who would rather
   click, and needs no conversation of its own.
2. **Put a single ask-line in the panel** that routes to the manager and shows
   the answer inside the panel, leaving the project's transcript untouched.

Build (1) first. (2) is the kind of thing that sounds necessary and turns out to
be used twice; it can be added later without redoing anything.

### What has to happen first

- **The Settings JavaScript is now its own file** (`web_ui/static/js/settings.js`)
  rather than four hundred lines inside the template. It had no template
  variables in it, so it was only there by habit -- and injecting a template
  containing an inline `<script>` into a panel would not have run it. Done, and
  verified to behave identically: theme, zoom, autosave, modals, voice preview.
- **Three of the eight forms POST and reload** -- the theme row and the two
  custom-endpoint forms. In a panel those have to become fetch calls, or the
  panel throws itself away on submit. That is the actual remaining work.
- A `/settings/body` route returning the panel's contents without the page
  shell, so the panel can fetch it and the page can keep working as a fallback.

Keep `/settings` as a real page throughout. It is the one screen a user cannot
afford to lose access to -- the API key lives there -- so the panel should be an
addition until it has been used for a while, not a replacement on day one.

## Handing a lesson to a child

**Status: designed, follows the lesson notes above.**

The flow, end to end: the parent talks to the home assistant about what they
want taught. It creates the project, writes `LESSON.md`, and edits it in
conversation -- attach a picture to use, a video, a folder; say to keep off the
internet; say that spiders are not to come up. When the parent says it is ready,
it offers a button. The button turns child mode on and opens the lesson
*session* directly, not the child's home screen.

Two things this needs that do not exist yet:

- **A project can be created for the child.** Today the manager creates into the
  profile it is running as. A parent's manager needs to be able to make one that
  lands in the child's list, under the child's folder and profile.
- **Turning child mode on needs no password**, and should not ask for one --
  the password is for coming *out*. Setting one if there is none is the only
  thing worth prompting for, and only once.

### Should the child be locked into the lesson?

**Recommendation: no, and this is a real recommendation rather than a deferral.**

The worry is fair -- a child opens yesterday's racing game instead of the lesson.
But a lock needs an unlock, an unlock needs the parent, and the parent is the
one resource a homeschooling household has least of. The failure it creates is
worse than the one it prevents: a child who has finished, wants to be rewarded
with their own project, and is stuck behind a password while the person holding
it is teaching someone else in another room.

The better lever is that the lesson session's assistant *knows the objectives*.
"We can go back to your racing game once we have done the two questions your mum
left" is the same outcome, arrives from something that sounds like a person, and
does not need anybody to come and unlock anything.

If focus is still wanted after trying it, the least-bad version: a flag on the
child profile that hides the other projects, cleared by the parent password
**and** cleared automatically when the lesson's objectives are marked done -- so
the ordinary path out is finishing, not finding an adult.

## Language in child mode

**Status: asked for, not built. One concern to settle first.**

Wanted: in child mode, strip swearing and inappropriate words from everything
rendered -- what the assistant says, and the child's own dictation as it is
transcribed.

The concern is the classic one, and it will bite: a naive word list mangles
ordinary words a child doing schoolwork will absolutely use. Class, pass,
assignment, assassin, grape, Uranus, cockatoo, Scunthorpe, analysis. Word
boundaries fix most of it and not all -- "pass" against "ass" is exactly the
case boundaries do not settle.

So whatever is built needs a curated list with boundary rules and a test file of
words that must survive, treated as seriously as the list itself.

Two halves, worth separating because they are not equally valuable:

- **What the assistant says** -- worth doing, cheap, and the thing a parent is
  actually trusting the app about. The model will not swear at a child anyway,
  so this is a net under a net; the risk is only false positives, which the word
  list controls.
- **The child's own dictation** -- less clear. The child already said it, and
  blanking it teaches nothing while looking like the microphone failed. The
  value a parent wants is that the app does not say it *back*, which the first
  half already covers.

The awkward part either way: replies stream in a word at a time, so a filter has
to buffer across chunk boundaries or it will miss anything split across two.
The sentence splitter that read-aloud already uses is the natural seam.

## Built since this file was written

- **Confirmation before switching into a new project** — creating a project no
  longer teleports the user into it. The button that was already there is now
  the only way in, and keyboard focus lands on it.
- **Time awareness** — a gap of an hour or more is described in words on the
  outgoing request only ("3 days later"), never written into the stored
  message.
- **Pictures the model can actually see** — attachments and screenshots reach
  the model as pictures. Only from disk; nothing is ever fetched from the web.
- **Running the project for the user** (`preview`) — the app owns one process
  and one window per project, so a rebuild replaces both rather than stacking
  up, with Play and Stop buttons calling the same thing. Confined to this
  machine in child mode.
- **The manager can write files**, so a plan can be drafted into a project from
  the home screen.
