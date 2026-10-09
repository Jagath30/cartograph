"""The reading of Postgres's plan that the smoke check uses as its witness.
No database and no model: a plan written by hand."""

from app.smoke_pipeline import classes, plan_joins


def test_pairs_are_gathered_into_classes_and_the_order_does_not_matter() -> None:
    a, b, c, d = ("t", "a"), ("u", "b"), ("v", "c"), ("w", "d")
    assert classes([(a, b), (c, d)]) == {frozenset({a, b}), frozenset({c, d})}
    assert classes([(a, b), (c, d), (b, c)]) == {frozenset({a, b, c, d})}
    assert classes([(b, a)]) == classes([(a, b)]) and classes([]) == set()


PLAN = {
    "Node Type": "Aggregate",
    "Plans": [
        {
            "Node Type": "Hash Join",
            "Hash Cond": "(cs.cs_bill_cdemo_sk = cd.cd_demo_sk)",
            "Plans": [
                {
                    "Node Type": "Nested Loop",
                    "Plans": [
                        {"Node Type": "Seq Scan", "Relation Name": "catalog_sales", "Alias": "cs"},
                        {
                            "Node Type": "Index Scan",
                            "Relation Name": "date_dim",
                            "Alias": "d",
                            "Index Cond": "(d.d_date_sk = cs.cs_sold_date_sk)",
                            "Filter": "(d.d_year = 2001)",
                        },
                    ],
                },
                {
                    "Node Type": "Seq Scan",
                    "Relation Name": "customer_demographics",
                    "Alias": "cd",
                    "Filter": "(cd.cd_marital_status = 'M'::text)",
                },
            ],
        }
    ],
}


def test_join_conditions_are_read_from_the_plan_with_aliases_resolved_and_filters_left_out() -> None:
    assert plan_joins(PLAN) == {
        frozenset({("catalog_sales", "cs_bill_cdemo_sk"), ("customer_demographics", "cd_demo_sk")}),
        frozenset({("date_dim", "d_date_sk"), ("catalog_sales", "cs_sold_date_sk")}),
    }


def test_a_two_column_condition_gives_two_pairs_and_a_plan_with_no_join_gives_none() -> None:
    plan = {
        "Node Type": "Merge Join",
        "Merge Cond": "((sr.sr_item_sk = ss.ss_item_sk) AND (sr.sr_ticket_number = ss.ss_ticket_number))",
        "Plans": [
            {"Node Type": "Seq Scan", "Relation Name": "store_returns", "Alias": "sr"},
            {"Node Type": "Seq Scan", "Relation Name": "store_sales", "Alias": "ss"},
        ],
    }
    assert plan_joins(plan) == {
        frozenset({("store_returns", "sr_item_sk"), ("store_sales", "ss_item_sk")}),
        frozenset({("store_returns", "sr_ticket_number"), ("store_sales", "ss_ticket_number")}),
    }
    assert plan_joins({"Node Type": "Seq Scan", "Relation Name": "customer", "Alias": "customer"}) == set()
