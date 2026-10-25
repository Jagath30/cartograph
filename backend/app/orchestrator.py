"""The orchestrator: one question through every stage, and one trace out
(DD-02 rule 1, DD-04, Design sections 03 and 04).

The only component that knows a pipeline exists. The stages never call
one another: each returns what it found, a failure included, and what
happens next is decided here and nowhere else. Every way out of `answer`
goes through one place that assembles the trace, so there is no path that
produces none.

    link        shell + core   question -> the tables it needs  (step 10b;
                               only when a linker is given)
    locate      shell + core   question -> anchors, the tree, warnings
    choose      shell + core   the alphabet's choices -> routes named,
                               the tree rebuilt  (v2.1; only when a
                               chooser is given)
    prompt      core           the tree's subgraph and joins -> text
    generate    shell          text -> the model's reply      (up to 3 times)
    validate    core           the reply's SQL -> ValidatedSql, or findings
    conform     core           the SQL's joins against the tree's
    execute     shell          ValidatedSql -> rows

WHAT IS TRIED AGAIN AND WHAT IS NOT (DD-15 as amended at step 7). At most
two retries a question, of any kind (NFR-15). Retried: SQL refused for a
fixable fault, with the findings sent back; a reply that is not the JSON
asked for. Never retried: a write or anything else failing `read_only`
(the question ends, DD-15); a divergence, an incomplete or unchecked
conformance (the SQL runs and the trace says so, DD-13); a reply cut off
by the output cap (at temperature 0 it would be cut off again); a
provider or network failure; an execution error.

THE SQL THAT RUNS IS THE SQL THAT WAS VALIDATED AND CHECKED (rule 3 of
step 7). One ValidatedSql is made; conformance reads its text; the
executor is handed the same object. The trace carries the hash of what
was validated and of what the executor says it sent.

THE TRACE IS WRITTEN ONCE (DD-02 rule 1, FR-27, NFR-13; step 8).
`answer` returns the trace in memory and writes nothing, as at step 7.
`answer_and_keep` goes on: the trace is assembled into the document that
is stored and served (app.core.trace_document), with its narrative and a
query id made here, and handed to the store, which writes the query row
and the trace row in one transaction. Whatever the outcome: a decline or
a failure is written as surely as an answer.

LINKING (step 10b, the owner's rulings at stops A and B). Given a linker,
`answer` first asks a model, shown every table and key, to name the
tables the question needs; an empty list is the decline at linking, with
no generation call. The named tables are located by the linker (the
anchors of the frozen tree), and everything after is as without one. The
two calls share one budget of two retries a question (NFR-15): what
linking spends, generation does not get. A cut-off reply is not retried.
Linking's tokens and cost are the question's. Since v2.1 was adopted
(stop F1), a linked answer is kept as trace version 3, with its linking
and choosing.

CHOOSING (step 10b, v2.1, the owner's rulings at stop A2). Given a chooser
as well, once linking's tables are located: if the tree holds a choice the
alphabet made, one call is shown the question and those choices, and
names for each the alternative the question means, or none. A named route
decides that choice for this question alone and the tree is rebuilt with
it; from the rebuilt tree on, nothing changes. No call when there is no
such choice. Linking, choosing and generation share the budget of two
retries. A choosing call that fails -- cut off, malformed past the budget,
or failed at the model -- is not retried and ends nothing: every choice is
left open, the fault is recorded, and the question goes on with the tree
linking located (option i).

IF THAT WRITE FAILS (the owner's ruling 5). The answer is not returned:
`TraceNotPersisted` is raised, and the API answers 500. An answer whose
trace was lost is what NFR-13 forbids. There is no retry. One log entry
carries the query id, the outcome, the cost and the WHOLE document, so
the record survives where the database refused it. The error's class is
logged and its words are not: a driver's message can carry a connection
string. A trace holds no secret, and tests hold it to that.
"""

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

from app.core.choosing import CHOOSE_REPLY_NAME, OpenChoice, Settled, choices_of, choose_reply_schema, named_routes
from app.core.choosing import parse_choose_reply, settle
from app.core.conformance import Conformance, Edge, Equality, compare, edges_of, extract_joins
from app.core.linking import link_retry_message, parse_link_reply
from app.core.prompt_builder import REPLY_NAME, REPLY_SCHEMA, Prompt, build_prompt, parse_reply, retry_message
from app.core.snapshot import SchemaSnapshot
from app.core.sql_reading import schema_of
from app.core.sql_validator import Validation, feedback, validate
from app.core.trace_document import TRACE_VERSION, Context, TraceDocument, assemble
from app.shell.model_client import Message, ModelClient, ModelReply, ReplyShape
from app.shell.query_executor import Execution

log = logging.getLogger("cartograph.pipeline")

MAX_RETRIES = 2

Outcome = Literal["answered", "not_answerable", "validation_failed", "model_failed", "execution_failed"]

# IR-05's machine-readable codes, as correction 1 of the Design left them.
CODES: dict[str, str | None] = {
    "answered": None,
    "not_answerable": "question_not_answerable",
    "validation_failed": "validation_failure",
    "model_failed": "model_failure",
    "execution_failed": "execution_failure",
}

# What a decline says. Always about the tables retrieved, never "this
# schema": the model saw part of the warehouse, and so did retrieval.
NOT_ANSWERABLE = {
    "no_anchors": "This cannot be answered from the tables retrieved: no table matched the question well enough to start from.",
    "anchors_not_connected": "This cannot be answered from the tables retrieved: the tables the question matched could not be joined to one another.",
    "model": "This cannot be answered from the tables retrieved: the model, shown those tables, said they do not hold the answer.",
    "linking": "This cannot be answered from this schema: shown every table, the model named none that holds the answer.",
}  # fmt: skip
# Given a linker, the tables were named, not retrieved (the owner's ruling
# at stop F2). A linked tree always has anchors, so "no_anchors" never arises.
NOT_ANSWERABLE_NAMED = {
    "anchors_not_connected": "This cannot be answered from the tables named for it: they could not be joined to one another.",
    "model": "This cannot be answered from the tables found for it: the model writing the query, shown those tables, said they do not hold the answer.",
}  # fmt: skip

AttemptOutcome = Literal[
    "accepted", "not_answerable", "malformed", "refused_retryable", "refused_terminal", "cut_off", "model_failed"
]


@dataclass(frozen=True)
class Attempt:
    number: int
    # Every message sent on this attempt, the earlier attempts' included.
    messages: tuple[Message, ...]
    reply: ModelReply
    outcome: AttemptOutcome
    # The SQL the reply held, when it held any.
    sql: str | None = None
    validation: Validation | None = None
    findings: tuple[str, ...] = ()
    # Tables the SQL names that the prompt did not show.
    tables_outside_prompt: tuple[str, ...] = ()


LinkOutcome = Literal["accepted", "none", "malformed", "cut_off", "model_failed"]


@dataclass(frozen=True)
class LinkAttempt:
    number: int
    # Every message sent on this attempt, the earlier attempts' included.
    messages: tuple[Message, ...]
    reply: ModelReply
    outcome: LinkOutcome
    # The tables the reply named, when it was of the form asked for.
    tables: tuple[str, ...] = ()
    fault: str | None = None


@dataclass(frozen=True)
class Linking:
    """How the tables were found, when a linker found them."""

    prompt: Prompt
    attempts: tuple[LinkAttempt, ...]
    # The tables named, () for the decline at linking, None when no reply
    # of the form asked for came back.
    tables: tuple[str, ...] | None


@dataclass(frozen=True)
class Linker:
    """What the link stage needs. `locate` turns the named tables into a
    located question (app.core.linking.locate_linked, in the pipeline)."""

    prompt: Callable[[str], Prompt]
    shape: ReplyShape
    known: frozenset[str]
    model: ModelClient
    locate: Callable[[str, tuple[str, ...]], object]


ChooseOutcome = Literal["accepted", "malformed", "cut_off", "model_failed"]


@dataclass(frozen=True)
class ChooseAttempt:
    number: int
    messages: tuple[Message, ...]
    reply: ModelReply
    outcome: ChooseOutcome
    # The letter named per choice, when the reply was of the form asked for.
    named: tuple[str | None, ...] = ()
    fault: str | None = None


@dataclass(frozen=True)
class Choosing:
    """How the alphabet's choices were put to the model, when a chooser was
    given and the question was located."""

    # The tree as linking located it, before anything was chosen.
    first: object
    # The choices asked about; () when the tree held none and no call was made.
    choices: tuple[OpenChoice, ...]
    prompt: Prompt | None
    attempts: tuple[ChooseAttempt, ...]
    # The letter named per choice; None when no reply of the form asked for
    # came back, and every choice was left open.
    named: tuple[str | None, ...] | None
    settled: tuple[Settled, ...]
    # Alphabetical choices of the rebuilt tree that were never asked about.
    arose: tuple[OpenChoice, ...]
    # Why every choice was left open, when the call failed.
    fault: str | None


@dataclass(frozen=True)
class Chooser:
    """What the choose stage needs. `relocate` locates the linked tables
    again with the routes named (locate_linked's `chosen`, in the pipeline)."""

    prompt: Callable[[str, tuple[OpenChoice, ...]], Prompt]
    model: ModelClient
    relocate: Callable[[str, tuple[str, ...], frozenset[str]], object]


@dataclass(frozen=True)
class Trace:
    trace_version: int
    question: str
    outcome: Outcome
    # IR-05: None when answered.
    code: str | None
    message: str | None
    # snapshot id (None until one is stored) and hash.
    schema_ref: tuple[int | None, str]
    # retrieval, the tree and its explanation: Design section 04's
    # `retrieval`, `subgraph` and `paths` come from here.
    located: object
    prompt: Prompt | None
    # paths.selected, as the edges conformance compared with ...
    selected_edges: tuple[Edge, ...]
    # ... paths.actual_edges, and paths.diverged (None: nothing ran).
    actual_edges: tuple[Equality, ...]
    # The columns the executed SQL's inner joins make equal to one another.
    actual_classes: tuple[frozenset, ...]
    diverged: bool | None
    # generation.attempts
    attempts: tuple[Attempt, ...]
    # validation: of the last attempt that held SQL
    validation: Validation | None
    conformance: Conformance | None
    execution: Execution | None
    # Of the text validated, and of the text the executor says it sent.
    validated_sha256: str | None
    executed_sha256: str | None
    # milliseconds per stage (NFR-22)
    timings: dict[str, int]
    tokens_in: int
    tokens_out: int
    cost_usd: float
    # Every table the validated SQL reads, joined or not.
    sql_tables: tuple[str, ...] = ()
    # Step 10b: how the tables were found, when a linker found them.
    linking: Linking | None = None
    # Step 10b, v2.1: the alphabet's choices put to the model, when a
    # chooser was given.
    choosing: Choosing | None = None

    @property
    def sql(self) -> str | None:
        """The SQL that ran, or would have."""
        return self.execution.executed if self.execution else None


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Executor(Protocol):
    def run(self, validated) -> Execution: ...


class Store(Protocol):
    def save(self, document: TraceDocument) -> None: ...


@dataclass(frozen=True)
class Keep:
    """What the orchestrator needs to write a trace: where, and the facts
    the pipeline's own trace does not hold."""

    store: Store
    context: Context
    # Whose query it is. Until step 11, the one local user.
    user_id: int
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    new_id: Callable[[], UUID] = uuid4


@dataclass(frozen=True)
class Kept:
    # In memory, with the rows: they are returned and never stored (DR-15).
    trace: Trace
    # What was written.
    document: TraceDocument


class TraceNotPersisted(RuntimeError):
    """The pipeline ran and its trace could not be written. The answer is
    not returned (NFR-13)."""

    def __init__(self, query_id: UUID) -> None:
        super().__init__(f"the trace of query {query_id} could not be written; its answer was not returned")
        self.query_id = query_id


class Orchestrator:
    def __init__(
        self,
        snapshot: SchemaSnapshot,
        schema_ref: tuple[int | None, str],
        locate: Callable[[str], object],
        model: ModelClient,
        executor: Executor,
        max_retries: int = MAX_RETRIES,
        keep: Keep | None = None,
        build: Callable[[str, SchemaSnapshot, tuple, tuple | None], Prompt] = build_prompt,
        linker: Linker | None = None,
        chooser: Chooser | None = None,
    ) -> None:
        """`build` makes the prompt from the question, the snapshot, and the
        located tree's subgraph and joins. It is the frozen PromptBuilder's,
        except in step 10's full-schema mode (app.core.full_schema)."""
        if not 0 <= max_retries <= MAX_RETRIES:
            raise ValueError(f"retries are capped at {MAX_RETRIES} per question (NFR-15)")
        if chooser is not None and linker is None:
            raise ValueError("a chooser needs a linker: it rebuilds the tree from the tables linking named")
        self._snapshot = snapshot
        self._schema = schema_of(snapshot)
        self._schema_ref = schema_ref
        self._locate = locate
        self._model = model
        self._executor = executor
        self._max_retries = max_retries
        self._keep = keep
        self._build = build
        self._linker = linker
        self._declined = NOT_ANSWERABLE if linker is None else {**NOT_ANSWERABLE, **NOT_ANSWERABLE_NAMED}
        self._chooser = chooser

    def answer_and_keep(self, question: str) -> Kept:
        """`answer`, then the one write. Raises TraceNotPersisted when the
        store refuses it."""
        keep = self._keep
        if keep is None:
            raise RuntimeError("this orchestrator was given no store to keep a trace in")
        trace = self.answer(question)
        document = assemble(trace, keep.context, keep.new_id(), keep.user_id, keep.now())
        began = time.monotonic()
        try:
            keep.store.save(document)
        except Exception as error:
            log.error(
                json.dumps(
                    {"event": "trace_not_persisted", "query_id": str(document.query_id), "outcome": document.outcome,
                     "cost_usd": document.cost_usd, "error": type(error).__name__,
                     "trace": document.model_dump(mode="json")}
                )
            )  # fmt: skip
            # Not chained: the driver's own words stay out of whatever
            # reports this.
            raise TraceNotPersisted(document.query_id) from None
        log.info(
            json.dumps(
                {"event": "stage", "stage": "persist", "duration_ms": round((time.monotonic() - began) * 1000),
                 "outcome": "written", "query_id": str(document.query_id), "schema": self._schema_ref[1][:12]}
            )
        )  # fmt: skip
        return Kept(trace, document)

    def _choose(self, question, named_tables, located, retries_left, choose_attempts, stage, state):
        """The choose stage. Returns the located question to go on with, and
        the retries left. Never ends the question."""
        chooser = self._chooser
        choices = choices_of(located.tree, question)

        def record(prompt, named, settled=(), arose=(), fault=None):
            state["choosing"] = Choosing(located, choices, prompt, tuple(choose_attempts), named, settled, arose, fault)

        if not choices:
            record(None, None)
            return located, retries_left
        prompt = chooser.prompt(question, choices)
        shape = ReplyShape(CHOOSE_REPLY_NAME, choose_reply_schema(choices))
        messages: tuple[Message, ...] = (Message("system", prompt.system), Message("user", prompt.user))
        left_open = tuple(Settled(c.number, c.anchor, None, "left_open") for c in choices)
        for number in range(1, retries_left + 2):
            began = time.monotonic()
            reply = chooser.model.complete(messages, shape)
            stage("choose", began, reply.failure or "replied")
            if not reply.ok:
                kind: ChooseOutcome = "cut_off" if reply.failure == "cut_off" else "model_failed"
                choose_attempts.append(ChooseAttempt(number, messages, reply, kind, fault=reply.failure))
                record(prompt, None, left_open, fault=reply.failure)
                return located, retries_left
            read = parse_choose_reply(reply.text, choices)
            if read.kind == "malformed":
                choose_attempts.append(ChooseAttempt(number, messages, reply, "malformed", fault=read.fault))
                if retries_left == 0:
                    fault = f"the model's reply was not in the form asked for: {read.fault}"
                    record(prompt, None, left_open, fault=fault)
                    return located, retries_left
                retries_left -= 1
                messages += (
                    Message("assistant", reply.text),
                    Message("user", link_retry_message(f"The reply was not accepted: {read.fault}.")),
                )
                continue
            choose_attempts.append(ChooseAttempt(number, messages, reply, "accepted", read.named))
            break
        chosen = named_routes(choices, read.named)
        rebuilt = located
        if chosen:
            began = time.monotonic()
            rebuilt = chooser.relocate(question, named_tables, chosen)
            stage("choose", began, "rebuilt")
        settled, arose = settle(choices, read.named, rebuilt.tree)
        record(prompt, read.named, settled, arose)
        return rebuilt, retries_left

    def answer(self, question: str) -> Trace:
        timings: dict[str, int] = {}
        attempts: list[Attempt] = []
        state: dict = {"located": None, "prompt": None, "validation": None, "conformance": None,
                       "execution": None, "selected": (), "validated": None, "extraction": None,
                       "linking": None, "choosing": None}  # fmt: skip
        link_attempts: list[LinkAttempt] = []
        choose_attempts: list[ChooseAttempt] = []
        question_id = sha256(question)[:12]

        def stage(name: str, began: float, outcome: str) -> None:
            timings[name] = timings.get(name, 0) + round((time.monotonic() - began) * 1000)
            log.info(
                json.dumps(
                    {"event": "stage", "stage": name, "duration_ms": timings[name], "outcome": outcome,
                     "question": question_id, "schema": self._schema_ref[1][:12]}
                )
            )  # fmt: skip

        def finish(outcome: Outcome, message: str | None = None) -> Trace:
            execution: Execution | None = state["execution"]
            conformance: Conformance | None = state["conformance"]
            validated = state["validated"]
            return Trace(
                trace_version=TRACE_VERSION,
                question=question,
                outcome=outcome,
                code=CODES[outcome],
                message=message,
                schema_ref=self._schema_ref,
                located=state["located"],
                prompt=state["prompt"],
                selected_edges=state["selected"],
                actual_edges=state["extraction"].equalities if state["extraction"] else (),
                actual_classes=state["extraction"].classes if state["extraction"] else (),
                diverged=(conformance.outcome == "diverged") if conformance else None,
                attempts=tuple(attempts),
                validation=state["validation"],
                conformance=conformance,
                execution=execution,
                validated_sha256=sha256(validated.text) if validated else None,
                executed_sha256=sha256(execution.executed) if execution else None,
                timings=dict(timings),
                tokens_in=sum(a.reply.tokens_in for a in (*attempts, *link_attempts, *choose_attempts)),
                tokens_out=sum(a.reply.tokens_out for a in (*attempts, *link_attempts, *choose_attempts)),
                cost_usd=sum(a.reply.cost_usd for a in (*attempts, *link_attempts, *choose_attempts)),
                sql_tables=state["extraction"].tables if state["extraction"] else (),
                linking=state["linking"],
                choosing=state["choosing"],
            )

        def linking_so_far(tables: tuple[str, ...] | None) -> None:
            state["linking"] = Linking(link_prompt, tuple(link_attempts), tables)

        retries_left = self._max_retries

        # ---- link: only when a linker is given ---------------------------
        if self._linker is not None:
            linker = self._linker
            link_prompt = linker.prompt(question)
            link_messages: tuple[Message, ...] = (Message("system", link_prompt.system), Message("user", link_prompt.user))
            linking_so_far(None)
            named: tuple[str, ...] | None = None
            for number in range(1, self._max_retries + 2):
                began = time.monotonic()
                link_reply = linker.model.complete(link_messages, linker.shape)
                stage("link", began, link_reply.failure or "replied")
                if not link_reply.ok:
                    kind: LinkOutcome = "cut_off" if link_reply.failure == "cut_off" else "model_failed"
                    link_attempts.append(LinkAttempt(number, link_messages, link_reply, kind, fault=link_reply.failure))
                    linking_so_far(None)
                    return finish("model_failed", link_reply.message)
                read_link = parse_link_reply(link_reply.text, linker.known)
                if read_link.kind == "malformed":
                    link_attempts.append(LinkAttempt(number, link_messages, link_reply, "malformed", fault=read_link.fault))
                    linking_so_far(None)
                    if retries_left == 0:
                        return finish("model_failed", f"the model's reply was not in the form asked for: {read_link.fault}")
                    retries_left -= 1
                    link_messages += (
                        Message("assistant", link_reply.text),
                        Message("user", link_retry_message(f"The reply was not accepted: {read_link.fault}.")),
                    )
                    continue
                outcome: LinkOutcome = "none" if read_link.kind == "none" else "accepted"
                link_attempts.append(LinkAttempt(number, link_messages, link_reply, outcome, read_link.tables))
                named = read_link.tables
                linking_so_far(named)
                break
            if not named:
                return finish("not_answerable", NOT_ANSWERABLE["linking"])

        # ---- locate ------------------------------------------------------
        began = time.monotonic()
        located = self._locate(question) if self._linker is None else self._linker.locate(question, named)
        state["located"] = located
        tree = located.tree
        if located.declined or not tree.tables:
            reason = tree.decline_reason or "no_anchors"
            stage("locate", began, reason)
            return finish("not_answerable", self._declined[reason])
        stage("locate", began, "located")

        # ---- choose: only with a chooser, and only when the alphabet chose
        if self._chooser is not None:
            located, retries_left = self._choose(question, named, located, retries_left, choose_attempts, stage, state)
            state["located"], tree = located, located.tree

        # ---- prompt ------------------------------------------------------
        began = time.monotonic()
        prompt = self._build(question, self._snapshot, tree.subgraph, tree.joins)
        state["prompt"], state["selected"] = prompt, edges_of(tree.joins)
        stage("prompt", began, "built")

        # ---- generate and validate, at most 1 + max_retries times --------
        shape = ReplyShape(REPLY_NAME, REPLY_SCHEMA)
        messages: tuple[Message, ...] = (Message("system", prompt.system), Message("user", prompt.user))
        validated = None
        for number in range(1, retries_left + 2):
            last = number == retries_left + 1
            began = time.monotonic()
            reply = self._model.complete(messages, shape)
            stage("generate", began, reply.failure or "replied")

            if not reply.ok:
                kind: AttemptOutcome = "cut_off" if reply.failure == "cut_off" else "model_failed"
                attempts.append(Attempt(number, messages, reply, kind, findings=(reply.message or reply.failure,)))
                return finish("model_failed", reply.message)

            read = parse_reply(reply.text)
            if read.kind == "malformed":
                attempts.append(Attempt(number, messages, reply, "malformed", findings=(read.fault,)))
                if last:
                    return finish("model_failed", f"the model's reply was not in the form asked for: {read.fault}")
                messages += (
                    Message("assistant", reply.text),
                    Message("user", retry_message(f"The reply was not accepted: {read.fault}.")),
                )
                continue
            if read.kind == "not_answerable":
                attempts.append(Attempt(number, messages, reply, "not_answerable"))
                return finish("not_answerable", self._declined["model"])

            began = time.monotonic()
            validation = validate(read.sql, self._schema)
            state["validation"] = validation
            outside = tuple(t for t in extract_joins(read.sql, self._schema).tables if t not in prompt.tables)
            said = tuple(finding.message for finding in validation.findings)
            if validation.passed:
                stage("validate", began, "passed")
                attempts.append(Attempt(number, messages, reply, "accepted", read.sql, validation, said, outside))
                validated = validation.validated
                break
            stage("validate", began, "terminal" if validation.terminal else "retryable")
            if validation.terminal:
                attempts.append(Attempt(number, messages, reply, "refused_terminal", read.sql, validation, said, outside))
                return finish("validation_failed", "The generated SQL was not a read-only SELECT. It was not run, and the model was not asked again.")
            attempts.append(Attempt(number, messages, reply, "refused_retryable", read.sql, validation, said, outside))
            if last:
                return finish("validation_failed", f"The generated SQL was refused {number} times and was not run.")
            messages += (Message("assistant", reply.text), Message("user", retry_message(feedback(validation))))

        # ---- conform: flagged, never retried, never a reason not to run ---
        began = time.monotonic()
        state["validated"] = validated
        state["extraction"] = extract_joins(validated.text, self._schema)
        state["conformance"] = compare(state["extraction"], state["selected"])
        stage("conform", began, state["conformance"].outcome)

        # ---- execute: the object validate() sealed, and no other ---------
        began = time.monotonic()
        execution = self._executor.run(validated)
        state["execution"] = execution
        stage("execute", began, execution.failure or "ran")
        if not execution.ok:
            return finish("execution_failed", execution.message)
        return finish("answered")
