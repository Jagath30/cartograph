"""A sale over the page it was sold through (step 9, the owner's ruling
at stop 3; CHECKPOINTS.md, item 84).

Two declared preferences: attach to web_sales rather than web_page, and to
catalog_sales rather than catalog_page. Before them the alphabet hung the
date table on the PAGE ("web_page" sorts before "web_sales"), and asked
"How much did each gender spend on the web in the year 2002?" the plan
filtered on the date the page was last accessed.

Pure: the committed overlay, the real JoinTree on TPC-DS, no warehouse
and no model. Runs in CI.
"""

from dataclasses import replace

from tracing import GRAPH, SNAPSHOT, document, reply

from app.core.explainer import explain_tree
from app.core.graph_builder import build_graph
from app.core.join_tree import build_tree

# The anchors retrieval chose for that question on 14 October 2026, best
# first (trace 4f575978, and again in 44205844).
GENDER = ("web_sales", "web_returns", "customer_demographics", "web_page", "date_dim")
# The same with the customer, which the anchor cap left out that day.
GENDER_AND_CUSTOMER = (*GENDER, "customer")
# Exactly the SQL Cartograph wrote for that question on 14 October 2026
# (trace 44205844).
SALE_DATE_SQL = """SELECT cd.cd_gender, SUM(ws.ws_ext_sales_price) AS total_spent
FROM web_sales AS ws
JOIN date_dim AS dd ON ws.ws_sold_date_sk = dd.d_date_sk
JOIN customer_demographics AS cd ON ws.ws_bill_cdemo_sk = cd.cd_demo_sk
WHERE dd.d_year = 2002
GROUP BY cd.cd_gender"""
REASON = "a {} page is only what a sale was made through, so whatever either could carry is taken to describe the sale (an operator's default)"


def tree(anchors, graph=GRAPH):
    scores = {anchor: 1.0 - position / 10 for position, anchor in enumerate(anchors)}
    return build_tree(graph, anchors, scores)


def attachment(built, anchor):
    return next(a for a in built.attachments if a.anchor == anchor)


def without_these_two():
    """The graph as it was before the ruling."""
    kept = tuple(
        p for p in SNAPSHOT.attach_preferences if {p.attach_to, p.rather_than} not in ({"web_sales", "web_page"}, {"catalog_sales", "catalog_page"})
    )
    assert len(kept) == len(SNAPSHOT.attach_preferences) - 2
    return build_graph(replace(SNAPSHOT, attach_preferences=kept))


def test_both_are_declared_with_their_reason() -> None:
    declared = {(p.attach_to, p.rather_than): p.because for p in SNAPSHOT.attach_preferences}
    assert declared[("web_sales", "web_page")] == REASON.format("web")
    assert declared[("catalog_sales", "catalog_page")] == REASON.format("catalog")


def test_the_date_hangs_on_the_web_sale_by_its_date_of_sale() -> None:
    date = attachment(tree(GENDER), "date_dim")
    assert date.attached_to == "web_sales"
    assert date.path.id == "web_sales.ws_sold_date_sk=date_dim.d_date_sk"
    # The page's dates are set aside by the declared preference, not tied.
    assert not any("web_page" in path.id for path in date.tied)
    assert any(path.id.startswith("web_page.") for path in date.withdrawn)


def test_before_the_ruling_it_hung_on_the_page_by_the_alphabet() -> None:
    """What the preference changed, held so that it is seen if it comes back."""
    date = attachment(tree(GENDER, without_these_two()), "date_dim")
    assert date.attached_to == "web_page" and date.rule == "alphabetical"
    assert date.path.id == "web_page.wp_access_date_sk=date_dim.d_date_sk"


def test_the_customer_never_hangs_on_the_page() -> None:
    for anchors in (("web_sales", "web_page", "customer"), GENDER_AND_CUSTOMER):
        customer = attachment(tree(anchors), "customer")
        assert customer.attached_to != "web_page"
        assert not any("web_page" in path.id for path in (customer.path, *customer.tied))
        assert "web_page.wp_customer_sk=customer.c_customer_sk" in [path.id for path in customer.withdrawn]


def test_where_the_customer_can_hang_only_on_the_sale_or_the_page_it_hangs_on_the_sale() -> None:
    customer = attachment(tree(("web_sales", "web_page", "customer")), "customer")
    assert customer.attached_to == "web_sales"
    assert customer.path.id == "web_sales.ws_bill_customer_sk=customer.c_customer_sk"


def test_recorded_with_the_gender_anchors_the_customer_hangs_on_its_current_demographics() -> None:
    """Today's behaviour, pinned, not decided here. With the demographics
    in the plan the customer could hang on them by its current
    demographics, on the sale by the billing or the shipping key, or on
    the calendar by three of its dates: a separate tie, which the alphabet
    breaks (customer_demographics sorts first). The owner's ruling at stop
    3 of step 9 declined a third preference to decide it."""
    customer = attachment(tree(GENDER_AND_CUSTOMER), "customer")
    assert customer.attached_to == "customer_demographics" and customer.rule == "alphabetical"
    assert customer.path.id == "customer.c_current_cdemo_sk=customer_demographics.cd_demo_sk"


def test_the_billed_or_shipped_to_demographics_still_has_no_basis() -> None:
    demographics = attachment(tree(GENDER), "customer_demographics")
    assert demographics.rule == "alphabetical" and demographics.attached_to == "web_sales"
    assert demographics.path.id == "web_sales.ws_bill_cdemo_sk=customer_demographics.cd_demo_sk"
    # `tied` holds the route chosen and those it tied with.
    assert [path.id for path in demographics.tied] == [
        "web_sales.ws_bill_cdemo_sk=customer_demographics.cd_demo_sk",
        "web_sales.ws_ship_cdemo_sk=customer_demographics.cd_demo_sk",
    ]


def test_the_catalog_the_same_way() -> None:
    date = attachment(tree(("catalog_sales", "catalog_page", "date_dim")), "date_dim")
    assert date.attached_to == "catalog_sales"
    assert date.path.id == "catalog_sales.cs_sold_date_sk=date_dim.d_date_sk"
    assert not any("catalog_page" in path.id for path in date.tied)


def test_the_warnings_the_date_choice_is_reasoned_and_the_demographics_choice_is_not() -> None:
    codes = [(w.code, w.about[0]) for w in explain_tree(tree(GENDER), GRAPH).warnings]
    assert ("arbitrary_choice", "web_sales.ws_bill_cdemo_sk=customer_demographics.cd_demo_sk") in codes
    assert not any("date_dim" in about for _, about in codes)


def test_the_panel_states_the_date_choice_as_having_a_basis() -> None:
    """Stop 3 ruling, 2c: the amber block holds the one choice with no
    basis, and the narrative gives the date's basis, the declared reason."""
    made = document([reply(SALE_DATE_SQL)], *GENDER)
    loud = [w for w in made.paths.warnings if w.loud]
    assert [w.code for w in loud] == ["arbitrary_choice"]
    assert loud[0].plain.startswith("This answer uses the billed customer's demographics.")
    assert "date" not in loud[0].plain
    route = made.narrative.route
    assert "its date of sale" in route
    assert REASON.format("web").removesuffix(" (an operator's default)") in route
    assert route.count("it is arbitrary") == 1
    assert made.paths.diverged is False
