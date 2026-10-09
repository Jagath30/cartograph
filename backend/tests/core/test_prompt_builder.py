"""PromptBuilder (FR-15, FR-17, FR-42, DD-14 as amended).

From the hand-written fixture, so every line of the prompt asserted here
can be checked against tests/core/conftest.py by eye.
"""

import dataclasses
import json

import pytest

from app.core.path_finder import Join
from app.core.prompt_builder import (
    GENERAL_RULES,
    JOIN_RULES,
    REPLY_SCHEMA,
    build_prompt,
    parse_reply,
    render_joins,
    render_tables,
    retry_message,
)
from app.core.snapshot import Column, Table

SALE_TO_STORE = Join("store_sales", ("ss_store_sk",), "store", ("s_store_sk",), "catalog", "fk", "many_to_one")
SALE_TO_ADDRESS = Join("store_sales", ("ss_addr_sk",), "customer_address", ("ca_address_sk",), "catalog", "fk", "many_to_one")
TWO_COLUMNS = Join(
    "store_returns", ("sr_item_sk", "sr_ticket_number"), "store_sales", ("ss_item_sk", "ss_ticket_number"),
    "overlay", None, "many_to_one",
)  # fmt: skip
QUESTION = "How much did each store sell?"


@pytest.fixture
def named(small_snapshot):
    """The fixture with readable names on one table, as the overlay gives them."""
    tables = tuple(
        dataclasses.replace(table, readable="store sales") if table.name == "store_sales" else table
        for table in small_snapshot.tables
    )
    columns = tuple(
        dataclasses.replace(column, readable="store sales — extended sales price")
        if column.name == "ss_ext_sales_price"
        else column
        for column in small_snapshot.columns
    )
    return dataclasses.replace(small_snapshot, tables=tables, columns=columns)


# --------------------------------------------------------------------------
# The tables
# --------------------------------------------------------------------------


def test_a_table_is_rendered_whole_with_its_keys_and_its_readable_names(named) -> None:
    assert render_tables(named, ("store_sales",)) == (
        "-- store sales\n"
        "CREATE TABLE store_sales (\n"
        "  ss_ticket_number bigint,  -- ss_ticket_number\n"
        "  ss_store_sk bigint,  -- ss_store_sk\n"
        "  ss_addr_sk bigint,  -- ss_addr_sk\n"
        "  ss_customer_sk bigint,  -- ss_customer_sk\n"
        "  ss_ext_sales_price numeric(7,2)  -- extended sales price\n"
        ");"
    )


def test_a_primary_key_is_declared_and_no_foreign_key_ever_is(named) -> None:
    text = render_tables(named, ("store", "store_sales", "customer_address"))
    assert "  s_state character varying,  -- s_state\n  PRIMARY KEY (s_store_sk)\n);" in text
    assert "REFERENCES" not in text.upper() and "FOREIGN" not in text.upper()


def test_tables_come_in_the_order_given_and_only_those_given(named) -> None:
    text = render_tables(named, ("store", "store_sales"))
    assert text.index("CREATE TABLE store (") < text.index("CREATE TABLE store_sales (")
    assert text.count("CREATE TABLE") == 2 and "CREATE TABLE customer" not in text


def test_every_column_of_a_table_is_there(named) -> None:
    text = render_tables(named, tuple(table.name for table in named.tables))
    for column in named.columns:
        assert f"  {column.name} {column.data_type}" in text


def test_a_table_that_is_not_in_the_snapshot_raises(named) -> None:
    with pytest.raises(KeyError):
        render_tables(named, ("no_such_table",))


# --------------------------------------------------------------------------
# The joins
# --------------------------------------------------------------------------


def test_each_join_is_one_line_and_a_two_column_key_is_on_one_line_with_and() -> None:
    assert render_joins((SALE_TO_STORE, TWO_COLUMNS)) == (
        JOIN_RULES + "\n"
        "  store_returns.sr_item_sk = store_sales.ss_item_sk AND store_returns.sr_ticket_number = store_sales.ss_ticket_number\n"
        "  store_sales.ss_store_sk = store.s_store_sk"
    )


def test_the_join_lines_do_not_depend_on_the_order_the_tree_listed_them() -> None:
    assert render_joins((SALE_TO_STORE, SALE_TO_ADDRESS)) == render_joins((SALE_TO_ADDRESS, SALE_TO_STORE))


# --------------------------------------------------------------------------
# The whole prompt
# --------------------------------------------------------------------------


def test_the_prompt_is_the_rules_then_tables_then_joins_then_the_question(named) -> None:
    prompt = build_prompt(f"  {QUESTION}\n", named, ("store_sales", "store"), (SALE_TO_STORE,))

    assert prompt.system == GENERAL_RULES
    tables, joins, question = prompt.user.split("\n\n" + JOIN_RULES)[0], JOIN_RULES, f"Question: {QUESTION}"
    assert prompt.user == (
        "Tables:\n\n" + render_tables(named, ("store_sales", "store")) + "\n\n"
        + render_joins((SALE_TO_STORE,)) + "\n\n" + question
    )  # fmt: skip
    assert tables.startswith("Tables:") and joins in prompt.user and prompt.user.endswith(question)
    assert prompt.tables == ("store_sales", "store") and prompt.has_join_section
    assert prompt.text == GENERAL_RULES + "\n\n" + prompt.user


def test_the_general_rules_say_nothing_about_joins_or_a_path() -> None:
    """They are used unchanged in both evaluation modes at step 10, and one
    of those has no selected path."""
    lowered = GENERAL_RULES.lower()
    for word in ("join", "path", "condition", "selected"):
        assert word not in lowered
    for needed in ("exactly one select", "qualify every column", "json", "not_answerable", "do not explain"):
        assert needed in lowered


def test_with_no_selected_path_the_prompt_has_no_join_section_and_the_same_rules(named) -> None:
    for joins in (None, ()):
        prompt = build_prompt(QUESTION, named, ("store_sales", "store"), joins)
        assert prompt.system == GENERAL_RULES and not prompt.has_join_section
        assert "join" not in prompt.user.lower()
        assert prompt.user == "Tables:\n\n" + render_tables(named, ("store_sales", "store")) + f"\n\nQuestion: {QUESTION}"


def test_everything_about_joins_is_in_the_join_section() -> None:
    assert "exactly these conditions" in JOIN_RULES and "need not appear" in JOIN_RULES


def test_the_question_is_sent_as_asked(named) -> None:
    odd = 'What about "quoted" things; DROP TABLE store -- and {braces}?'
    assert build_prompt(odd, named, ("store",), None).user.endswith(f"Question: {odd}")


def test_the_same_inputs_give_the_same_text(named) -> None:
    a = build_prompt(QUESTION, named, ("store_sales", "store"), (SALE_TO_STORE,))
    b = build_prompt(QUESTION, named, ("store_sales", "store"), (SALE_TO_STORE,))
    assert a == b and a.text == b.text


def test_a_column_with_no_readable_name_is_commented_with_its_own() -> None:
    from app.core.snapshot import SchemaSnapshot

    snapshot = SchemaSnapshot((Table("t"),), (Column("t", "a", "integer"),), (), ())
    assert render_tables(snapshot, ("t",)) == "-- t\nCREATE TABLE t (\n  a integer  -- a\n);"


# --------------------------------------------------------------------------
# The reply
# --------------------------------------------------------------------------


def test_the_reply_shape_has_two_fields_and_no_room_for_a_reason() -> None:
    assert set(REPLY_SCHEMA["properties"]) == {"status", "sql"}
    assert REPLY_SCHEMA["required"] == ["status", "sql"] and REPLY_SCHEMA["additionalProperties"] is False
    assert REPLY_SCHEMA["properties"]["status"]["enum"] == ["sql", "not_answerable"]


def test_a_reply_with_sql_is_read_and_its_sql_is_not_touched() -> None:
    sql = "select  s_store_name\n  FROM store ;  "
    reply = parse_reply(json.dumps({"status": "sql", "sql": sql}))
    assert reply.kind == "sql" and reply.sql == sql and reply.fault is None


@pytest.mark.parametrize("sql", ["", "   ", "\n"])
def test_a_reply_that_says_not_answerable_is_read(sql) -> None:
    reply = parse_reply(json.dumps({"status": "not_answerable", "sql": sql}))
    assert reply.kind == "not_answerable" and reply.sql is None


@pytest.mark.parametrize(
    ("text", "fault"),
    [
        ('{"status": "not_answerable", "sql": "SELECT 1"}', "status is not_answerable and SQL was given all the same"),
        ('{"status": "sql", "sql": ""}', "status is sql and there is no SQL"),
        ('{"status": "sql", "sql": "   "}', "status is sql and there is no SQL"),
        ('{"status": "maybe", "sql": "SELECT 1"}', "status or sql is not of the form asked for"),
        ('{"status": "sql", "sql": null}', "status or sql is not of the form asked for"),
        ('{"status": "sql", "sql": ["SELECT 1"]}', "status or sql is not of the form asked for"),
        ('{"status": "sql"}', "the reply does not have exactly the fields status and sql"),
        ('{"sql": "SELECT 1"}', "the reply does not have exactly the fields status and sql"),
        ('{"status": "sql", "sql": "SELECT 1", "why": "because"}', "the reply does not have exactly the fields status and sql"),
        ('["sql", "SELECT 1"]', "the reply does not have exactly the fields status and sql"),
        ('"SELECT 1"', "the reply does not have exactly the fields status and sql"),
        ("SELECT 1", "the reply is not JSON"),
        ('```json\n{"status": "sql", "sql": "SELECT 1"}\n```', "the reply is not JSON"),
        ('Here you go: {"status": "sql", "sql": "SELECT 1"}', "the reply is not JSON"),
        ('{"status": "sql", "sql": "SELECT', "the reply is not JSON"),
        ("", "the reply is not JSON"),
    ],
)
def test_a_reply_that_contradicts_itself_or_is_the_wrong_shape_is_malformed(text, fault) -> None:
    reply = parse_reply(text)
    assert reply.kind == "malformed" and reply.sql is None and reply.fault == fault


def test_the_retry_message_carries_the_feedback_and_asks_for_the_same_form() -> None:
    message = retry_message("The SQL was not accepted:\n- There is no table named shops.")
    assert message.startswith("The SQL was not accepted:\n- There is no table named shops.")
    assert message.endswith("Reply again in the same JSON form, with a corrected query.")
