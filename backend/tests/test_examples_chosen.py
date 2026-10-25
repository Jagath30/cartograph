"""The demonstration's examples in chosen mode, pinned from the replies the
live run gave once at stop F3 (25 October 2026; the plan approved at stop
F1). Linking's and choosing's replies and the SQL are those of the stored
queries named below; the tree is the real JoinTree's. Free to rerun: no
model, no warehouse.

The gender question is pinned too, as the reason it left the examples:
the model named the billed customer's demographics, so it no longer
shows a choice made with no basis. By ruling 4 at stop F1 it was replaced
by the original set's Q3, and not asked again.
"""

import pytest
from tracing import linked_document, reply

# question: (stored query, linking's reply, choosing's reply or None, SQL or None)
LIVE = {
    "Where are our catalog customers located?": (
        "15665d35-8096-464a-8d1b-53e6ec0ec551",
        '{"tables":["catalog_sales","customer_address"]}',
        '{"choices":{"1":null}}',
        "SELECT DISTINCT customer_address.ca_city, customer_address.ca_state, customer_address.ca_country\n"
        "FROM catalog_sales\nJOIN customer_address ON catalog_sales.cs_bill_addr_sk = customer_address.ca_address_sk",
    ),
    "Which five states spent the most on the web?": (
        "c09411ce-2e3e-4d96-ab3b-cc35c78baf72",
        '{"tables":["web_sales","customer_address"]}',
        '{"choices":{"1":null}}',
        "SELECT customer_address.ca_state, SUM(web_sales.ws_net_paid) AS total_spent FROM web_sales JOIN "
        "customer_address ON web_sales.ws_bill_addr_sk = customer_address.ca_address_sk GROUP BY "
        "customer_address.ca_state ORDER BY total_spent DESC NULLS LAST LIMIT 5",
    ),
    "How many customers were born in each country?": (
        "93794b53-ea64-468c-9608-2d5a090de33a",
        '{"tables":["customer"]}',
        None,
        "SELECT customer.c_birth_country, COUNT(*) AS customer_count\nFROM customer\nGROUP BY customer.c_birth_country",
    ),
    "Which of our suppliers deliver late most often?": ("a7c2686b-387f-47a6-96b3-ee0da42dcc7d", '{"tables": []}', None, None),
    "How much did each gender spend on the web in the year 2002?": (
        "90d690c1-085e-4eb3-8763-9607daae23ea",
        '{"tables":["web_sales","customer_demographics","date_dim"]}',
        '{"choices":{"1":"A"}}',
        "SELECT cd.cd_gender, SUM(ws.ws_net_paid) AS total_spent\nFROM web_sales AS ws\n"
        "JOIN customer_demographics AS cd ON ws.ws_bill_cdemo_sk = cd.cd_demo_sk\n"
        "JOIN date_dim AS dd ON ws.ws_sold_date_sk = dd.d_date_sk\nWHERE dd.d_year = 2002\nGROUP BY cd.cd_gender",
    ),
}


def asked(question: str):
    _, link, choose, sql = LIVE[question]
    return linked_document([link], [reply(sql)] if sql else [], question=question,
                           choose_script=[choose] if choose else [])  # fmt: skip


@pytest.mark.parametrize(
    "question, letters",
    [
        ("Where are our catalog customers located?", {"A": "cs_bill_addr_sk", "B": "cs_ship_addr_sk"}),
        ("Which five states spent the most on the web?", {"A": "ws_ship_addr_sk", "B": "ws_bill_addr_sk"}),
    ],
)
def test_a_choice_the_model_left_open_shows_a_choice_made_with_no_basis(question, letters) -> None:
    made = asked(question)
    assert made.outcome == "answered"
    (choice,) = made.choosing.choices
    # The letters are as the live call was shown them.
    assert {a.letter: a.route_id.split("=")[0].split(".")[1] for a in choice.alternatives} == letters
    assert (choice.named, choice.outcome) == (None, "left_open")
    assert made.paths.attachments[1].decided_by == "alphabet"
    assert [(w.code, w.state) for w in made.paths.warnings if w.loud] == [("arbitrary_choice", "followed")]
    assert made.had_ambiguity and "it is arbitrary." in made.narrative.route


def test_one_table_is_a_quiet_answer() -> None:
    made = asked("How many customers were born in each country?")
    assert made.outcome == "answered" and made.linking.tables == ["customer"]
    assert made.choosing is None or made.choosing.attempts == []
    assert made.paths.warnings == [] and not made.had_ambiguity


def test_the_suppliers_question_is_declined_at_linking() -> None:
    made = asked("Which of our suppliers deliver late most often?")
    assert made.outcome == "not_answerable" and made.linking.tables == [] and made.paths is None
    assert made.narrative.found == "A language model, shown all 24 tables, named none that holds the answer."


def test_the_gender_question_no_longer_shows_a_choice_with_no_basis() -> None:
    made = asked("How much did each gender spend on the web in the year 2002?")
    (choice,) = made.choosing.choices
    assert choice.alternatives[0].route_id.startswith("web_sales.ws_bill_cdemo_sk")
    assert (choice.named, choice.outcome) == ("A", "applied")
    decided = {a.anchor: a.decided_by for a in made.paths.attachments}
    assert decided["customer_demographics"] == "model_reading"
    assert [w for w in made.paths.warnings if w.loud] == [] and not made.had_ambiguity

