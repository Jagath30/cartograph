"""The narrative at trace_version 3 (step 10b, v2.1 adopted; the wording
the owner approved at stop F1). Told from the linking and the choosing as
well as the plan:

  found      what a language model, shown every table of the snapshot,
             named for the question; what was added to link them.
  route      a choice the model made says so, and is never a rule's or a
             declared default's. A choice left open stays arbitrary.
  not_taken  what the model's reading set aside, apart from what a
             declared preference did.
  the decline at linking, and a linking call that failed.

Traces are made offline (tracing.linked_document) with scripted replies.
Every question here is the original set's Q3 or a made-up one.
"""

import json

from tracing import SNAPSHOT, linked_document, located, reply

from app.core.choosing import choices_of, label
from app.orchestrator import NOT_ANSWERABLE
from app.shell.model_client import ModelReply

QUESTION = "Where are our catalog customers located?"
TIED = ("catalog_sales", "customer_address")
SHIP = ("SELECT ca.ca_state, COUNT(*) FROM catalog_sales cs JOIN customer_address ca "
        "ON cs.cs_ship_addr_sk = ca.ca_address_sk GROUP BY ca.ca_state")  # fmt: skip
BILL = SHIP.replace("cs_ship_addr_sk", "cs_bill_addr_sk")
COUNT = len(SNAPSHOT.tables)


def linked(*tables: str) -> str:
    return json.dumps({"tables": list(tables)})


def chose(**answers) -> str:
    return json.dumps({"choices": answers})


def _letter(column: str) -> str:
    (choice,) = choices_of(located(*TIED).tree, QUESTION)
    return label(next(i for i, p in enumerate(choice.alternatives) if column in p.id))


def chosen(letter, sql=SHIP):
    return linked_document([linked(*TIED)], [reply(sql)], question=QUESTION, choose_script=[chose(**{"1": letter})])


# --------------------------------------------------------------------------
# found
# --------------------------------------------------------------------------


def test_found_says_what_the_model_named_out_of_every_table() -> None:
    made = chosen(_letter("cs_ship_addr_sk"))
    assert made.narrative.found == (
        f"A language model, shown all {COUNT} tables, named 2 for your question: catalog sales and customer address."
    )
    assert COUNT == 24


def test_the_count_of_tables_comes_from_the_snapshot_not_a_fixed_number() -> None:
    from app.core.narrative import narrate

    made = chosen(_letter("cs_ship_addr_sk"))
    names = {"tables": {t: t.replace("_", " ") for t in ("catalog_sales", "customer_address", "item")}, "columns": {}}
    told = narrate(outcome=made.outcome, message=made.message, retrieval=made.retrieval, subgraph=made.subgraph,
                   paths=made.paths, generation=made.generation, validation=made.validation,
                   execution=made.execution, names=names, linking=made.linking)  # fmt: skip
    assert told.found.startswith("A language model, shown all 3 tables, named 2 for your question:")


def test_one_table_named() -> None:
    made = linked_document([linked("customer")], [reply("SELECT COUNT(*) FROM customer")], question="How many?",
                           choose_script=[])  # fmt: skip
    assert made.narrative.found == f"A language model, shown all {COUNT} tables, named one for your question: customer."


def test_a_bridge_the_model_did_not_name_is_said_to_have_been_added() -> None:
    made = linked_document([linked("store", "household_demographics")],
                           [reply("SELECT 1 FROM store s JOIN store_sales ss ON ss.ss_store_sk = s.s_store_sk "
                                  "JOIN household_demographics h ON ss.ss_hdemo_sk = h.hd_demo_sk")],
                           question="A question?")  # fmt: skip
    # The tree bridges them through store_returns (the alphabet's pick).
    assert made.paths.tables == ["store", "household_demographics", "store_returns"]
    assert "To link them, store returns was added; the model had not named it." in made.narrative.found
    assert "your question did not name" not in made.narrative.found


# --------------------------------------------------------------------------
# A choice the model made, and one it left open
# --------------------------------------------------------------------------


def test_a_choice_the_model_made_says_so_and_never_that_a_rule_did() -> None:
    made = chosen(_letter("cs_ship_addr_sk"))
    route = made.narrative.route
    assert "By the rules, " in route
    assert "equally possible; a language model, reading your question, chose this one." in route
    for never in ("arbitrary", "alphabetical", "declared defaults", "shortest route", "wording of your question"):
        assert never not in route


def test_what_the_model_set_aside_is_its_own_sentence_never_a_declared_preferences() -> None:
    """The fault fixed here (entered at b81385c, when model_choice was
    added): every rule but the alphabet's and the wording's fell into
    "Set aside by declared preference"."""
    made = chosen(_letter("cs_ship_addr_sk"))
    not_taken = made.narrative.not_taken
    assert "Set aside by the model's reading of your question: " in not_taken
    assert "declared preference" not in not_taken and "Not used" not in not_taken


def test_a_choice_left_open_stays_arbitrary_and_keeps_its_amber_warning() -> None:
    made = chosen(None, sql=BILL)
    assert made.paths.attachments[1].decided_by == "alphabet"
    assert "it is arbitrary." in made.narrative.route
    assert "Not used: " in made.narrative.not_taken
    (warning,) = made.paths.warnings
    assert warning.code == "arbitrary_choice" and warning.loud and warning.plain
    assert "model" not in made.narrative.route.split("named 2")[-1]
    assert made.had_ambiguity


def test_a_choice_the_model_made_raises_no_warning() -> None:
    made = chosen(_letter("cs_ship_addr_sk"))
    assert made.paths.warnings == [] and not made.had_ambiguity


# --------------------------------------------------------------------------
# The decline at linking, and a linking call that failed
# --------------------------------------------------------------------------


def test_the_decline_at_linking_is_told_in_every_part() -> None:
    made = linked_document([linked()], [], question="Which of our suppliers deliver late most often?", choose_script=[])
    assert (made.outcome, made.message) == ("not_answerable", NOT_ANSWERABLE["linking"])
    assert made.paths is None and made.retrieval is None and made.subgraph is None and made.generation is None
    assert made.linking.tables == [] and made.choosing is None and not made.had_ambiguity
    told = made.narrative
    assert told.found == f"A language model, shown all {COUNT} tables, named none that holds the answer."
    assert told.route == "No plan was made."
    assert told.not_taken == "No plan was made, so nothing was set aside."
    assert told.sql == "No query was written, and no model was asked to write one."
    assert told.result == (
        "No query was written. The model was shown every table of the warehouse, and named none that holds the answer."
    )


def test_a_linking_call_that_failed_is_told() -> None:
    failed = ModelReply("m", "m", None, None, 0, 0, 0, 0.0, 5, "cut_off", "the model's reply was cut off by the output limit")
    made = linked_document([failed], [], question="A question?", choose_script=[])
    assert made.outcome == "model_failed" and made.paths is None
    assert made.narrative.found == (
        f"A language model, shown all {COUNT} tables, was asked to name the ones your question needs, and gave no "
        "reply in the form asked for."
    )
    assert made.narrative.sql == "No query was written: the model's reply was cut off by the output limit."
    assert made.narrative.result == "No answer was given."


def test_named_tables_that_cannot_be_linked_are_told_so() -> None:
    # Within one join, store and household_demographics are not linked.
    made = linked_document([linked("store", "household_demographics")], [], question="A question?", max_joins=1)
    assert made.outcome == "not_answerable" and made.paths.unconnected
    assert made.narrative.found.startswith(f"A language model, shown all {COUNT} tables, named 2 for your question: ")
    assert "could not be linked to the rest within 1 step." in made.narrative.found
    assert made.narrative.result == (
        "No answer was given: the tables named for your question could not be linked to one another."
    )
