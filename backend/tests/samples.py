"""A schema snapshot written by hand, for tests of the application store."""

from app.core.snapshot import Column, ForeignKey, PrimaryKey, SchemaSnapshot, Table

# One of everything awkward: a table comment, a column comment, a nullable
# and a not-null column, a two-column primary key, a two-column overlay key
# with a note, a catalog key with a name, and text that needs quoting.
SMALL = SchemaSnapshot(
    tables=(
        Table("sale", "every sale", "sale", "sale. Table sale."),
        Table("return", None, "return", "return. Table return."),
        Table("shop", None, "shop", "shop. Table shop."),
    ),
    columns=(
        Column("sale", "item", "bigint", False, None, "sale — item", "sale — item. Integer column in table sale."),
        Column("sale", "ticket", "bigint", False, None, "sale — ticket", "sale — ticket. Integer column in table sale."),
        Column("sale", "shop_key", "bigint", True, None, "sale — shop key", "sale — shop key. Integer column in table sale."),
        Column("sale", "price", "numeric(7,2)", True, "what it's sold for", "what it's sold for", "what it's sold for. Numeric column in table sale."),
        Column("return", "item", "bigint", True, None, "return — item", "return — item. Integer column in table return."),
        Column("return", "ticket", "bigint", True, None, "return — ticket", "return — ticket. Integer column in table return."),
        Column("return", "amount", "numeric(7,2)", True, None, "return — amount", "return — amount. Numeric column in table return."),
        Column("shop", "key", "bigint", False, None, "shop — key", "shop — key. Integer column in table shop."),
        Column("shop", "region", "character varying(20)", True, None, "shop — region", "shop — region. Text column in table shop."),
    ),
    primary_keys=(PrimaryKey("sale", ("ticket", "item")), PrimaryKey("shop", ("key",))),
    foreign_keys=(
        ForeignKey("sale", ("shop_key",), "shop", ("key",), "catalog", "sale_shop_fk"),
        ForeignKey("return", ("item", "ticket"), "sale", ("item", "ticket"), "overlay", None, "measured: every return matches one sale"),
    ),
)  # fmt: skip
