"""The download page makes promises. These check they are still true.

The page is the first thing a stranger sees, and the part of it that matters
most is the section explaining why their computer is about to warn them. Every
sentence in there is a claim about how the program actually behaves -- where it
installs, what it talks to, how the release is built. A claim like that rots
quietly: somebody adds a service, or changes the release workflow, and the page
goes on reassuring people about something that is no longer the case.

So the claims are pinned here. If one of these fails, the fix may well be to
change the page rather than the code -- but it has to be a decision either way.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "docs" / "index.html").read_text()
WORKFLOW = (ROOT / ".github" / "workflows" / "release.yml").read_text()


def test_the_page_offers_the_source_beside_the_download():
    """Anybody can go and look, and is told so where they decide."""
    assert "https://github.com/Tristan367/basicagent/releases/latest" in PAGE
    assert 'class="also" href="https://github.com/Tristan367/basicagent"' in PAGE


def test_the_warning_is_explained_before_it_is_met():
    """Both installer steps point at the section, and the section exists."""
    assert 'id="safe"' in PAGE
    for anchor in re.findall(r'href="#([^"]+)"', PAGE):
        assert f'id="{anchor}"' in PAGE, f"the page links to #{anchor}, which is not there"
    # The two places a person actually meets the warning.
    windows, mac = PAGE.split("<h3>Mac</h3>")[0], PAGE.split("<h3>Mac</h3>")[1]
    assert 'href="#safe"' in windows.split("<h3>Windows</h3>")[1]
    assert 'href="#safe"' in mac.split("<h3>Linux</h3>")[0]


def test_the_receipt_the_page_promises_is_actually_signed():
    """The page says GitHub signs a statement about the download. It must."""
    assert "signs a statement" in PAGE
    assert "actions/attest-build-provenance" in WORKFLOW
    assert "attestations: write" in WORKFLOW
    assert "id-token: write" in WORKFLOW
    # The signature covers the file people download, not something else.
    assert "subject-path: basicagent-*.zip" in WORKFLOW
    # And the release itself says how to check it.
    assert "gh attestation verify" in WORKFLOW


def test_the_three_places_it_talks_to_are_the_only_three():
    """The page names an AI provider, GitHub and DuckDuckGo, and no others.

    Anything else reaching the network at runtime makes that sentence false.
    """
    allowed = {
        "generativelanguage.googleapis.com",  # Gemini
        "openrouter.ai",
        "api.deepseek.com",
        "api.github.com",  # the update check
        "github.com",  # release downloads: the voices and the speech model
        "lite.duckduckgo.com",  # the search tool
    }
    # Places that are only ever shown to a person to visit, never called.
    signposts = {
        "aistudio.google.com", "ai.google.dev", "platform.deepseek.com",
        "api-docs.deepseek.com", "www.python.org",
        "docs.astral.sh", "www.w3.org", "www.apple.com",
    }
    found = set()
    for path in list((ROOT / "agent_server").rglob("*.py")) + \
            list((ROOT / "web_ui").rglob("*.py")) + list(ROOT.glob("*.py")):
        for host in re.findall(r"https?://([a-zA-Z0-9.-]+)", path.read_text()):
            if host in {"localhost", "127.0.0.1", "box", "..."}:
                continue
            found.add(host)
    unexpected = found - allowed - signposts
    assert not unexpected, (
        f"the download page says it talks to three places; this also reaches "
        f"{sorted(unexpected)}. Either stop, or rewrite that paragraph."
    )


def test_it_still_installs_without_administrator():
    """The page says so, and an installer asking for root would embarrass it.

    Not a search for the word: the installer legitimately *tells* people that
    `sudo apt install python` is how they would get Python if it is missing.
    What it must never do is run something as root itself, so this looks for a
    command that begins with it, and for the Windows way of asking to elevate.
    """
    assert "never asks for administrator or root" in PAGE
    installer = (ROOT / "install.py").read_text()

    for literal in re.findall(r"""["']([^"'\n]*)["']""", installer):
        assert not literal.lower().startswith(("sudo", "runas", "doas")), (
            f"the installer looks like it elevates: {literal!r}")

    for elevation in ("ShellExecute", "geteuid", "IsUserAnAdmin", "pkexec"):
        assert elevation not in installer
