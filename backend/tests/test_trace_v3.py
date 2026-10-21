"""Trace version 3 (step 10b, v2.1 adopted; the owner's rulings at stop
F1): what the stored document gains, and versions 1 and 2 still read and
served as they were.

  linking    the system message whole; the user message by its sha256,
             with the hash of the snapshot it was built from, since it is
             the full-schema prompt and can be rebuilt; the tables named;
             every attempt.
  choosing   the prompt whole; each choice put to the model, its
             alternatives as listed, the alphabet's pick, what was named
             and how the rebuilt tree took it; what arose after; every
             attempt; the fault.
  decided_by on each attachment: who decided the choice. A choice the
             model made is never a rule's.

The answers in tests/stored_traces were made offline for the original
set's Q3 by each version's own code (make_document.py): version 1 by
checkpoint-08-trace's, version 2 by the build before version 3. Each
`*_served.json` is how the build before version 3 served it.
"""

import hashlib
import json
from pathlib import Path

import pytest
from tracing import CONTEXT, GRAPH, SNAPSHOT, document, linked_document, reply

from app.core.choosing import CHOOSE_RULES, choices_of, label
from app.core.linking import LINK_RULES, build_link_prompt
from app.core.trace_document import READABLE_VERSIONS, TRACE_VERSION, TraceDocument

STORED = Path(__file__).resolve().parent / "stored_traces"
QUESTION = "Where are our catalog customers located?"
TIED = ("catalog_sales", "customer_address")
SQL = ("SELECT ca.ca_state, COUNT(*) FROM catalog_sales cs JOIN customer_address ca "
       "ON cs.cs_ship_addr_sk = ca.ca_address_sk GROUP BY ca.ca_state")  # fmt: skip


def linked(*tables: str) -> str:
    return json.dumps({"tables": list(tables)})


def chose(**answers) -> str:
    return json.dumps({"choices": answers})


def _ship_letter() -> str:
    from tracing import located

    (choice,) = choices_of(located(*TIED).tree, QUESTION)
    return label(next(i for i, p in enumerate(choice.alternatives) if "cs_ship_addr_sk" in p.id))


# --------------------------------------------------------------------------
# Versions 1 and 2, as they were
# --------------------------------------------------------------------------


def test_this_build_writes_version_3_and_reads_1_2_and_3() -> None:
    assert TRACE_VERSION == 3 and READABLE_VERSIONS == (1, 2, 3)


@pytest.mark.parametrize(
    "stored, served", [("q3_v1_stored.json", "q3_v1_served.json"), ("q3_v2_stored.json", "q3_v2_stored.json")]
)
def test_a_stored_version_1_or_2_answer_is_served_byte_for_byte_as_before(stored, served) -> None:
    document_ = TraceDocument.model_validate_json((STORED / stored).read_text())
    assert document_.trace_version in (1, 2)
    assert document_.model_dump_json() == (STORED / served).read_text()


def test_the_pinned_answers_are_what_their_names_say() -> None:
    v1 = json.loads((STORED / "q3_v1_stored.json").read_text())
    v2 = json.loads((STORED / "q3_v2_stored.json").read_text())
    assert (v1["trace_version"], v2["trace_version"]) == (1, 2)
    assert v1["question"] == v2["question"] == QUESTION
    assert all("plain" not in w for w in v1["paths"]["warnings"])
    assert [w["code"] for w in v2["paths"]["warnings"]] == ["arbitrary_choice"] and v2["paths"]["warnings"][0]["plain"]


@pytest.mark.parametrize("stored", ["q3_v1_stored.json", "q3_v2_stored.json"])
def test_a_stored_version_1_or_2_answer_has_nothing_of_version_3(stored) -> None:
    served = json.loads(TraceDocument.model_validate_json((STORED / stored).read_text()).model_dump_json())
    assert "linking" not in served and "choosing" not in served
    assert all("decided_by" not in a for a in served["paths"]["attachments"])


def test_a_stored_version_1_and_2_answer_go_through_the_store_and_back_unchanged(scratch_database) -> None:
    from app.shell.trace_store import TraceStore

    import uuid

    store = TraceStore(scratch_database)
    for stored, served in (("q3_v1_stored.json", "q3_v1_served.json"), ("q3_v2_stored.json", "q3_v2_stored.json")):
        # Both answers were made under one query id; each is kept under its own.
        query_id = uuid.uuid4()
        written = TraceDocument.model_validate_json((STORED / stored).read_text())
        written = written.model_copy(update={"user_id": store.local_user_id(), "query_id": query_id})
        store.save(written)
        read = store.load(query_id)
        expected = json.loads((STORED / served).read_text())
        expected.update(user_id=store.local_user_id(), query_id=str(query_id))
        assert json.loads(read.model_dump_json()) == expected


# --------------------------------------------------------------------------
# Linking
# --------------------------------------------------------------------------


def _chosen(choose_script=None, link_script=None, generation_script=None):
    return linked_document(
        link_script or [linked(*TIED)],
        generation_script or [reply(SQL)],
        question=QUESTION,
        choose_script=[chose(**{"1": _ship_letter()})] if choose_script is None else choose_script,
    )


def test_a_version_3_answer_says_so() -> None:
    assert _chosen().trace_version == 3


def test_linking_keeps_its_system_message_whole_and_its_user_message_by_hash() -> None:
    made = _chosen()
    linking = made.linking
    assert linking.prompt_system == LINK_RULES
    user = build_link_prompt(QUESTION, SNAPSHOT, GRAPH).user
    assert linking.prompt_user_sha256 == hashlib.sha256(user.encode()).hexdigest()
    assert linking.snapshot_hash == made.schema_ref.hash
    assert "prompt_user" not in json.loads(made.model_dump_json())["linking"]
    assert linking.tables == list(TIED)
    (attempt,) = linking.attempts
    assert (attempt.number, attempt.outcome, attempt.tables, attempt.fault) == (1, "accepted", list(TIED), None)
    assert attempt.reply == linked(*TIED) and attempt.messages_added == []


def test_the_linking_user_message_is_rebuilt_from_the_snapshot_and_the_question() -> None:
    """The ruling at stop F1: stored by its sha256, so it must be possible to
    get it back from the snapshot it was built from, and the question."""
    made = _chosen()
    rebuilt = build_link_prompt(made.question, CONTEXT.snapshot, CONTEXT.graph).user
    assert hashlib.sha256(rebuilt.encode()).hexdigest() == made.linking.prompt_user_sha256


def test_against_the_stored_snapshot_the_hash_and_the_user_message_both_match(app_database_url) -> None:
    """Live: the snapshot the store holds as current, rebuilt from its rows,
    has the hash a linked answer records, and rebuilds the same user
    message."""
    import os

    if not os.environ.get("WAREHOUSE_DATABASE_URL"):
        pytest.skip("needs the warehouse")
    from app.config import get_settings
    from app.core.graph_builder import build_graph
    from app.shell.schema_ingestor import SchemaIngestor
    from app.shell.snapshot_store import SnapshotStore, snapshot_hash

    settings = get_settings()
    ingested = SchemaIngestor(settings.warehouse_database_url, settings.warehouse_overlay_path).ingest()
    stored, rebuilt = SnapshotStore(app_database_url).load_current(ingested.preferences, ingested.attach_preferences)
    assert snapshot_hash(ingested) == stored.hash
    asked = build_link_prompt(QUESTION, ingested, build_graph(ingested)).user
    again = build_link_prompt(QUESTION, rebuilt, build_graph(rebuilt)).user
    assert hashlib.sha256(asked.encode()).hexdigest() == hashlib.sha256(again.encode()).hexdigest()


def test_a_linking_retry_keeps_what_was_added_and_its_fault() -> None:
    made = _chosen(link_script=["{}", linked(*TIED)])
    first, second = made.linking.attempts
    assert (first.outcome, first.fault) == ("malformed", "the reply does not have exactly the field tables")
    assert [m.role for m in second.messages_added] == ["assistant", "user"]
    assert second.messages_added[0].content == "{}"


# --------------------------------------------------------------------------
# Choosing
# --------------------------------------------------------------------------


def test_choosing_keeps_its_prompt_whole_and_each_choice_as_it_was_put() -> None:
    from tracing import located

    made = _chosen()
    choosing = made.choosing
    (choice,) = choices_of(located(*TIED).tree, QUESTION)
    assert choosing.prompt_system == CHOOSE_RULES
    assert choosing.prompt_user.startswith(f"Question: {QUESTION}\n\nChoice 1: customer_address can be joined")
    from app.core.choosing import build_choose_prompt

    assert choosing.prompt_user == build_choose_prompt(QUESTION, (choice,), GRAPH).user
    (asked,) = choosing.choices
    assert (asked.number, asked.anchor, asked.attached_to) == (1, "customer_address", "catalog_sales")
    assert [a.letter for a in asked.alternatives] == ["A", "B"]
    assert [a.route_id for a in asked.alternatives] == [p.id for p in choice.alternatives]
    assert all(a.description for a in asked.alternatives)
    assert asked.alphabet == choice.alphabet and "cs_bill_addr_sk" in asked.alphabet
    assert asked.named == _ship_letter() and "cs_ship_addr_sk" in asked.named_route
    assert asked.outcome == "applied"
    assert choosing.arose == [] and choosing.fault is None
    (attempt,) = choosing.attempts
    assert (attempt.outcome, attempt.named, attempt.fault) == ("accepted", [_ship_letter()], None)


def test_a_choice_left_open_is_kept_as_such() -> None:
    made = _chosen(choose_script=[chose(**{"1": None})])
    (asked,) = made.choosing.choices
    assert (asked.named, asked.named_route, asked.outcome) == (None, None, "left_open")


def test_a_failed_choosing_call_keeps_its_fault_and_every_choice_open() -> None:
    from app.shell.model_client import ModelReply

    failed = ModelReply("m", "m", None, None, 0, 0, 0, 0.0, 5, "cut_off", "a fixed sentence")
    made = _chosen(choose_script=[failed])
    assert made.choosing.fault == "cut_off"
    assert [c.outcome for c in made.choosing.choices] == ["left_open"]
    assert made.choosing.attempts[0].outcome == "cut_off"


def test_with_no_choice_to_ask_choosing_is_kept_empty_with_no_prompt() -> None:
    made = linked_document([linked("store_sales", "store")], [reply("SELECT 1 FROM store")], question=QUESTION,
                           choose_script=[])  # fmt: skip
    assert made.choosing.choices == [] and made.choosing.attempts == [] and made.choosing.prompt_user is None


def test_linked_without_a_chooser_keeps_no_choosing() -> None:
    made = linked_document([linked(*TIED)], [reply(SQL)], question=QUESTION)
    assert made.linking is not None and made.choosing is None
    assert "choosing" not in json.loads(made.model_dump_json())


# --------------------------------------------------------------------------
# Who decided each choice
# --------------------------------------------------------------------------


def test_each_attachment_says_who_decided_it() -> None:
    made = _chosen()
    seed, joined = made.paths.attachments
    assert seed.decided_by is None and "decided_by" not in json.loads(made.model_dump_json())["paths"]["attachments"][0]
    assert (joined.rule, joined.decided_by) == ("model_choice", "model_reading")
    open_ = _chosen(choose_script=[chose(**{"1": None})])
    assert (open_.paths.attachments[1].rule, open_.paths.attachments[1].decided_by) == ("alphabetical", "alphabet")


@pytest.mark.parametrize(
    "rule, decided_by",
    [("only_path", "only_path"), ("shortest", "shortest"), ("question_evidence", "wording"),
     ("preference", "declared_default"), ("model_choice", "model_reading"), ("alphabetical", "alphabet")],
)  # fmt: skip
def test_every_rule_has_its_own_name_for_who_decided(rule, decided_by) -> None:
    from app.core.trace_document import DECIDED_BY

    assert DECIDED_BY[rule] == decided_by
    assert len(set(DECIDED_BY.values())) == len(DECIDED_BY)


def test_a_version_3_answer_without_linking_still_says_who_decided() -> None:
    made = document([reply(SQL)], *TIED, question=QUESTION)
    assert made.trace_version == 3 and made.linking is None
    assert made.paths.attachments[1].decided_by == "alphabet"


def test_the_whole_question_is_counted_in_tokens_and_cost() -> None:
    from app.shell.model_client import ModelReply

    def paid(text, tokens_in, tokens_out, cost):
        return ModelReply("m", "m", None, text, tokens_in, 0, tokens_out, cost, 5)

    made = _chosen(
        link_script=[paid(linked(*TIED), 7900, 20, 0.0008)],
        choose_script=[paid(chose(**{"1": None}), 300, 10, 0.00004)],
        generation_script=[paid(reply(SQL), 2000, 80, 0.0002)],
    )
    assert (made.tokens_in, made.tokens_out) == (10200, 110)
    assert made.cost_usd == pytest.approx(0.00104)
    assert made.linking.attempts[0].tokens_in == 7900 and made.choosing.attempts[0].cost_usd == pytest.approx(0.00004)


def test_the_snapshot_hash_is_kept_whole() -> None:
    full = "f" * 64
    made = linked_document([linked(*TIED)], [reply(SQL)], question=QUESTION, schema_ref=(7, full))
    assert made.linking.snapshot_hash == full == made.schema_ref.hash
