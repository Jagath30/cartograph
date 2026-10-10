"""The narrative: the plain-English account of how an answer was reached,
composed once and stored (FR-26, DD-06, DD-19, criterion 14).

Pure (DD-01): the sections of a trace document in, five strings out. It is
run once, when the trace is assembled, and what it returns is stored; no
display composes anything (DD-06). Improving the wording later does not
improve a trace already written, and that cost was accepted.

WHO IT IS FOR. A reader who does not write SQL. So no table or column is
named by its identifier: every name is the readable one built at ingestion
(DD-08), and a join is the Explainer's own sentence. Scores, SQL and
timings are in the trace's technical sections and not here.

THE FIVE PARTS, each a string (the owner's ruling 8), in this order:

  found      what the question matched, what was added to connect it, and
             what else the model was shown.
  route      which route was taken and why. One wording for each of DD-12's
             rules: the only route; the shortest; the question's wording;
             a declared preference, with its reason; or the alphabet, and
             then the word "arbitrary" is in the sentence itself. Joins
             the query made are told in full; the part of the plan it did
             not use gets one sentence.
  not_taken  the routes the choice was between, in full; longer ones as a
             count (ruling 6).
  sql        whether the query kept to the plan: one wording for each
             outcome of the ConformanceCheck, naming the joins left out
             or made instead. `not_checked` never claims the plan was
             kept (T-02).
  result     what came back. A decline lists the tables the model was
             shown, and says it is a statement about those tables and not
             about the warehouse (item 62).

Nothing here decides anything about the answer. It says what the stages
decided, and every sentence is derived from a fact in the trace; nothing
is narrated by a model (DD-14).
"""

from pydantic import BaseModel, ConfigDict


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


def _listed(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _count(number: int, one: str, many: str) -> str:
    return f"{number} {one if number == 1 else many}"


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


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
        # What is told in full: the joins that touched the answer, or the
        # whole plan when no query was compared with it.
        self.told = self.used if self.checked else joined
        self.untold = [a for a in joined if a not in self.told]

    @staticmethod
    def _key(edge) -> tuple:
        return (edge.from_table, tuple(edge.from_columns), edge.to_table, tuple(edge.to_columns))

    def table(self, name: str) -> str:
        return self.names["tables"].get(name, name)

    def column(self, table: str, name: str) -> str:
        return self.names["columns"].get(f"{table}.{name}", name)

    def tables(self, names) -> str:
        return _listed([self.table(name) for name in names])

    # ---- found ------------------------------------------------------------

    def found(self) -> str:
        paths = self.paths
        if not paths.anchors:
            return "No table matched the question closely enough to start from."
        if len(paths.anchors) == 1:
            said = [f"The question matched one table: {self.table(paths.anchors[0])}."]
        else:
            said = [f"The question matched {len(paths.anchors)} tables: {self.tables(paths.anchors)}."]
        if paths.unconnected:
            said.append(
                f"{_capital(self.tables(paths.unconnected))} could not be connected to the rest within "
                f"{_count(paths.max_joins, 'join', 'joins')}."
            )
            return " ".join(said)

        bridges = [name for name in paths.tables if name not in paths.anchors]
        if bridges:
            one = len(bridges) == 1
            said.append(
                f"To connect them, {self.tables(bridges)} {'was' if one else 'were'} added: "
                f"the question did not name {'it' if one else 'them'}."
            )
        dropped = paths.subgraph_bound.dropped_anchors
        if dropped:
            said.append(
                f"{_capital(self.tables(dropped))} also matched and {'was' if len(dropped) == 1 else 'were'} left out "
                f"to keep the plan within {paths.subgraph_bound.limit} tables."
            )
        beside = [node.table for node in self.subgraph.nodes if not node.in_tree] if self.subgraph else []
        if beside:
            said.append(
                f"The model was also shown {self.tables(beside)}, which {'lies' if len(beside) == 1 else 'lie'} "
                "on routes that were equally possible."
            )
        calls = len(self.retrieval.close_calls) if self.retrieval else 0
        if calls:
            said.append(
                f"For {_count(calls, 'word', 'words')} of the question another table scored about as well; "
                f"{'that is' if calls == 1 else 'those are'} listed with the tables retrieved."
            )
        return " ".join(said)

    # ---- route ------------------------------------------------------------

    def route(self) -> str:
        paths = self.paths
        if not paths.tables:
            return "No route was planned."
        joined = [a for a in paths.attachments if a.selected is not None]
        if not joined:
            return f"Only {self.table(paths.tables[0])} was needed, so no join was planned."
        seed = self.table(paths.seed)
        if not self.told:
            return (
                f"The plan starts from {seed} and joins {_listed([a.anchor_readable for a in joined])} to it; "
                "the query used none of the planned joins."
            )
        said = [f"The plan starts from {seed}."]
        for attachment in self.told:
            said.append(
                f"{_capital(attachment.anchor_readable)} is joined to {attachment.attached_to_readable}. "
                f"{attachment.selected.description} {self._why(attachment)}"
            )
        if self.untold:
            one = len(self.untold) == 1
            said.append(
                f"The plan also joined {_listed([a.anchor_readable for a in self.untold])}; "
                f"the query did not use {'that join' if one else 'those joins'}."
            )
        return " ".join(said)

    def _why(self, attachment) -> str:
        """One wording for each rule of DD-12."""
        withdrawn = sum(1 for alternative in attachment.alternatives if alternative.status == "withdrawn")
        routes = 1 + len(attachment.alternatives)
        because = " Also: ".join(_sentence(reason) for reason in attachment.preference_reasons)
        if attachment.rule == "only_path":
            return f"It is the only route between them within {_count(self.paths.max_joins, 'join', 'joins')}."
        if attachment.rule == "shortest":
            return f"{attachment.discovered} routes existed; this is the shortest."
        if attachment.rule == "question_evidence":
            return f"{routes} routes were equally short; the wording of the question pointed to this one."
        if attachment.rule == "preference":
            return _sentence(
                f"{routes} routes were equally short; a preference declared for this warehouse chose this one, "
                f"because: {because}"
            )
        # The alphabet. The word "arbitrary" is in the sentence itself.
        if withdrawn:
            return (
                f"{routes} routes were equally short. "
                + _sentence(f"A preference declared for this warehouse set aside {withdrawn} of them, because: {because}")
                + f" Between the {routes - withdrawn} left nothing said which was meant, so the alphabet chose: "
                "this choice is arbitrary."
            )
        return (
            f"{routes} routes were equally short and nothing said which was meant, so the alphabet chose: "
            "this choice is arbitrary."
        )

    # ---- not taken --------------------------------------------------------

    def not_taken(self) -> str:
        paths = self.paths
        if not paths.tables:
            return "No route was planned, so none was set aside."
        if not any(a.selected is not None for a in paths.attachments):
            return "With one table there was no route to choose."
        said = []
        for attachment in self.told:
            tied = [a for a in attachment.alternatives if a.status == "tied"]
            withdrawn = [a for a in attachment.alternatives if a.status == "withdrawn"]
            if tied:
                ways = "another way" if len(tied) == 1 else f"in {len(tied)} other ways"
                said.append(
                    f"{_capital(attachment.anchor_readable)} could equally have been joined {ways}. "
                    + " ".join(a.description for a in tied)
                )
            if withdrawn:
                said.append(
                    f"A declared preference set aside {len(withdrawn)} more: " + " ".join(a.description for a in withdrawn)
                )
        if not said and self.told:
            said.append("No other route was equally short.")
        longer = sum(max(a.discovered - 1 - len(a.alternatives), 0) for a in self.told)
        if longer:
            said.append(f"{_count(longer, 'longer route', 'longer routes')} also existed and {'was' if longer == 1 else 'were'} not considered.")
        if any(a.alternatives for a in self.untold):
            said.append(
                "Choices were also made in the part of the plan the query did not use; they did not affect this answer."
            )
        elif not self.told:
            said.append("No other route was equally short.")
        if any(warning.state == "other_route_taken" for warning in paths.warnings):
            said.append("The query itself took one of these other routes, not the one planned.")
        return " ".join(said)

    # ---- sql --------------------------------------------------------------

    def _planned(self, edge) -> str:
        through = " and ".join(self.column(edge.from_table, name) for name in edge.from_columns)
        return f"{self.table(edge.from_table)} to {self.table(edge.to_table)} (through {through})"

    def _made(self, edge) -> str:
        return (
            f"{self.table(edge.left_table)} to {self.table(edge.right_table)} (matching "
            f"{self.column(edge.left_table, edge.left_column)} to {self.column(edge.right_table, edge.right_column)})"
        )

    def sql(self) -> str:
        if not self.checked:
            return self._no_query()
        paths = self.paths
        planned = paths.selected_edges
        made = sum(1 for edge in planned if edge.use == "present")
        missing = [edge for edge in planned if edge.use == "missing"]
        joins = _count(len(planned), "join", "joins")
        conformance = self.validation.conformance

        if conformance == "conforms":
            said = f"The query made exactly the {joins} planned." if planned else "No join was planned and the query made none."
        elif conformance == "incomplete":
            if not paths.actual_edges:
                read = self.tables(self.execution.tables) if self.execution and self.execution.tables else "one table"
                said = (
                    f"The query read {read} and joined nothing: the {len(planned)} planned "
                    f"{'join was' if len(planned) == 1 else 'joins were'} not needed."
                )
            else:
                said = (
                    f"The query kept to the plan and did not need all of it: it made {made} of the {len(planned)} "
                    f"planned joins. Left out: {'; '.join(self._planned(edge) for edge in missing)}."
                )
        elif conformance == "diverged":
            parts = ["The query did not keep to the plan."]
            foreign = [edge for edge in paths.actual_edges if edge.foreign]
            if foreign:
                parts.append(f"It joined {'; '.join(self._made(edge) for edge in foreign)}, which the plan did not select.")
            partial = [edge for edge in planned if edge.use == "partial"]
            if partial:
                parts.append(f"It joined {'; '.join(self._planned(edge) for edge in partial)} on only part of the key.")
            for group in paths.cross_joins:
                parts.append(
                    f"It read {self.tables(group)} without joining them, so every row of one is paired with every "
                    "row of the other."
                )
            if paths.unchecked:
                parts.append("Part of it could not be read with confidence.")
            if missing:
                parts.append(f"Planned and not made: {'; '.join(self._planned(edge) for edge in missing)}.")
            said = " ".join(parts)
        else:
            said = (
                "Part of the query could not be read with confidence, so it is not confirmed that it kept to the "
                f"plan. Of what could be read, it made {made} of the {len(planned)} planned joins and none outside them."
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
                return "No answer was given: the tables the question matched could not be joined to one another."
            return "No answer was given: nothing in the tables retrieved matched the question."
        return "No answer was given."
