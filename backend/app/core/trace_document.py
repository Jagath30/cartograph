"""The trace document: what is stored (DR-09), what crosses the API
(IR-04), and what the panel will render (Design section 04, DD-05, DD-07).

Pure (DD-01). `assemble` turns the trace the orchestrator holds in memory
into this document, and composes the narrative once (DD-06). The models
are Pydantic: a malformed trace cannot be written because one cannot be
constructed (DD-07, IR-11), and the type stored is the type served.

trace_version 1. Section 04's shape, with what the build has changed:

  schema_ref    the snapshot's id and hash, and the hash of the
                preferences in force (item 70): the snapshot hash does not
                cover them, and two runs over one snapshot can select
                different joins.
  settings      the settings in force, so that two traces can be compared.
  paths         the system selects a TREE, so this holds attachments, not
                one selected path. Each carries the route chosen and every
                route the choice was between -- tied, or withdrawn by a
                declared preference -- in full, with its plain-English
                sentence. Longer routes are a COUNT (`discovered`): an
                amendment owed to FR-13 and DD-21 (the owner's ruling 6;
                item 34). DD-21's own quiet wording needs only the count.
  paths.warnings
                each marked against the joins the executed SQL made (item
                64; DD-21 as amended). See app.core.warning_marks.
  generation.tables_shown
                the tables the model was shown. A decline is a statement
                about these and no others (item 62).
  execution     the row count, never the rows (DR-15).
  narrative     five labelled strings (ruling 8). See app.core.narrative.

Every section but question, outcome and timings is optional, and the
document says what is absent by leaving it out: a question that could not
be located has no generation, validation or execution.

NOTHING HERE IS A SECRET, and that is checked by a test: no key, no
password, no connection string. A model failure's message comes from a
fixed table; the prompt is the schema and the question.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import networkx as nx
from pydantic import BaseModel, ConfigDict

from app.core.conformance import Edge, edges_of
from app.core.explainer import describe_path
from app.core.narrative import Narrative, narrate
from app.core.snapshot import SchemaSnapshot
from app.core.warning_marks import Ran, had_ambiguity, mark_warnings

TRACE_VERSION = 1
# How many scored elements are kept, best first. Every table's score is
# kept whatever this is.
CANDIDATES_KEPT = 50

Outcome = Literal["answered", "not_answerable", "validation_failed", "model_failed", "execution_failed"]
ConformanceOutcome = Literal["conforms", "diverged", "incomplete", "not_checked"]
WarningState = Literal["followed", "other_route_taken", "not_used", "unknown", "not_applicable"]
EdgeUse = Literal["present", "partial", "missing"]


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SchemaRef(Section):
    snapshot_id: int | None
    hash: str
    preferences_hash: str


class SettingsInForce(Section):
    alpha: float | None
    anchor_cut: float | None
    anchor_cap: int | None
    margin: float | None
    subgraph_bound: int
    max_joins: int
    model: str | None
    temperature: float | None


class Candidate(Section):
    kind: Literal["table", "column"]
    name: str
    table: str
    column: str | None
    semantic_score: float
    keyword_score: float
    combined: float
    rank: int


class TableScore(Section):
    table: str
    score: float
    best: str


class AnchorBound(Section):
    cut: float
    cap: int
    excluded_by_cap: list[str]
    set_aside_as_rivals: list[str]


class Considered(Section):
    element: str
    score: float


class Rival(Section):
    table: str
    element: str
    score: float


class TermAlternative(Section):
    """FR-41: what one term of the question was weighed against."""

    term: str
    kind: str
    chosen_table: str
    chosen_element: str
    considered: list[Considered]
    # Tables within the margin and not joined to the one chosen ...
    rivals: list[Rival]
    # ... and joined to it: never rivals, recorded so nothing is left out.
    partners: list[Rival]


class CloseCall(Section):
    term: str
    chosen: str
    rival: str
    chosen_score: float
    rival_score: float
    text: str


class Retrieval(Section):
    anchors: list[str]
    tables: list[TableScore]
    candidates: list[Candidate]
    # How many elements were scored; `candidates` holds the best of them.
    candidates_total: int
    anchor_bound: AnchorBound
    term_alternatives: list[TermAlternative]
    close_calls: list[CloseCall]


class Column(Section):
    name: str
    data_type: str
    readable: str
    primary_key: bool
    foreign_key: bool


class Node(Section):
    table: str
    readable: str
    anchor: bool
    # False for a table shown only because a tied route passes through it.
    in_tree: bool
    columns: list[Column]


class SchemaEdge(Section):
    """One foreign key, from the table that holds it."""

    from_table: str
    from_columns: list[str]
    to_table: str
    to_columns: list[str]
    source: Literal["catalog", "overlay"]
    constraint: str | None = None
    note: str | None = None


class Subgraph(Section):
    """What the model was handed: a copy, not a reference (DD-05)."""

    nodes: list[Node]
    edges: list[SchemaEdge]


class Join(SchemaEdge):
    walked: str
    description: str


class Route(Section):
    id: str
    tables: list[str]
    length: int
    joins: list[Join]
    description: str


class Alternative(Route):
    # tied: the rule could not tell it from the route chosen.
    # withdrawn: a declared preference set it aside.
    status: Literal["tied", "withdrawn"]


class Attachment(Section):
    anchor: str
    anchor_readable: str
    order: int
    attached_to: str | None
    attached_to_readable: str | None
    rule: str | None
    reason: str
    # The reasons given with the declared preferences that took part.
    preference_reasons: list[str]
    # Routes of any length between the anchor and where it could attach.
    discovered: int
    selected: Route | None
    alternatives: list[Alternative]


class SelectedEdge(SchemaEdge):
    # Whether the executed SQL makes this join. None when no SQL was checked.
    use: EdgeUse | None


class ActualEdge(Section):
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    outer: bool
    union: bool
    # Not a join the tree selected, nor implied by one.
    foreign: bool


class Unchecked(Section):
    reason: str
    detail: str


class Warning(Section):
    code: str
    text: str
    about: list[str]
    # The joins it is about, and those of the routes it was tied with.
    joins: list[str]
    other_joins: list[str]
    state: WarningState
    loud: bool


class SubgraphBound(Section):
    limit: int
    dropped_anchors: list[str]
    excluded: list[str]


class Paths(Section):
    declined: bool
    decline_reason: str | None
    unconnected: list[str]
    max_joins: int
    anchors: list[str]
    seed: str | None
    tables: list[str]
    attachments: list[Attachment]
    selected_edges: list[SelectedEdge]
    actual_edges: list[ActualEdge]
    actual_classes: list[list[str]]
    cross_joins: list[list[str]]
    unchecked: list[Unchecked]
    # None: nothing was checked.
    diverged: bool | None
    subgraph_bound: SubgraphBound
    warnings: list[Warning]


class Message(Section):
    role: str
    content: str


class Attempt(Section):
    number: int
    outcome: str
    # What was sent on this attempt beyond the attempt before: nothing on
    # the first, then the refused reply and what was said about it.
    messages_added: list[Message]
    requested_model: str
    returned_model: str | None
    fingerprint: str | None
    reply: str | None
    sql: str | None
    tokens_in: int
    tokens_cached: int
    tokens_out: int
    cost_usd: float
    duration_ms: int
    findings: list[str]
    tables_outside_prompt: list[str]
    failure: str | None


class Generation(Section):
    prompt_system: str
    prompt_user: str
    tables_shown: list[str]
    attempts: list[Attempt]


class Validation(Section):
    syntax: str
    read_only: str
    references: str
    findings: list[str]
    conformance: ConformanceOutcome | None
    conformance_findings: list[str]
    validated_sha256: str | None
    executed_sha256: str | None


class Execution(Section):
    sql: str
    # Every table the SQL reads, joined or not.
    tables: list[str]
    columns: list[str]
    row_count: int
    truncated: bool
    row_cap: int
    duration_ms: int
    statement_timeout: str | None
    failure: str | None
    sqlstate: str | None
    error: str | None


class TraceDocument(Section):
    trace_version: int
    query_id: UUID
    user_id: int
    question: str
    created_at: datetime
    outcome: Outcome
    # IR-05: None when answered.
    code: str | None
    message: str | None
    schema_ref: SchemaRef
    settings: SettingsInForce
    retrieval: Retrieval | None
    subgraph: Subgraph | None
    paths: Paths | None
    generation: Generation | None
    validation: Validation | None
    execution: Execution | None
    timings: dict[str, int]
    tokens_in: int
    tokens_out: int
    cost_usd: float
    # DD-07's extracted columns that are not already above.
    had_ambiguity: bool
    duration_ms: int
    narrative: Narrative

    @property
    def conformance_result(self) -> str | None:
        return self.validation.conformance if self.validation else None


@dataclass(frozen=True)
class Context:
    """What `assemble` needs that the pipeline's trace does not hold."""

    snapshot: SchemaSnapshot
    graph: nx.DiGraph
    preferences_hash: str
    subgraph_bound: int
    max_joins: int
    model: str | None = None
    temperature: float | None = None


def preferences_hash(snapshot: SchemaSnapshot) -> str:
    """The preferences in force, of both kinds, with their reasons, in one
    fixed order (item 70). Of what was loaded and not of the file's bytes:
    a comment edited in the overlay changes nothing that is selected."""
    content = {
        "preferences": sorted(
            ({"between": list(p.between), "prefer": sorted(list(pair) for pair in p.prefer), "because": p.because}
             for p in snapshot.preferences),
            key=lambda entry: json.dumps(entry, sort_keys=True),
        ),
        "attach_preferences": sorted(
            (asdict(p) for p in snapshot.attach_preferences), key=lambda entry: json.dumps(entry, sort_keys=True)
        ),
    }  # fmt: skip
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def edge_text(edge: Edge) -> str:
    return " and ".join(f"{a[0]}.{a[1]} = {b[0]}.{b[1]}" for a, b in edge)


def assemble(trace, context: Context, query_id: UUID, user_id: int, created_at: datetime) -> TraceDocument:
    """`trace` is app.orchestrator.Trace. Nothing is decided here: every
    value was decided by a stage, and is read off."""
    located = trace.located
    tree = located.tree
    graph = context.graph

    def table(name: str) -> str:
        return graph.nodes[name]["readable"] or name

    def column(table_name: str, name: str) -> str:
        readable = graph.nodes[f"{table_name}.{name}"]["readable"] or name
        return readable.split(" — ", 1)[-1]

    answered = trace.outcome == "answered"
    ran = None
    if answered and trace.conformance is not None:
        ran = Ran(trace.actual_edges, trace.actual_classes, bool(trace.conformance.unchecked))
    marks = mark_warnings(tree, located.explanation.warnings, ran)

    retrieval = _retrieval(located)
    subgraph = _subgraph(tree, context.snapshot, table) if tree.subgraph else None
    paths = _paths(trace, marks, table, graph)
    generation = _generation(trace) if trace.prompt is not None else None
    validation = _validation(trace)
    execution = _execution(trace)
    # Readable names for the narrative, which names nothing by identifier.
    names = {
        "tables": {entry.name: table(entry.name) for entry in context.snapshot.tables},
        "columns": {f"{c.table}.{c.name}": column(c.table, c.name) for c in context.snapshot.columns},
    }
    narrative = narrate(
        outcome=trace.outcome, message=trace.message, retrieval=retrieval, subgraph=subgraph, paths=paths,
        generation=generation, validation=validation, execution=execution, names=names,
    )  # fmt: skip

    settings = located.retrieval.settings if located.retrieval is not None else None
    return TraceDocument(
        trace_version=TRACE_VERSION,
        query_id=query_id,
        user_id=user_id,
        question=trace.question,
        created_at=created_at,
        outcome=trace.outcome,
        code=trace.code,
        message=trace.message,
        schema_ref=SchemaRef(
            snapshot_id=trace.schema_ref[0], hash=trace.schema_ref[1], preferences_hash=context.preferences_hash
        ),
        settings=SettingsInForce(
            alpha=settings.alpha if settings else None,
            anchor_cut=settings.anchor_cut if settings else None,
            anchor_cap=settings.anchor_cap if settings else None,
            margin=settings.margin if settings else None,
            subgraph_bound=context.subgraph_bound,
            max_joins=context.max_joins,
            model=context.model,
            temperature=context.temperature,
        ),
        retrieval=retrieval,
        subgraph=subgraph,
        paths=paths,
        generation=generation,
        validation=validation,
        execution=execution,
        timings=dict(trace.timings),
        tokens_in=trace.tokens_in,
        tokens_out=trace.tokens_out,
        cost_usd=trace.cost_usd,
        had_ambiguity=had_ambiguity(marks),
        duration_ms=sum(trace.timings.values()),
        narrative=narrative,
    )


def _retrieval(located) -> Retrieval | None:
    found = located.retrieval
    if found is None:
        return None

    def others(entries) -> list[Rival]:
        return [Rival(table=e.rival, element=e.rival_element, score=e.rival_score) for e in entries]

    return Retrieval(
        anchors=list(found.anchors),
        tables=[TableScore(table=t.table, score=t.score, best=t.best) for t in found.tables],
        candidates=[
            Candidate(
                kind=c.kind, name=c.element, table=c.table, column=c.column, semantic_score=c.semantic,
                keyword_score=c.keyword, combined=c.combined, rank=c.rank,
            )
            for c in found.candidates[:CANDIDATES_KEPT]
        ],  # fmt: skip
        candidates_total=len(found.candidates),
        anchor_bound=AnchorBound(
            cut=found.anchor_bound.cut,
            cap=found.anchor_bound.cap,
            excluded_by_cap=list(found.anchor_bound.excluded_by_cap),
            set_aside_as_rivals=list(found.anchor_bound.set_aside_as_rivals),
        ),
        term_alternatives=[
            TermAlternative(
                term=t.term.text, kind=t.term.kind, chosen_table=t.chosen_table, chosen_element=t.chosen_element,
                considered=[Considered(element=element, score=score) for element, score in t.considered],
                rivals=others(t.rivals), partners=others(t.partners),
            )
            for t in found.terms
        ],  # fmt: skip
        close_calls=[
            CloseCall(term=c.term, chosen=c.chosen, rival=c.rival, chosen_score=c.chosen_score,
                      rival_score=c.rival_score, text=c.text)
            for c in located.explanation.close_calls
        ],  # fmt: skip
    )


def _subgraph(tree, snapshot: SchemaSnapshot, table) -> Subgraph:
    shown = set(tree.subgraph)
    primary = {(key.table, name) for key in snapshot.primary_keys for name in key.columns}
    keys = [key for key in snapshot.foreign_keys if key.from_table in shown and key.to_table in shown]
    foreign = {(key.from_table, name) for key in snapshot.foreign_keys for name in key.from_columns}
    nodes = [
        Node(
            table=name,
            readable=table(name),
            anchor=name in tree.anchors,
            in_tree=name in tree.tables,
            columns=[
                Column(
                    name=c.name, data_type=c.data_type, readable=c.readable or c.name,
                    primary_key=(name, c.name) in primary, foreign_key=(name, c.name) in foreign,
                )
                for c in snapshot.columns
                if c.table == name
            ],  # fmt: skip
        )
        for name in tree.subgraph
    ]
    edges = [
        SchemaEdge(
            from_table=key.from_table, from_columns=list(key.from_columns), to_table=key.to_table,
            to_columns=list(key.to_columns), source=key.source, constraint=key.name, note=key.note,
        )
        for key in keys
    ]  # fmt: skip
    return Subgraph(nodes=nodes, edges=edges)


def _join_fields(join) -> dict:
    return {
        "from_table": join.fk_table, "from_columns": list(join.fk_columns), "to_table": join.pk_table,
        "to_columns": list(join.pk_columns), "source": join.source, "constraint": join.constraint, "note": join.note,
    }  # fmt: skip


def _route(path) -> dict:
    return {
        "id": path.id,
        "tables": list(path.tables),
        "length": path.length,
        "joins": [Join(**_join_fields(join), walked=join.walked, description=join.description) for join in path.joins],
        "description": path.description,
    }


def _paths(trace, marks, table, graph) -> Paths:
    tree = trace.located.tree
    explanation = trace.located.explanation
    conformance = trace.conformance

    use: dict[Edge, str] = {}
    if conformance is not None:
        for name in ("present", "partial", "missing"):
            use.update({edge: name for edge in getattr(conformance, name)})
    foreign = set(conformance.foreign) if conformance is not None else set()

    attachments = []
    for raw, told in zip(tree.attachments, explanation.attachments):
        declared = [p.because for p in (*raw.attach_preferences, *raw.route_preferences)]
        if raw.preference_applied is not None:
            declared.append(raw.preference_applied.because)
        # The Explainer lists what was tied; what a preference withdrew is
        # on the tree's own record of the attachment, and is put into the
        # Explainer's words by the Explainer (DD-06).
        alternatives = [Alternative(**_route(path), status="tied") for path in told.alternatives]
        alternatives += [
            Alternative(**_route(describe_path(path, graph)), status="withdrawn") for path in raw.withdrawn
        ]
        attachments.append(
            Attachment(
                anchor=raw.anchor,
                anchor_readable=table(raw.anchor),
                order=raw.order,
                attached_to=raw.attached_to,
                attached_to_readable=table(raw.attached_to) if raw.attached_to else None,
                rule=raw.rule,
                reason=told.reason.text,
                preference_reasons=declared,
                discovered=raw.discovered,
                selected=Route(**_route(told.path)) if told.path is not None else None,
                alternatives=alternatives,
            )
        )

    return Paths(
        declined=tree.declined,
        decline_reason=tree.decline_reason,
        unconnected=list(tree.unconnected),
        max_joins=tree.max_joins,
        anchors=list(tree.anchors),
        seed=tree.seed,
        tables=list(tree.tables),
        attachments=attachments,
        selected_edges=[
            SelectedEdge(**_join_fields(join), use=use.get(edge))
            for join, edge in zip(tree.joins, edges_of(tree.joins))
        ],
        actual_edges=[
            ActualEdge(
                left_table=e.left[0], left_column=e.left[1], right_table=e.right[0], right_column=e.right[1],
                outer=e.outer, union=e.union, foreign=e in foreign,
            )
            for e in trace.actual_edges
        ],  # fmt: skip
        actual_classes=[sorted(f"{t}.{c}" for t, c in members) for members in trace.actual_classes],
        cross_joins=[list(group) for group in conformance.cross_joins] if conformance else [],
        unchecked=[Unchecked(reason=u.reason, detail=u.detail) for u in conformance.unchecked] if conformance else [],
        diverged=trace.diverged,
        subgraph_bound=SubgraphBound(
            limit=tree.subgraph_bound.limit,
            dropped_anchors=list(tree.subgraph_bound.dropped_anchors),
            excluded=list(tree.subgraph_bound.excluded),
        ),
        warnings=[
            Warning(
                code=m.code, text=m.text, about=list(m.about), joins=[edge_text(e) for e in m.joins],
                other_joins=[edge_text(e) for e in m.other_joins], state=m.state, loud=m.loud,
            )
            for m in marks
        ],  # fmt: skip
    )


def _generation(trace) -> Generation:
    attempts = []
    sent = 2
    for attempt in trace.attempts:
        reply = attempt.reply
        attempts.append(
            Attempt(
                number=attempt.number,
                outcome=attempt.outcome,
                messages_added=[Message(role=m.role, content=m.content) for m in attempt.messages[sent:]],
                requested_model=reply.requested_model,
                returned_model=reply.returned_model,
                fingerprint=reply.fingerprint,
                reply=reply.text,
                sql=attempt.sql,
                tokens_in=reply.tokens_in,
                tokens_cached=reply.tokens_cached,
                tokens_out=reply.tokens_out,
                cost_usd=reply.cost_usd,
                duration_ms=reply.duration_ms,
                findings=list(attempt.findings),
                tables_outside_prompt=list(attempt.tables_outside_prompt),
                failure=reply.failure,
            )
        )
        sent = len(attempt.messages)
    return Generation(
        prompt_system=trace.prompt.system,
        prompt_user=trace.prompt.user,
        tables_shown=list(trace.prompt.tables),
        attempts=attempts,
    )


def _validation(trace) -> Validation | None:
    validation = trace.validation
    if validation is None:
        return None
    conformance = trace.conformance
    return Validation(
        syntax=validation.syntax,
        read_only=validation.read_only,
        references=validation.references,
        findings=[finding.message for finding in validation.findings],
        conformance=conformance.outcome if conformance else None,
        conformance_findings=list(conformance.findings) if conformance else [],
        validated_sha256=trace.validated_sha256,
        executed_sha256=trace.executed_sha256,
    )


def _execution(trace) -> Execution | None:
    execution = trace.execution
    if execution is None:
        return None
    return Execution(
        sql=execution.executed,
        tables=list(trace.sql_tables),
        columns=list(execution.columns),
        row_count=execution.row_count,
        truncated=execution.truncated,
        row_cap=execution.row_cap,
        duration_ms=execution.duration_ms,
        statement_timeout=execution.statement_timeout,
        failure=execution.failure,
        sqlstate=execution.sqlstate,
        error=execution.message,
    )
