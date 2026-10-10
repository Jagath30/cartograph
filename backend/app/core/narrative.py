"""The narrative: the plain-English account of how an answer was reached,
composed once and stored (FR-26, DD-06, DD-19, criterion 14).

Pure (DD-01): the sections of a trace document in, five strings out. It is
run once, when the trace is assembled, and what it returns is stored; no
display composes anything (DD-06). Improving the wording later does not
improve a trace already written, and that cost was accepted.

WHO IT IS FOR. A manager who does not write SQL. It is the SUMMARY that
reader takes in; the Explainer's full sentence for every route, with its
keys and its direction, stays in the trace's `paths` for the detail view
(DD-19). The rules, as the owner set them at stop 2 of step 8:

  1. It speaks of MEANING. "The billed customer's demographics", not "a
     row" or "a surrogate key"; no table or column by its identifier.
  2. `route` says, for each choice that touched the answer, what was used
     and HOW it was chosen: the only route, the shortest, the wording of
     the question, a declared preference, or the alphabet -- and then it
     says the choice is arbitrary. It does not name the alternatives.
     `not_taken` names them, once: "Not used: ...", then ONE "If you meant
     one of those, the answer may differ; ask again naming it." for all
     the arbitrary choices together, then what a declared preference or
     the question's wording set aside, the longer routes, and the unused
     part of the plan.
  3. A declared preference's reason is given once in a narrative, as a
     clause after "because".
  4. Longer routes get one sentence in all: "N longer routes also
     existed; the shortest was used." They were considered and lost.
  5. Choices in the part of the plan the query did not use get one
     sentence. A plan that gave no answer is one sentence too.
  6. Alternatives that share an owner and a noun are one phrase: "the
     customer's dates of first purchase, first shipment and last review".
  7. The plain phrases come from app.core.narrative_words, which nothing
     else reads. Where it has none, the readable name is used without
     "surrogate key".

CLOSE CALLS ARE NOT HERE (the owner's ruling at stop 2b). Their terms are
machine-made word pairs, not the reader's words; they stay in the trace's
retrieval section for the detail view.

THE FIVE PARTS, each a string (ruling 8 at stop 1), in this order:

  found      what the question matched, and what was added to connect it.
  route      what the answer links to what, and on what basis.
  not_taken  what was not used, and what to do about it.
  sql        whether the query followed the plan. `not_checked` never
             claims that it did (T-02).
  result     what came back. A decline lists the tables the model was
             shown, and says it is a statement about those tables and not
             about the warehouse (item 62).

Nothing here decides anything about the answer. Every sentence is derived
from a fact in the trace; nothing is narrated by a model (DD-14).
"""

from pydantic import BaseModel, ConfigDict

from app.core.narrative_words import KEY_SUFFIX, MOMENTS, ONE, PAIRS, ROLES, TABLES, THINGS

ASK_AGAIN_ONE = "If you meant the other, the answer may differ; ask again naming it."
ASK_AGAIN_SEVERAL = "If you meant one of those, the answer may differ; ask again naming it."
ARBITRARY = "nothing in your question said which you meant, so the choice was made alphabetically: it is arbitrary."


class Narrative(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    found: str
    route: str
    not_taken: str
    sql: str
    result: str


def narrate(*, outcome, message, retrieval, subgraph, paths, generation, validation, execution, names) -> Narrative:
    """`names` maps every table, and every `table.column`, to its readable
    name. The other arguments are the sections of the trace document."""
    tell = _Teller(outcome, message, retrieval, subgraph, paths, generation, validation, execution, names)
    return Narrative(found=tell.found(), route=tell.route(), not_taken=tell.not_taken(), sql=tell.sql(), result=tell.result())


def _listed(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    # A list of phrases that themselves hold a list is parted by semicolons.
    if any(" and " in item or ", " in item for item in items):
        if len(items) == 2:
            return f"{items[0]}, and {items[1]}"
        return "; ".join(items[:-1]) + "; and " + items[-1]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _plural(text: str) -> str:
    """"date of" -> "dates of"; "customer's demographics" -> "customers'
    demographics"; "customer" -> "customers"."""
    if " of" in text:
        head, rest = text.split(" of", 1)
        return f"{head}s of{rest}"
    if "'s " in text:
        return text.replace("'s ", "s' ", 1)
    return text + ("es" if text.endswith("s") else "s")


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _count(number: int, one: str, many: str) -> str:
    return f"{number} {one if number == 1 else many}"


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _clause(reason: str) -> str:
    """A declared reason as it reads after "because"."""
    return reason.strip().rstrip(".")


class _Teller:
    def __init__(self, outcome, message, retrieval, subgraph, paths, generation, validation, execution, names) -> None:
        self.outcome = outcome
        self.message = message
        self.retrieval = retrieval
        self.subgraph = subgraph
        self.paths = paths
        self.generation = generation
        self.validation = validation
        self.execution = execution
        self.names = names
        # The SQL was read and compared with the plan.
        self.checked = validation is not None and validation.conformance is not None
        use = {self._key(edge): edge.use for edge in paths.selected_edges}
        joined = [a for a in paths.attachments if a.selected is not None]
        # A choice the query went against, or may have, touched the answer
        # as surely as one it followed: it is told too.
        against = {
            warning.about[0]
            for warning in paths.warnings
            if warning.state in ("other_route_taken", "unknown") and warning.about
        }
        self.used = [
            a
            for a in joined
            if a.selected.id in against or any(use.get(self._key(j)) in ("present", "partial") for j in a.selected.joins)
        ]
        # What is told: the choices that touched the answer (rule 2). When
        # no answer was given none did, and the plan is one sentence; its
        # choices are in the trace's detail.
        self.answered = outcome == "answered"
        self.told = self.used
        self.untold = [a for a in joined if a not in self.told]
        # Each declared reason is given once (rule 3).
        self._reasons_given: list[str] = []

    @staticmethod
    def _key(edge) -> tuple:
        return (edge.from_table, tuple(edge.from_columns), edge.to_table, tuple(edge.to_columns))

    # ---- plain words ------------------------------------------------------

    def table(self, name: str) -> str:
        readable = self.names["tables"].get(name, name)
        return TABLES.get(readable, readable)

    def one(self, name: str) -> str:
        """One record of a table: web sales -> web sale."""
        readable = self.names["tables"].get(name, name)
        return ONE.get(readable, readable)

    def tables(self, names) -> str:
        return _listed(self.table(name) for name in names)

    def _plain_column(self, table: str, column: str) -> str:
        readable = self.names["columns"].get(f"{table}.{column}", column)
        return readable.removesuffix(KEY_SUFFIX)

    def _thing(self, join) -> tuple:
        """What a key points to, in plain words, as parts that can be
        grouped (rule 6):
          ("of", "date of", "first purchase")       its date of first purchase
          ("adj", "billed", "customer's demographics")
          ("plain", article, phrase)."""
        target = self.names["tables"].get(join.to_table, join.to_table)
        thing = THINGS.get(target, ONE.get(target, target))
        if len(join.from_columns) != 1:
            return ("plain", "its", thing)
        base = self._plain_column(join.from_table, join.from_columns[0])
        if base in MOMENTS:
            moment = MOMENTS[base]
            for head in ("date of", "time of"):
                if moment.startswith(head + " "):
                    return ("of", head, moment[len(head) + 1 :])
            return ("plain", "its", moment)
        for tail in (target, target.split(" ")[-1]):
            if base == tail:
                return ("plain", "its", thing)
            if base.endswith(" " + tail):
                role = base[: -len(tail) - 1]
                if (role, target) in PAIRS:
                    return ("plain", "the", PAIRS[role, target])
                return ("adj", ROLES.get(role, role), thing)
        # No phrase: the readable name, without "surrogate key" (rule 7).
        return ("plain", "its", base)

    @staticmethod
    def _said(part: tuple, owner: str | None = None, alone: bool = False) -> str:
        kind, first, second = part
        article, body = {"of": ("its", f"{first} {second}"), "adj": ("the", f"{first} {second}")}.get(kind, (first, second))
        if owner:
            return f"the {owner}'s {body}"
        if not article:
            return body
        return f"{'the' if alone else article} {body}"

    def _owner(self, join, beside) -> str | None:
        """Whose it is, when it belongs to another table than the one
        being spoken of: "the customer's date of first purchase"."""
        return self.one(join.from_table) if beside is not None and join.from_table != beside else None

    def link(self, join) -> str:
        return f"each {self.one(join.from_table)} to {self._said(self._thing(join))}"

    def _reading(self, route, beside) -> tuple:
        """A whole route as (owner, part): its key's meaning, or for a
        route of several joins the table it goes through."""
        if len(route.joins) == 1:
            return self._owner(route.joins[0], beside), self._thing(route.joins[0])
        parts = [self._thing(join) for join in route.joins]
        dated = [f"{first} {second}" if kind == "of" else second for kind, first, second in parts
                 if kind == "of" or second in MOMENTS.values()]  # fmt: skip
        moments = list(dict.fromkeys(dated))
        through = f"a route through {self.tables(route.tables[1:-1])}" + (f" ({_listed(moments)})" if moments else "")
        return None, ("plain", "", through)

    def _grouped(self, readings: list[tuple]) -> list[str]:
        """The readings as phrases, those that share an owner and a noun
        as one (rule 6), each at the place of its first member."""
        readings = list(dict.fromkeys(readings))
        groups: dict[tuple, list[tuple]] = {}
        for owner, part in readings:
            key = (owner, part[0], part[1] if part[0] == "of" else part[2]) if part[0] != "plain" else (owner, part)
            groups.setdefault(key, []).append((owner, part))
        phrases = []
        for members in groups.values():
            owner, (kind, first, second) = members[0]
            if len(members) == 1:
                phrase = self._said(members[0][1], owner, alone=True)
            elif kind == "of":
                body = f"{_plural(first)} {_listed(part[2] for _, part in members)}"
                phrase = f"the {owner}'s {body}" if owner else f"the {body}"
            else:
                body = f"{_listed(part[1] for _, part in members)} {_plural(second)}"
                phrase = f"the {owner}'s {body}" if owner else f"the {body}"
            phrases.append(phrase.strip())
        return phrases

    def _because(self, attachment) -> str:
        reasons = list(dict.fromkeys(_clause(r) for r in attachment.preference_reasons))
        fresh = [r for r in reasons if r not in self._reasons_given]
        given = len(fresh) < len(reasons)
        self._reasons_given += fresh
        if not fresh:
            return ", for the reason already given"
        return (", for the reason already given and because " if given else ", because ") + ", and because ".join(fresh)

    # ---- found ------------------------------------------------------------

    def found(self) -> str:
        paths = self.paths
        if not paths.anchors:
            return "No table matched your question closely enough to start from."
        if len(paths.anchors) == 1:
            said = [f"Your question matched one table: {self.table(paths.anchors[0])}."]
        else:
            said = [f"Your question matched {len(paths.anchors)} tables: {self.tables(paths.anchors)}."]
        if paths.unconnected:
            said.append(
                f"{_capital(self.tables(paths.unconnected))} could not be linked to the rest within "
                f"{_count(paths.max_joins, 'step', 'steps')}."
            )
            return " ".join(said)

        bridges = [name for name in paths.tables if name not in paths.anchors]
        if bridges:
            one = len(bridges) == 1
            said.append(
                f"To link them, {self.tables(bridges)} {'was' if one else 'were'} added: "
                f"your question did not name {'it' if one else 'them'}."
            )
        dropped = paths.subgraph_bound.dropped_anchors
        if dropped:
            said.append(
                f"{_capital(self.tables(dropped))} also matched and {'was' if len(dropped) == 1 else 'were'} left out "
                f"to keep the plan within {paths.subgraph_bound.limit} tables."
            )
        beside = [node.table for node in self.subgraph.nodes if not node.in_tree] if self.subgraph else []
        if beside:
            said.append(f"The model was also shown {self.tables(beside)}, which could have linked them equally well.")
        return " ".join(said)

    # ---- route ------------------------------------------------------------

    def route(self) -> str:
        paths = self.paths
        if not paths.tables:
            return "No plan was made."
        joined = [a for a in paths.attachments if a.selected is not None]
        if not joined:
            return f"Only {self.table(paths.tables[0])} was found, so there was nothing to link."
        if not self.answered:
            return (
                f"A plan was made linking {self.tables(paths.tables)}. No answer came of it, so none of its "
                "choices affected one."
            )
        if not self.told:
            return (
                f"The plan linked {self.tables(paths.tables)}; the query used none of those links, "
                "so no choice among them affected this answer."
            )
        said = []
        for position, attachment in enumerate(self.told):
            said.append(self._used(attachment, first=position == 0))
            said.append(self._basis(attachment))
        if self.untold:
            not_used = self.tables(a.anchor for a in self.untold)
            said.append(f"The plan also brought in {not_used}; the query did not use {'it' if len(self.untold) == 1 else 'them'}.")
        return " ".join(said)

    def _used(self, attachment, first: bool) -> str:
        """One sentence for what was used."""
        opening = "The answer links" if first else "It also links"
        joins = attachment.selected.joins
        if len(joins) == 1:
            return f"{opening} {self.link(joins[0])}."
        ends = self.tables([attachment.anchor, attachment.attached_to])
        between = self.tables(attachment.selected.tables[1:-1])
        return f"{opening} {ends} through {between}: {_listed(self.link(join) for join in joins)}."

    def _others(self, attachment, status: str) -> list[tuple]:
        """The alternatives of one kind, each as (owner, part)."""
        beside = attachment.selected.joins[0].from_table if len(attachment.selected.joins) == 1 else None
        return [self._reading(a, beside) for a in attachment.alternatives if a.status == status]

    def _basis(self, attachment) -> str:
        """How it was chosen: one wording for each rule of DD-12. The
        alternatives are counted here and named under what was not used."""
        tied = len(self._others(attachment, "tied"))
        withdrawn = len(self._others(attachment, "withdrawn"))
        if attachment.rule == "only_path":
            return "It is the only route between them."
        if not (tied or withdrawn):
            return "It is the shortest route between them."
        if attachment.rule == "question_evidence":
            return f"The wording of your question pointed to this over {_count(tied, 'other reading', 'other readings')}."
        if attachment.rule == "preference":
            return (
                f"{_count(tied + withdrawn, 'other reading was', 'other readings were')} equally possible; "
                f"a preference declared for this warehouse chose this one{self._because(attachment)}."
            )
        # The alphabet. The word "arbitrary" is in the sentence itself.
        if withdrawn:
            return (
                f"A preference declared for this warehouse set aside {_count(withdrawn, 'reading', 'readings')}"
                f"{self._because(attachment)}. {_count(tied, 'other', 'others')} remained equally possible; {ARBITRARY}"
            )
        return f"{_count(tied, 'other reading was', 'other readings were')} equally possible; {ARBITRARY}"

    # ---- not taken --------------------------------------------------------

    def not_taken(self) -> str:
        paths = self.paths
        if not paths.tables:
            return "No plan was made, so nothing was set aside."
        if not any(a.selected is not None for a in paths.attachments):
            return "With one table there was nothing to choose between."
        if not self.answered:
            return "No answer was given, so nothing that was chosen affected one."
        arbitrary: list[tuple] = []
        by_wording: list[tuple] = []
        by_preference: list[tuple] = []
        for attachment in self.told:
            tied = self._others(attachment, "tied")
            if attachment.rule == "alphabetical":
                arbitrary += tied
            elif attachment.rule == "question_evidence":
                by_wording += tied
            else:
                by_preference += tied
            by_preference += self._others(attachment, "withdrawn")
        # Named once: a reading is listed where the reader can act on it.
        by_preference = [reading for reading in by_preference if reading not in arbitrary]

        said = []
        if arbitrary:
            names = self._grouped(arbitrary)
            one = len(dict.fromkeys(arbitrary)) == 1
            said.append(f"Not used: {_listed(names)}. {ASK_AGAIN_ONE if one else ASK_AGAIN_SEVERAL}")
        if any(warning.state == "other_route_taken" for warning in paths.warnings):
            said.append("The query itself used one of these and not the one planned.")
        if by_wording:
            said.append(f"Set aside by the wording of your question: {_listed(self._grouped(by_wording))}.")
        if by_preference:
            said.append(f"Set aside by declared preference: {_listed(self._grouped(by_preference))}.")
        if not said and self.told:
            said.append("Nothing else was equally short.")
        longer = sum(max(a.discovered - 1 - len(a.alternatives), 0) for a in self.told)
        if longer:
            said.append(f"{_count(longer, 'longer route', 'longer routes')} also existed; the shortest was used.")
        if any(a.alternatives for a in self.untold):
            said.append("Other choices were made in the part of the plan the query did not use; they did not affect this answer.")
        elif not self.told:
            said.append("Nothing was chosen that affected this answer.")
        return " ".join(said)

    # ---- sql --------------------------------------------------------------

    def _planned(self, edge) -> str:
        return f"the link from {self.link(edge)}"

    def _made(self, edge) -> str:
        """A join the query made that the plan did not choose, in plain
        words: by the key it is, when the schema shown holds that key."""
        pair = {(edge.left_table, edge.left_column), (edge.right_table, edge.right_column)}
        for key in self.subgraph.edges if self.subgraph else []:
            pairs = [
                {(key.from_table, start), (key.to_table, end)} for start, end in zip(key.from_columns, key.to_columns)
            ]
            if pair in pairs:
                return self.link(key)
        return (
            f"{self.table(edge.left_table)} to {self.table(edge.right_table)} by matching "
            f"{self._plain_column(edge.left_table, edge.left_column)} with "
            f"{self._plain_column(edge.right_table, edge.right_column)}"
        )

    def sql(self) -> str:
        if not self.checked:
            return self._no_query()
        paths = self.paths
        planned = paths.selected_edges
        made = sum(1 for edge in planned if edge.use == "present")
        missing = [edge for edge in planned if edge.use == "missing"]
        read = set(self.execution.tables) if self.execution else set()
        unneeded = [name for name in paths.tables if name not in read]
        spare = f"; {self.tables(unneeded)} {'was' if len(unneeded) == 1 else 'were'} not needed" if unneeded else ""
        conformance = self.validation.conformance

        if conformance == "conforms":
            said = "The query followed this plan exactly." if planned else "The query read the one table and linked nothing."
        elif conformance == "incomplete":
            if not paths.actual_edges:
                only = self.tables(self.execution.tables) if self.execution and self.execution.tables else "one table"
                said = f"The query read only {only} and linked nothing{spare}."
            else:
                said = f"The query followed this plan, using {made} of its {_count(len(planned), 'join', 'joins')}{spare}."
        elif conformance == "diverged":
            parts = ["The query did not follow this plan."]
            foreign = [edge for edge in paths.actual_edges if edge.foreign]
            if foreign:
                parts.append(f"It linked {_listed(self._made(edge) for edge in foreign)}, which the plan did not choose.")
            partial = [edge for edge in planned if edge.use == "partial"]
            if partial:
                parts.append(f"It made {_listed(self._planned(edge) for edge in partial)} on only part of what identifies it.")
            for group in paths.cross_joins:
                parts.append(
                    f"It read {self.tables(group)} without linking them, so everything in one is paired with "
                    "everything in the other."
                )
            if paths.unchecked:
                parts.append("Part of it could not be read with confidence.")
            if missing:
                parts.append(f"Planned and not used: {_listed(self._planned(edge) for edge in missing)}.")
            said = " ".join(parts)
        else:
            said = (
                "Part of the query could not be read with confidence, so it is not confirmed that it followed this "
                f"plan. What could be read used {made} of its {_count(len(planned), 'join', 'joins')} and nothing outside them."
            )

        for warning in paths.warnings:
            if warning.code == "many_to_many" and warning.loud:
                pivot = self.table(warning.about[0]) if warning.about else "one table"
                said += (
                    f" Take care: more than one table is linked through {pivot}, so their records are paired with "
                    "one another and totals can be counted more than once."
                )
        asked = len(self.generation.attempts)
        if asked > 1:
            earlier = "reply was" if asked == 2 else "replies were"
            said = f"The model was asked {asked} times; the earlier {earlier} not accepted. {said}"
        return said

    def _no_query(self) -> str:
        if self.outcome == "not_answerable":
            if self.generation is None:
                return "No query was written, and no model was asked."
            return "No query was written: the model said the tables it was shown do not hold the answer."
        if self.outcome == "model_failed":
            return _sentence(f"No query was written: {self.message}")
        if self.validation is not None and self.validation.read_only == "fail":
            return (
                "No query was run. What the model wrote would have done something other than read the data. "
                "It was refused, and the model was not asked again."
            )
        asked = len(self.generation.attempts) if self.generation else 0
        return f"No query was run. What the model wrote was refused {_count(asked, 'time', 'times')}: it was not a query this warehouse could run."

    # ---- result -----------------------------------------------------------

    def result(self) -> str:
        execution = self.execution
        if self.outcome == "answered":
            rows = execution.row_count
            said = "The query ran and no rows came back." if rows == 0 else f"{_count(rows, 'row', 'rows')} came back."
            if execution.truncated:
                said += f" More rows matched than the limit of {execution.row_cap} allows; these are the first."
            return said
        if self.outcome == "execution_failed":
            if execution.failure == "timeout":
                limit = f" of {execution.statement_timeout}" if execution.statement_timeout else ""
                return f"The query was run and was stopped at the time limit{limit}. No rows came back."
            if execution.failure in ("not_reachable", "role_refused"):
                return _sentence(f"The query was not run: {execution.error}")
            return f"The query was run and the database stopped it: {_sentence(execution.error or 'no reason was given')} No rows came back."
        if self.outcome == "not_answerable":
            if self.generation is not None:
                shown = self.generation.tables_shown
                if len(shown) == 1:
                    return (
                        f"No answer was given. The model was shown one table: {self.table(shown[0])}. It said it does "
                        "not hold the answer. That is a statement about this table, not about the whole warehouse."
                    )
                return (
                    f"No answer was given. The model was shown {len(shown)} tables: {self.tables(shown)}. It said "
                    "they do not hold the answer. That is a statement about these tables, not about the whole warehouse."
                )
            if self.paths.unconnected:
                return "No answer was given: the tables your question matched could not be linked to one another."
            return "No answer was given: nothing in the tables retrieved matched your question."
        return "No answer was given."
