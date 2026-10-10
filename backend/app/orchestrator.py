"""The orchestrator: one question through every stage, and one trace out
(DD-02 rule 1, DD-04, Design sections 03 and 04).

The only component that knows a pipeline exists. The stages never call
one another: each returns what it found, a failure included, and what
happens next is decided here and nowhere else. Every way out of `answer`
goes through one place that assembles the trace, so there is no path that
produces none.

    locate      shell + core   question -> anchors, the tree, warnings
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

WHAT STEP 7 DOES NOT DO: persist the trace, serve it, or give it a query
or user id. The trace here is in memory; step 8 stores it.
"""

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from app.core.conformance import Conformance, Edge, Equality, compare, edges_of, extract_joins
from app.core.prompt_builder import REPLY_NAME, REPLY_SCHEMA, Prompt, build_prompt, parse_reply, retry_message
from app.core.snapshot import SchemaSnapshot
from app.core.sql_reading import schema_of
from app.core.sql_validator import Validation, feedback, validate
from app.shell.model_client import Message, ModelClient, ModelReply, ReplyShape
from app.shell.query_executor import Execution

log = logging.getLogger("cartograph.pipeline")

TRACE_VERSION = 1
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

    @property
    def sql(self) -> str | None:
        """The SQL that ran, or would have."""
        return self.execution.executed if self.execution else None


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Executor(Protocol):
    def run(self, validated) -> Execution: ...


class Orchestrator:
    def __init__(
        self,
        snapshot: SchemaSnapshot,
        schema_ref: tuple[int | None, str],
        locate: Callable[[str], object],
        model: ModelClient,
        executor: Executor,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        if not 0 <= max_retries <= MAX_RETRIES:
            raise ValueError(f"retries are capped at {MAX_RETRIES} per question (NFR-15)")
        self._snapshot = snapshot
        self._schema = schema_of(snapshot)
        self._schema_ref = schema_ref
        self._locate = locate
        self._model = model
        self._executor = executor
        self._max_retries = max_retries

    def answer(self, question: str) -> Trace:
        timings: dict[str, int] = {}
        attempts: list[Attempt] = []
        state: dict = {"located": None, "prompt": None, "validation": None, "conformance": None,
                       "execution": None, "selected": (), "validated": None, "extraction": None}  # fmt: skip
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
                tokens_in=sum(attempt.reply.tokens_in for attempt in attempts),
                tokens_out=sum(attempt.reply.tokens_out for attempt in attempts),
                cost_usd=sum(attempt.reply.cost_usd for attempt in attempts),
                sql_tables=state["extraction"].tables if state["extraction"] else (),
            )

        # ---- locate ------------------------------------------------------
        began = time.monotonic()
        located = self._locate(question)
        state["located"] = located
        tree = located.tree
        if located.declined or not tree.tables:
            reason = tree.decline_reason or "no_anchors"
            stage("locate", began, reason)
            return finish("not_answerable", NOT_ANSWERABLE[reason])
        stage("locate", began, "located")

        # ---- prompt ------------------------------------------------------
        began = time.monotonic()
        prompt = build_prompt(question, self._snapshot, tree.subgraph, tree.joins)
        state["prompt"], state["selected"] = prompt, edges_of(tree.joins)
        stage("prompt", began, "built")

        # ---- generate and validate, at most 1 + max_retries times --------
        shape = ReplyShape(REPLY_NAME, REPLY_SCHEMA)
        messages: tuple[Message, ...] = (Message("system", prompt.system), Message("user", prompt.user))
        validated = None
        for number in range(1, self._max_retries + 2):
            last = number == self._max_retries + 1
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
                return finish("not_answerable", NOT_ANSWERABLE["model"])

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
