"""The demonstration's gender question against the live warehouse (step 9,
the owner's ruling at stop 3).

"How much did each gender spend on the web in the year 2002?" The plan is
the real JoinTree's for the anchors retrieval chose; the model is scripted
with the SQL Cartograph itself wrote on 14 October (trace 44205844); the
executor is real, on the SELECT-only role. The figures are pinned: if the
warehouse, the plan or the executor moves, this says so.

By the billed customer's demographics women spent more; by the
shipped-to customer's, men. That is the choice the amber block reports.

Needs a loaded warehouse at scale factor 1, and skips cleanly without one.
"""

from decimal import Decimal

from tests.test_query_executor import warehouse_dsn  # noqa: F401  (a fixture)
from tracing import CONTEXT, QUERY_ID, USER_ID, WHEN, reply, trace_of

from app.core.trace_document import assemble
from app.shell.query_executor import QueryExecutor

GENDER = ("web_sales", "web_returns", "customer_demographics", "web_page", "date_dim")
# Exactly the SQL Cartograph wrote on 14 October 2026 (trace 44205844). It
# has no ORDER BY, so the rows are sorted here before they are compared.
BILLED = """SELECT cd.cd_gender, SUM(ws.ws_ext_sales_price) AS total_spent
FROM web_sales AS ws
JOIN date_dim AS dd ON ws.ws_sold_date_sk = dd.d_date_sk
JOIN customer_demographics AS cd ON ws.ws_bill_cdemo_sk = cd.cd_demo_sk
WHERE dd.d_year = 2002
GROUP BY cd.cd_gender"""
# Not written by Cartograph: the same SQL with the shipped-to customer's
# demographics in place of the billed customer's, the one change.
SHIPPED_TO = BILLED.replace("ws_bill_cdemo_sk", "ws_ship_cdemo_sk")


def asked(sql: str, dsn: str):
    trace = trace_of([reply(sql)], *GENDER, executor=QueryExecutor(dsn), question="How much did each gender spend on the web in the year 2002?")
    return trace, assemble(trace, CONTEXT, QUERY_ID, USER_ID, WHEN)


def test_the_billed_route_gives_the_pinned_totals_and_the_choice_is_loud(warehouse_dsn) -> None:  # noqa: F811
    trace, made = asked(BILLED, warehouse_dsn)
    assert made.outcome == "answered" and made.validation.conformance in ("conforms", "incomplete")
    assert sorted(trace.execution.rows) == [("F", Decimal("183224441.98")), ("M", Decimal("178618746.26"))]
    loud = [w for w in made.paths.warnings if w.loud]
    assert [(w.code, w.state) for w in loud] == [("arbitrary_choice", "followed")]
    assert loud[0].plain.startswith("This answer uses the billed customer's demographics.")


def test_the_shipped_to_route_turns_the_answer_round(warehouse_dsn) -> None:  # noqa: F811
    trace, made = asked(SHIPPED_TO, warehouse_dsn)
    assert sorted(trace.execution.rows) == [("F", Decimal("178673143.87")), ("M", Decimal("183166052.93"))]
    loud = [w for w in made.paths.warnings if w.loud]
    assert [(w.code, w.state) for w in loud] == [("arbitrary_choice", "other_route_taken")]
    assert loud[0].plain.startswith("This answer uses the shipped-to customer's demographics, not ")
