"""A snapshot written by hand, for tests that must not need a database.

Five tables: a miniature of the demonstration case (SDD figure 3) plus one
pair of parallel keys. Small enough to count on your fingers, which is the
point -- every number the tests assert can be checked by reading this file.

    5 tables, 13 columns, 3 primary keys, 6 foreign keys
"""

import pytest

from app.core.snapshot import Column, ForeignKey, PrimaryKey, SchemaSnapshot, Table


def _columns(table: str, *specs: tuple[str, str]) -> list[Column]:
    return [Column(table=table, name=name, data_type=data_type) for name, data_type in specs]


def _key(start: str, end: str, source: str = "catalog") -> ForeignKey:
    from_table, from_column = start.split(".")
    to_table, to_column = end.split(".")
    name = f"{from_column}_fk" if source == "catalog" else None
    return ForeignKey(from_table, (from_column,), to_table, (to_column,), source, name)


@pytest.fixture
def small_snapshot() -> SchemaSnapshot:
    return SchemaSnapshot(
        tables=tuple(
            Table(name) for name in ("catalog_sales", "customer", "customer_address", "store", "store_sales")
        ),
        columns=tuple(
            _columns("catalog_sales", ("cs_bill_addr_sk", "bigint"), ("cs_ship_addr_sk", "bigint"))
            + _columns("customer", ("c_customer_sk", "bigint"), ("c_current_addr_sk", "bigint"))
            + _columns("customer_address", ("ca_address_sk", "bigint"), ("ca_state", "character varying"))
            + _columns("store", ("s_store_sk", "bigint"), ("s_state", "character varying"))
            + _columns(
                "store_sales",
                ("ss_ticket_number", "bigint"),
                ("ss_store_sk", "bigint"),
                ("ss_addr_sk", "bigint"),
                ("ss_customer_sk", "bigint"),
                ("ss_ext_sales_price", "numeric(7,2)"),
            )
        ),
        primary_keys=(
            PrimaryKey("customer", ("c_customer_sk",)),
            PrimaryKey("customer_address", ("ca_address_sk",)),
            PrimaryKey("store", ("s_store_sk",)),
        ),
        foreign_keys=(
            # Route A: the address on the order.
            _key("store_sales.ss_addr_sk", "customer_address.ca_address_sk"),
            # Route B: the store where the sale happened.
            _key("store_sales.ss_store_sk", "store.s_store_sk"),
            # Route C, in two hops: the customer's address on file.
            _key("store_sales.ss_customer_sk", "customer.c_customer_sk"),
            _key("customer.c_current_addr_sk", "customer_address.ca_address_sk"),
            # Two keys from one table to the same target. The second is
            # marked as an overlay edge so provenance has something to carry.
            _key("catalog_sales.cs_bill_addr_sk", "customer_address.ca_address_sk"),
            _key("catalog_sales.cs_ship_addr_sk", "customer_address.ca_address_sk", source="overlay"),
        ),
    )
