# Cutting a release

Three commands. Everything else happens on its own.

```bash
echo "1.0.1" > VERSION
git commit -am "Release 1.0.1"
git tag v1.0.1 && git push origin main v1.0.1
```

That is it. Pushing the tag runs the tests, and if they pass it builds
`basicagent-1.0.1.zip` and publishes a GitHub Release with notes generated from
the commits since the last one.

**The tag and `VERSION` must match.** The workflow checks and refuses if they
do not, because the app compares its own `VERSION` against the newest tag to
decide whether an update exists — get it wrong one way and nobody is ever
told there is an update, get it wrong the other and everybody is told forever.

## What happens to somebody who already has it

Their app asks GitHub once a day. When a newer tag appears they see one line in
Settings — *"Version 1.0.1 is ready. You have 1.0.0."* — with an **Update now**
button. Pressing it fetches the new version, installs anything new it needs,
and restarts.

Their projects, settings and API keys live in a separate folder
(`~/.local/share/basicagent`, or the platform equivalent) and are never touched
by an update.

A clone updates with `git merge --ff-only`. If somebody has local changes it
refuses and says so rather than merging on their behalf — which matters
exactly once, to you, on your own machine.

## Version numbers

Ordinary semver, and the middle number is the one that will move most.

- **1.0.x** — a fix. Something was broken and now is not.
- **1.x.0** — something new, or a visible change to something existing.
- **x.0.0** — a change big enough that somebody would want telling first.

Release often. A fix that sits in `main` for a fortnight is a fix nobody has.

## The download page

`docs/index.html`, served by GitHub Pages from the `docs/` folder on `main`
(Settings → Pages → Source: `main`, folder: `/docs`). It lives at
`https://tristan367.github.io/basicagent/` and its download button always points
at `/releases/latest`, so it never needs editing when you release.

That URL is the one to hand out. Nobody needs a GitHub account, and nobody has
to understand what GitHub is.

## Before the first release

- [ ] Push `main` and make it the default branch.
- [ ] Turn on GitHub Pages: Settings → Pages → `main` / `docs`.
- [ ] Check the page loads, and that its download button reaches the release.
- [ ] Do the whole thing yourself on a machine that has never seen this app —
      download the zip, unzip, double-click the installer, add a key, build
      something. Every step you have to think about is a step somebody else
      will give up on.
