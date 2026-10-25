"""What `ask` and the smoke check print of a trace, at trace version 3:
how the tables were found and each choice put to the model, and a
question that was never located (step 10b, adoption step 3)."""

import json

from tracing import linked_trace, reply

from app.answer_report import model_calls, print_trace, summary

TABLES = json.dumps({"tables": ["catalog_sales", "customer_address"]})
SQL = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"
LEFT_OPEN = json.dumps({"choices": {"1": None}})


def test_a_chosen_answer_is_printed_with_its_linking_and_its_choice(capsys) -> None:
    trace = linked_trace([TABLES], [reply(SQL)], "Where are our catalog customers located?", [LEFT_OPEN])
    print_trace(trace)
    printed = capsys.readouterr().out
    assert "linked     catalog_sales, customer_address" in printed
    assert "link 1     accepted; " in printed
    assert "choice 1   customer_address: left_open, named none" in printed
    assert "choose 1   accepted; " in printed
    assert "over 3 model calls" in printed
    found = summary(trace)
    assert found["linked"] == ["catalog_sales", "customer_address"] and found["model_calls"] == 3 == model_calls(trace)
    assert found["choices"] == [{"anchor": "customer_address", "route": None, "outcome": "left_open"}]


def test_the_decline_at_linking_is_printed_though_nothing_was_located(capsys) -> None:
    trace = linked_trace([json.dumps({"tables": []})], [], choose_script=[])
    assert trace.located is None
    print_trace(trace)
    printed = capsys.readouterr().out
    assert "linked     none" in printed and "over 1 model calls" in printed
    found = summary(trace)
    assert (found["anchors"], found["warnings"], found["linked"], found["model_calls"]) == ([], [], [], 1)
