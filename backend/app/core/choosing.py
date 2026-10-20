"""Step 10b, v2.1: choosing. Where the tree holds a choice the alphabet
made (DD-12's last rule), one model call is shown the question and, for
each such choice, its alternatives, and names for each the alternative the
question means, or none (the owner's rulings at stop A2, 20 October 2026).

Pure (DD-01). The model call itself is the orchestrator's; this module
holds what is asked, what may come back, and what is done with it.

    CHOOSE_RULES         the call's system message, the owner's wording
    choices_of           the tree's alphabetical choices, in its order:
                         every attachment the alphabet decided, a tie at
                         one table or between places. Its alternatives are
                         what was left after the wording and any declared
                         preference; what a preference withdrew is never
                         offered
    build_choose_prompt  the question, then each choice with its
                         alternatives, each as its join condition in the
                         frozen join line's form and the Explainer's
                         sentence for that route
    choose_reply_schema  the reply's strict shape, built per call: every
                         choice once, each with its own letters or null
    parse_choose_reply   the reply read strictly
    named_routes         the route ids named, for build_tree's `chosen`
    settle               each choice against the rebuilt tree: applied,
                         left open, or not met; and any alphabetical choice
                         that arose only after the rebuild

THE ORDER OF THE ALTERNATIVES (ruling 5). Never the alphabet's, which
would put the alphabet's own pick first every time: each choice's routes
are listed in the order of the sha256 of the question's text, a newline,
and the route's id. Fixed and reproducible, needing no seed, and different
from question to question.

ONE CALL, ONE REBUILD (ruling 3). A named route may change what later
attachments see. Nothing is asked again: a choice that arises only after
the rebuild stays the alphabet's and is recorded as such.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

import networkx as nx

from app.core.explainer import describe_path
from app.core.join_tree import Attachment, JoinTree
from app.core.path_finder import Path
from app.core.prompt_builder import Prompt

CHOOSE_RULES = """\
You choose how the tables of a data warehouse are joined to answer a question.

The tables a query needs have been chosen. Between some of them there is more than one way to join. For each choice below you are given its alternatives, each as a join condition and a sentence saying what it joins.

Rules:
- For each choice, name the alternative the question means.
- If the question does not say which is meant, name none for that choice. Do not guess.
- Use only the labels given.

Reply with one JSON object and nothing else:
  {"choices": {"<choice number>": "<alternative letter>" or null, ...}}
with every choice number once. Do not explain."""

CHOOSE_REPLY_NAME = "choose_reply"

# Room for twenty choices answered at once, at fewer characters a token
# than any text takes (a test holds it to that).
CHOOSE_MAX_OUTPUT_TOKENS = 400


@dataclass(frozen=True)
class OpenChoice:
    """One attachment the alphabet decided, as it is put to the model."""

    number: int
    anchor: str
    # Where the alphabet attached it.
    attached_to: str
    # In the order listed, each under label(position).
    alternatives: tuple[Path, ...]
    # The id of the route the alphabet took.
    alphabet: str

    @property
    def key(self) -> tuple[str, frozenset[str]]:
        """The same choice in another tree: the table and its routes."""
        return self.anchor, frozenset(path.id for path in self.alternatives)


def label(position: int) -> str:
    """A, B, ... Z, AA, AB, ..."""
    letters = ""
    position += 1
    while position:
        position, rest = divmod(position - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def _listed(question: str, paths: tuple[Path, ...]) -> tuple[Path, ...]:
    return tuple(sorted(paths, key=lambda path: hashlib.sha256(f"{question}\n{path.id}".encode()).hexdigest()))


def _open(attachment: Attachment) -> bool:
    return attachment.path is not None and attachment.rule == "alphabetical"


def choices_of(tree: JoinTree, question: str) -> tuple[OpenChoice, ...]:
    found = [attachment for attachment in tree.attachments if _open(attachment)]
    return tuple(
        OpenChoice(number, a.anchor, a.attached_to, _listed(question, a.tied), a.path.id)
        for number, a in enumerate(found, start=1)
    )


def _conditions(path: Path) -> str:
    """Each join in the frozen join line's form, in order along the route."""
    return "; then ".join(
        " AND ".join(f"{join.fk_table}.{fk} = {join.pk_table}.{pk}" for fk, pk in zip(join.fk_columns, join.pk_columns))
        for join in path.joins
    )


def build_choose_prompt(question: str, choices: tuple[OpenChoice, ...], graph: nx.DiGraph) -> Prompt:
    if not choices:
        raise ValueError("no choice to ask about; the call is not made")
    parts = [f"Question: {question.strip()}"]
    for choice in choices:
        lines = [f"Choice {choice.number}: {choice.anchor} can be joined in these ways:"]
        lines += [
            f"  {label(i)}. {_conditions(path)} — {describe_path(path, graph).description}"
            for i, path in enumerate(choice.alternatives)
        ]
        parts.append("\n".join(lines))
    tables = tuple(sorted({table for c in choices for path in c.alternatives for table in path.tables}))
    return Prompt(CHOOSE_RULES, "\n\n".join(parts), tables, False)


def choose_reply_schema(choices: tuple[OpenChoice, ...]) -> dict:
    properties = {
        str(c.number): {"type": ["string", "null"], "enum": [*(label(i) for i in range(len(c.alternatives))), None]}
        for c in choices
    }
    return {
        "type": "object",
        "properties": {
            "choices": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            }
        },
        "required": ["choices"],
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class ChooseReply:
    kind: Literal["named", "malformed"]
    # Per choice, in order: the letter named, or None.
    named: tuple[str | None, ...] = ()
    fault: str | None = None


def parse_choose_reply(text: str, choices: tuple[OpenChoice, ...]) -> ChooseReply:
    """Read strictly. Nothing is repaired."""
    try:
        reply = json.loads(text)
    except ValueError:
        return ChooseReply("malformed", fault="the reply is not JSON")
    if not isinstance(reply, dict) or set(reply) != {"choices"}:
        return ChooseReply("malformed", fault="the reply does not have exactly the field choices")
    answers = reply["choices"]
    if not isinstance(answers, dict):
        return ChooseReply("malformed", fault="choices is not an object")
    if set(answers) != {str(c.number) for c in choices}:
        return ChooseReply("malformed", fault="it does not answer every choice exactly once")
    named = []
    for c in choices:
        answer = answers[str(c.number)]
        if answer is not None and answer not in {label(i) for i in range(len(c.alternatives))}:
            return ChooseReply("malformed", fault=f"it names an alternative choice {c.number} does not have: {answer}")
        named.append(answer)
    return ChooseReply("named", tuple(named))


def _route(choice: OpenChoice, letter: str | None) -> str | None:
    if letter is None:
        return None
    return choice.alternatives[[label(i) for i in range(len(choice.alternatives))].index(letter)].id


def named_routes(choices: tuple[OpenChoice, ...], named: tuple[str | None, ...]) -> frozenset[str]:
    return frozenset(route for c, letter in zip(choices, named) if (route := _route(c, letter)) is not None)


@dataclass(frozen=True)
class Settled:
    number: int
    anchor: str
    # The route named, or None when the model named none.
    route: str | None
    outcome: Literal["applied", "left_open", "not_met"]


def settle(
    choices: tuple[OpenChoice, ...], named: tuple[str | None, ...], rebuilt: JoinTree
) -> tuple[tuple[Settled, ...], tuple[OpenChoice, ...]]:
    """Each choice as the rebuilt tree took it, and the alphabetical choices
    of the rebuilt tree that were never asked about."""
    applied = {a.path.id for a in rebuilt.attachments if a.rule == "model_choice"}
    settled = []
    for c, letter in zip(choices, named):
        route = _route(c, letter)
        outcome = "left_open" if route is None else "applied" if route in applied else "not_met"
        settled.append(Settled(c.number, c.anchor, route, outcome))
    asked = {c.key for c in choices}
    arose = tuple(c for c in choices_of(rebuilt, "") if c.key not in asked)
    return tuple(settled), arose
