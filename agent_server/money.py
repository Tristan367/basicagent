"""Adding money to an account, said so that somebody can actually do it.

"Insufficient credits" is a true sentence that helps nobody. The person who has
to act on it is usually not the person reading it -- a parent, a teacher -- and
they have never seen a billing console, do not know which of Google's several
sites they are meant to be on, and have no idea whether the right answer is
five dollars or five hundred. Left there, the picture never gets made and the
child concludes the app is broken.

So this holds the whole answer in one place: where to go, what to click when
they get there, how much to put in, and what that buys in pictures. The numbers
come from the models this computer can actually reach, so they are this child's
numbers rather than an average.

Written once and used twice: the assistant is told it so it can say it, and the
app puts it on the screen itself so it cannot be paraphrased into something
subtly wrong. A link that is nearly right is worse than no link, because
somebody will spend an afternoon on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# What to suggest putting in. It is Google's minimum top-up, which makes it the
# easy answer to give, and it happens to be a sensible one: a few hundred
# pictures is a whole project's worth rather than an afternoon's.
SUGGESTED = 10.0


@dataclass(frozen=True)
class TopUp:
    """Where a provider takes money, and what to do when you get there."""

    provider: str
    name: str
    url: str
    url_label: str
    steps: list[str] = field(default_factory=list)
    # Prepaid means money goes in and is spent down, and when it is gone
    # nothing more is charged. That is the arrangement to prefer for a child's
    # account, and worth saying out loud where it is on offer.
    prepaid: bool = True


TOP_UPS: dict[str, TopUp] = {
    "gemini": TopUp(
        provider="gemini",
        name="Google",
        url="https://aistudio.google.com/apikey",
        url_label="aistudio.google.com/apikey",
        steps=[
            "Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) "
            "and sign in with the same Google account the key came from.",
            "Find the project the key belongs to. Beside it, under Billing "
            "Tier, click the Set up billing button.",
            "Choose or create a billing account, and pick the Prepay plan — "
            "that is money put in up front and spent down, rather than a bill "
            "arriving later.",
            "Put in $10. That is the smallest amount Google takes.",
            "Worth the extra minute: at "
            "[aistudio.google.com/spend](https://aistudio.google.com/spend) "
            "you can set a limit for the project. It stops when it reaches "
            "that and cannot run up a bill beyond it.",
        ],
    ),
    "openrouter": TopUp(
        provider="openrouter",
        name="OpenRouter",
        url="https://openrouter.ai/settings/credits",
        url_label="openrouter.ai/settings/credits",
        steps=[
            "Go to [openrouter.ai/settings/credits]"
            "(https://openrouter.ai/settings/credits) and sign in.",
            "Click Add Credits and put in $10.",
            "It works straight away. Credit is spent down as it is used, and "
            "when it runs out nothing more is charged.",
        ],
    ),
    "deepseek": TopUp(
        provider="deepseek",
        name="DeepSeek",
        url="https://platform.deepseek.com/top_up",
        url_label="platform.deepseek.com",
        steps=[
            "Go to [platform.deepseek.com](https://platform.deepseek.com/top_up) "
            "and sign in.",
            "Open Top up and add $10.",
        ],
    ),
}


def where(provider: str) -> TopUp:
    """How to put money on this provider's account.

    An unknown provider still gets an answer. It is a vaguer answer, because
    nobody here has seen the site -- but "sign in and look for Billing" is
    something a person can act on, and silence is not.
    """
    known = TOP_UPS.get(provider)
    if known:
        return known
    name = provider.removeprefix("custom:") or "the provider"
    return TopUp(
        provider=provider,
        name=name,
        url="",
        url_label="",
        steps=[
            f"Open {name}'s website and sign in with the account this key "
            f"came from.",
            "Look for Billing, Credits or Top up in the account settings, and "
            "add $10.",
        ],
        prepaid=False,
    )


def pictures_for(dollars: float, models) -> tuple[int, int] | None:
    """How many pictures that much money buys: fewest, then most.

    Fewest is the dearest model and most is the cheapest, so the two numbers
    bracket whatever actually happens. A parent deciding whether $10 is
    generous or stingy can read that in one go, which is the question they are
    really asking.

    None when nothing reachable has a published price. Guessing here would be
    guessing about somebody else's money.
    """
    priced = [m.about_each for m in models if getattr(m, "priced", False)]
    if not priced:
        return None
    return int(dollars // max(priced)), int(dollars // min(priced))


def _count(dollars: float, models) -> str:
    span = pictures_for(dollars, models)
    if not span:
        return ""
    fewest, most = span
    if fewest == most:
        return f"${dollars:.0f} is about {most} pictures."
    return (f"${dollars:.0f} is somewhere between {fewest} and {most} "
            f"pictures — {fewest} using the best model for every one, {most} "
            f"using the cheapest, which is fine for sprites and backgrounds.")


def advice(provider: str, models, dollars: float = SUGGESTED) -> str:
    """The whole answer as prose, for the assistant to pass on.

    Numbered, because the assistant will read this aloud to somebody who is
    following along on another screen, and "step three" is a thing they can be
    told to go back to.
    """
    top_up = where(provider)
    lines = [f"To put money on the {top_up.name} account:"]
    lines += [f"{n}. {step}" for n, step in enumerate(top_up.steps, 1)]
    count = _count(dollars, models)
    if count:
        lines.append("")
        lines.append(count)
    return "\n".join(lines)


def panel(provider: str, models, dollars: float = SUGGESTED) -> dict:
    """The same answer as data, for the app to put on screen itself.

    On screen rather than only in the reply because these steps have to be
    exactly right. An assistant retelling them will one day say
    console.cloud.google.com, which is a real page that looks plausible and is
    not where the button is, and somebody will lose an evening to it.
    """
    # The same splitter the settings walkthrough uses, so an address written
    # into a step becomes a real link in both places and there is one story
    # about escaping rather than two. It lives over there because that is where
    # it was first needed; importing it late keeps this module free of the web
    # layer for everything except this one line.
    from agent_server.routes.context import link_parts

    top_up = where(provider)
    return {
        "kind": "add_funds",
        "provider": provider,
        "name": top_up.name,
        "url": top_up.url,
        "url_label": top_up.url_label,
        "steps": [link_parts(step) for step in top_up.steps],
        "count": _count(dollars, models),
    }
