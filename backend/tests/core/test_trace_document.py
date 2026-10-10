"""The trace document at trace_version 1 (Design section 04, DD-05, DD-07,
DR-09, DR-15, IR-04; items 34, 62, 64 and 70).

Assembled from the orchestrator's in-memory trace by a pure function. The
trees are the real JoinTree's on TPC-DS; the SQL is written by hand.
"""

import json
from dataclasses import replace

from tracing import CONTEXT, QUERY_ID, SNAPSHOT, USER_ID, WHEN, Runs, document, located, reply

from app.core.snapshot import AttachPreference, Preference
from app.core.trace_document import CANDIDATES_KEPT, TraceDocument, preferences_hash
from app.shell.model_client import FAILURES, ModelReply

ADDRESS = ("catalog_sales", "customer_address")
BILL = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"
SHIP = BILL.replace("cs_bill_addr_sk", "cs_ship_addr_sk")
D7 = ("catalog_sales", "catalog_returns", "customer_demographics", "customer", "date_dim")


def test_an_answered_question_has_every_section() -> None:
    made = document([reply(BILL)], *ADDRESS, question="Where are catalogue buyers billed?")
    assert made.trace_version == 1 and made.outcome == "answered" and made.code is None
    assert (made.query_id, made.user_id, made.created_at) == (QUERY_ID, USER_ID, WHEN)
    assert made.question == "Where are catalogue buyers billed?"
    for section in ("subgraph", "paths", "generation", "validation", "execution"):
        assert getattr(made, section) is not None, section
    assert made.validation.conformance == "conforms" and made.conformance_result == "conforms"
    assert made.duration_ms == sum(made.timings.values())
    assert made.narrative.result == "2 rows came back."


def test_it_survives_json_unchanged() -> None:
    """What is stored is what is served: the body read back is the
    document that was written, field for field."""
    made = document([reply(BILL)], *ADDRESS)
    body = json.loads(made.model_dump_json())
    assert TraceDocument.model_validate(body) == made
    assert json.loads(TraceDocument.model_validate(body).model_dump_json()) == body


def test_a_field_nobody_declared_is_refused() -> None:
    """DD-07: a malformed trace cannot be constructed."""
    import pytest
    from pydantic import ValidationError

    body = json.loads(document([reply(BILL)], *ADDRESS).model_dump_json())
    with pytest.raises(ValidationError):
        TraceDocument.model_validate(body | {"rows": [[1]]})
    with pytest.raises(ValidationError):
        TraceDocument.model_validate(body | {"outcome": "probably"})


# ---- schema_ref and the preferences in force (item 70) ---------------------


def test_schema_ref_carries_the_snapshot_and_the_hash_of_the_preferences() -> None:
    ref = document([reply(BILL)], *ADDRESS).schema_ref
    assert (ref.snapshot_id, ref.hash) == (7, "abc123")
    assert ref.preferences_hash == preferences_hash(SNAPSHOT) and len(ref.preferences_hash) == 64


def test_the_preferences_hash_changes_with_any_preference_and_not_with_their_order() -> None:
    base = preferences_hash(SNAPSHOT)
    assert len(SNAPSHOT.preferences) >= 2 and len(SNAPSHOT.attach_preferences) >= 2
    reordered = replace(
        SNAPSHOT, preferences=SNAPSHOT.preferences[::-1], attach_preferences=SNAPSHOT.attach_preferences[::-1]
    )
    assert preferences_hash(reordered) == base
    assert preferences_hash(replace(SNAPSHOT, preferences=SNAPSHOT.preferences[1:])) != base
    assert preferences_hash(replace(SNAPSHOT, attach_preferences=SNAPSHOT.attach_preferences[1:])) != base
    first = SNAPSHOT.preferences[0]
    reworded = (Preference(first.between, first.prefer, first.because + " Reworded."), *SNAPSHOT.preferences[1:])
    assert preferences_hash(replace(SNAPSHOT, preferences=reworded)) != base
    turned = SNAPSHOT.attach_preferences[0]
    other = (AttachPreference(turned.rather_than, turned.attach_to, turned.because), *SNAPSHOT.attach_preferences[1:])
    assert preferences_hash(replace(SNAPSHOT, attach_preferences=other)) != base


def test_no_preferences_at_all_still_has_a_hash() -> None:
    none = preferences_hash(replace(SNAPSHOT, preferences=(), attach_preferences=()))
    assert len(none) == 64 and none != preferences_hash(SNAPSHOT)


def test_the_settings_in_force_are_recorded() -> None:
    settings = document([reply(BILL)], *ADDRESS).settings
    assert (settings.subgraph_bound, settings.max_joins, settings.model, settings.temperature) == (10, 3, "fake-scripted", 0.0)


# ---- the subgraph is a copy (DD-05) ----------------------------------------


def test_the_subgraph_holds_its_own_nodes_columns_and_edges() -> None:
    subgraph = document([reply(BILL)], *ADDRESS).subgraph
    assert [node.table for node in subgraph.nodes] == ["catalog_sales", "customer_address"]
    sales = subgraph.nodes[0]
    assert sales.readable == "catalog sales" and sales.anchor and sales.in_tree
    assert len(sales.columns) == sum(1 for column in SNAPSHOT.columns if column.table == "catalog_sales")
    key = next(column for column in sales.columns if column.name == "cs_bill_addr_sk")
    assert key.foreign_key and not key.primary_key and "bill address" in key.readable
    edges = {(edge.from_table, edge.from_columns[0], edge.to_table, edge.source) for edge in subgraph.edges}
    assert ("catalog_sales", "cs_bill_addr_sk", "customer_address", "overlay") in edges
    assert ("catalog_sales", "cs_ship_addr_sk", "customer_address", "overlay") in edges
    assert all(edge.from_table in ADDRESS and edge.to_table in ADDRESS for edge in subgraph.edges)


def test_a_table_shown_only_for_a_tied_route_is_marked_as_outside_the_tree() -> None:
    subgraph = document([reply("SELECT i.i_category FROM item i")], "item", "date_dim").subgraph
    outside = [node.table for node in subgraph.nodes if not node.in_tree]
    assert "store_sales" in outside and "item" not in outside


# ---- paths: a tree, with the routes the choice was between (item 34) -------


def test_each_attachment_carries_its_route_its_rule_and_its_reason() -> None:
    paths = document([reply(BILL)], *ADDRESS).paths
    seed, attached = paths.attachments
    assert (seed.anchor, seed.selected, seed.rule, seed.alternatives) == ("catalog_sales", None, None, [])
    assert (attached.anchor, attached.attached_to, attached.rule) == ("customer_address", "catalog_sales", "alphabetical")
    assert attached.anchor_readable == "customer address" and attached.attached_to_readable == "catalog sales"
    assert "arbitrary" in attached.reason
    assert attached.selected.id == "catalog_sales.cs_bill_addr_sk=customer_address.ca_address_sk"
    assert attached.selected.joins[0].source == "overlay" and attached.selected.description.startswith("Each customer address")


def test_the_routes_the_choice_was_between_are_in_full_and_longer_ones_are_a_count() -> None:
    """Ruling 6, an amendment owed to FR-13 and DD-21."""
    attached = document([reply(BILL)], *ADDRESS).paths.attachments[1]
    (alternative,) = attached.alternatives
    assert alternative.status == "tied"
    assert alternative.id == "catalog_sales.cs_ship_addr_sk=customer_address.ca_address_sk"
    assert alternative.joins and "ship address" in alternative.description
    assert attached.discovered == 153


def test_what_a_preference_withdrew_is_recorded_with_its_sentence_and_its_reason() -> None:
    paths = document([reply("SELECT COUNT(*) FROM catalog_sales")], *D7).paths
    demographics = next(a for a in paths.attachments if a.anchor == "customer_demographics")
    assert [a.status for a in demographics.alternatives] == ["tied", "withdrawn", "withdrawn"]
    assert all("catalog returns" in a.description for a in demographics.alternatives if a.status == "withdrawn")
    assert len(demographics.preference_reasons) == 1 and demographics.preference_reasons[0]


def test_the_selected_edges_say_which_the_sql_made() -> None:
    paths = document(
        [reply("SELECT COUNT(*) FROM catalog_sales cs JOIN customer_demographics cd ON cs.cs_bill_cdemo_sk = cd.cd_demo_sk")], *D7
    ).paths
    use = {(edge.from_columns[0]): edge.use for edge in paths.selected_edges}
    assert use["cs_bill_cdemo_sk"] == "present" and use["cs_sold_date_sk"] == "missing"
    assert [(e.left_column, e.right_column, e.foreign) for e in paths.actual_edges] == [("cs_bill_cdemo_sk", "cd_demo_sk", False)]
    assert paths.diverged is False


def test_a_divergence_is_recorded_with_both_joins() -> None:
    paths = document([reply(SHIP)], *ADDRESS).paths
    assert paths.diverged is True and paths.selected_edges[0].use == "missing"
    assert [(e.left_column, e.foreign) for e in paths.actual_edges] == [("cs_ship_addr_sk", True)]


def test_before_any_sql_is_checked_the_selected_edges_have_no_use() -> None:
    paths = document([reply(status="not_answerable")], *ADDRESS).paths
    assert [edge.use for edge in paths.selected_edges] == [None] and paths.diverged is None


# ---- warnings, marked (item 64) --------------------------------------------


def test_each_warning_carries_its_mark_and_the_extracted_column_follows() -> None:
    followed = document([reply(BILL)], *ADDRESS)
    (warning,) = followed.paths.warnings
    assert (warning.code, warning.state, warning.loud) == ("arbitrary_choice", "followed", True)
    assert warning.joins == ["catalog_sales.cs_bill_addr_sk = customer_address.ca_address_sk"]
    assert warning.other_joins == ["catalog_sales.cs_ship_addr_sk = customer_address.ca_address_sk"]
    assert warning.text.startswith("I had no basis for this choice.") and followed.had_ambiguity is True

    other = document([reply(SHIP)], *ADDRESS)
    assert other.paths.warnings[0].state == "other_route_taken" and other.had_ambiguity is True

    unused = document([reply("SELECT COUNT(*) FROM catalog_sales")], *ADDRESS)
    assert (unused.paths.warnings[0].state, unused.paths.warnings[0].loud) == ("not_used", False)
    assert unused.had_ambiguity is False


def test_nothing_is_loud_when_no_answer_was_given() -> None:
    """A decline, and a query the database stopped: no answer, so no
    warning is about one."""
    for made in (
        document([reply(status="not_answerable")], *ADDRESS),
        document([reply(BILL)], *ADDRESS, executor=Runs(failure="database_error")),
    ):
        assert [warning.state for warning in made.paths.warnings] == ["not_applicable"]
        assert made.had_ambiguity is False


# ---- generation; a decline lists the tables shown (item 62) ----------------


def test_a_decline_lists_the_tables_the_model_was_shown() -> None:
    made = document([reply(status="not_answerable")], *ADDRESS)
    assert made.outcome == "not_answerable" and made.code == "question_not_answerable"
    assert made.generation.tables_shown == ["catalog_sales", "customer_address"]
    assert made.validation is None and made.execution is None
    assert "catalog sales and customer address" in made.narrative.result


def test_the_prompt_is_stored_once_and_each_attempt_adds_only_what_was_added() -> None:
    made = document([reply("SELECT nothing FROM catalog_sales"), reply(BILL)], *ADDRESS)
    generation = made.generation
    assert "CREATE TABLE catalog_sales (" in generation.prompt_user and generation.prompt_system
    first, second = generation.attempts
    assert (first.outcome, first.messages_added) == ("refused_retryable", [])
    assert [message.role for message in second.messages_added] == ["assistant", "user"]
    assert second.outcome == "accepted" and second.sql == BILL and first.findings
    assert generation.prompt_user not in json.dumps([m.model_dump() for m in second.messages_added])


def test_a_model_failure_has_no_validation_and_says_what_failed() -> None:
    failed = ModelReply("m", None, None, None, 0, 0, 0, 0.0, 0, "transport", FAILURES["transport"])
    made = document([failed], *ADDRESS)
    assert (made.outcome, made.code, made.message) == ("model_failed", "model_failure", FAILURES["transport"])
    assert made.generation.attempts[0].failure == "transport" and made.validation is None


def test_a_question_that_matched_nothing_has_no_generation() -> None:
    made = document([], "income_band", "ship_mode", max_joins=1)
    assert made.outcome == "not_answerable" and made.generation is None and made.subgraph is None
    assert made.paths.declined and made.paths.unconnected == ["ship_mode"]
    assert [warning.code for warning in made.paths.warnings] == ["no_path"]


# ---- execution: the count and never the rows (DR-15) -----------------------


def test_the_rows_are_not_in_the_document() -> None:
    class Telling(Runs):
        def run(self, validated):
            ran = super().run(validated)
            return replace(ran, columns=("state",), rows=(("ZZ-SENTINEL-ROW",), ("QQ-OTHER-ROW",)))

    made = document([reply(BILL)], *ADDRESS, executor=Telling())
    execution = made.execution
    assert (execution.row_count, execution.columns, execution.truncated) == (2, ["state"], False)
    assert execution.tables == ["catalog_sales", "customer_address"] and execution.sql == BILL
    text = made.model_dump_json()
    assert "ZZ-SENTINEL-ROW" not in text and "QQ-OTHER-ROW" not in text
    assert "rows" not in type(execution).model_fields


def test_an_execution_failure_is_recorded_with_its_error() -> None:
    made = document([reply(BILL)], *ADDRESS, executor=Runs(failure="database_error"))
    assert (made.outcome, made.code) == ("execution_failed", "execution_failure")
    assert (made.execution.failure, made.execution.sqlstate, made.execution.error) == ("database_error", "22012", "division by zero")


# ---- retrieval -------------------------------------------------------------


def test_retrieval_is_recorded_with_scores_and_the_best_candidates_only() -> None:
    from app.core.locate import locate
    from app.core.retriever import RawScore, Settings
    from tracing import GRAPH, assemble, orchestrator

    scores = tuple(
        RawScore(column.table, column.name, 0.9 if column.table in ADDRESS else 0.1, 0.0) for column in SNAPSHOT.columns
    ) + tuple(RawScore(table.name, None, 0.8 if table.name in ADDRESS else 0.1, 0.0) for table in SNAPSHOT.tables)
    where = locate("billed where?", scores, (), GRAPH, Settings(0.5, 0.5, 5, 0.05))
    made = assemble(orchestrator([reply(BILL)], where).answer("billed where?"), CONTEXT, QUERY_ID, USER_ID, WHEN)

    retrieval = made.retrieval
    assert set(retrieval.anchors) == set(ADDRESS) and len(retrieval.tables) == 24
    assert len(retrieval.candidates) == CANDIDATES_KEPT and retrieval.candidates_total == len(scores)
    assert [candidate.rank for candidate in retrieval.candidates] == list(range(1, CANDIDATES_KEPT + 1))
    best = retrieval.candidates[0]
    assert best.kind == "column" and best.table in ADDRESS and 0 <= best.semantic_score <= 1
    assert (retrieval.anchor_bound.cut, retrieval.anchor_bound.cap) == (0.5, 5)
    assert (made.settings.alpha, made.settings.anchor_cut, made.settings.anchor_cap, made.settings.margin) == (0.5, 0.5, 5, 0.05)


# ---- no secret is in a trace (ruling 5) ------------------------------------


def test_the_document_has_no_field_that_could_hold_a_credential() -> None:
    """Checked by name over every field of every section, so that a field
    added later is looked at too."""
    seen: set[str] = set()

    def walk(model) -> None:
        for name, field in model.model_fields.items():
            seen.add(name)
            for inner in _models(field.annotation):
                walk(inner)

    def _models(annotation):
        from typing import get_args

        from pydantic import BaseModel

        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            yield annotation
        for argument in get_args(annotation):
            yield from _models(argument)

    walk(TraceDocument)
    assert len(seen) > 80
    for name in seen:
        assert not any(word in name for word in ("key_value", "password", "secret", "token_value", "dsn", "url", "authorization", "api_key")), name


def test_a_trace_assembled_beside_real_looking_credentials_holds_none_of_them() -> None:
    """The context a pipeline is opened with has the key and both database
    URLs within reach. None of it is an argument of `assemble`, and none
    of it is in what comes out."""
    import inspect

    from app.core import trace_document

    arguments = set(inspect.signature(trace_document.assemble).parameters)
    assert arguments == {"trace", "context", "query_id", "user_id", "created_at"}
    assert "settings" not in {field for field in trace_document.Context.__dataclass_fields__}
    text = document([reply(BILL)], *ADDRESS).model_dump_json().lower()
    for word in ("sk-", "bearer ", "postgresql://", "password", "authorization"):
        assert word not in text, word
