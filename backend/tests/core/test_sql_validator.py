"""SqlValidator (FR-18 to FR-21, NFR-08, NFR-26, criterion 4).

Every statement here was written by hand, before any model had written
one. The schema is TPC-DS, read from the committed DDL, so all of this
runs in CI with no database.

NFR-26 asks for negative cases: most of this file is SQL that must be
rejected, and for each the test says which check fails and whether the
question ends there or the model may try again.
"""

import pytest

from app.core.sql_reading import schema_of
from app.core.sql_validator import (
    ALLOWED_BY_NAME,
    DANGEROUS_FUNCTIONS,
    DANGEROUS_PREFIXES,
    ValidatedSql,
    feedback,
    validate,
)
from tests.core.tpcds_files import ddl_snapshot

SCHEMA = schema_of(ddl_snapshot())


def check(sql: str):
    return validate(sql, SCHEMA)


def codes(sql: str) -> list[str]:
    return [finding.code for finding in check(sql).findings]


def test_the_schema_under_test_is_the_whole_warehouse() -> None:
    assert len(SCHEMA) == 24 and sum(len(columns) for columns in SCHEMA.values()) == 425
    assert "ss_item_sk" in SCHEMA["store_sales"]


# --------------------------------------------------------------------------
# What passes
# --------------------------------------------------------------------------

GOOD = [
    "SELECT s_store_name FROM store",
    "select count(*) from store_sales;",
    "SELECT * FROM item",
    "SELECT i.* FROM item AS i",
    "SELECT public.store.s_store_name FROM public.store",
    """SELECT s.s_store_name, SUM(ss.ss_net_paid) AS paid
       FROM store_sales AS ss JOIN store AS s ON ss.ss_store_sk = s.s_store_sk
       GROUP BY s.s_store_name ORDER BY paid DESC LIMIT 10""",
    # The benchmark's own style: comma joins, unqualified columns.
    """select d_year, i_brand_id, sum(ss_ext_sales_price) sum_agg
       from date_dim, store_sales, item
       where d_date_sk = ss_sold_date_sk and ss_item_sk = i_item_sk and i_manufact_id = 128 and d_moy = 11
       group by d_year, i_brand_id order by d_year, sum_agg desc limit 100""",
    """WITH per_item AS (SELECT ss_item_sk AS item_sk, SUM(ss_net_paid) AS paid FROM store_sales GROUP BY ss_item_sk)
       SELECT i.i_category, SUM(p.paid) FROM per_item AS p JOIN item AS i ON i.i_item_sk = p.item_sk GROUP BY i.i_category""",
    "SELECT x.n FROM (SELECT COUNT(*) AS n FROM customer) AS x",
    "SELECT d.a FROM (SELECT c_customer_sk, c_birth_year FROM customer) AS d(a, b)",
    "SELECT c_customer_sk FROM customer WHERE c_birth_year = (SELECT MAX(c_birth_year) FROM customer)",
    "SELECT i_item_sk FROM item WHERE EXISTS (SELECT 1 FROM store_sales WHERE ss_item_sk = i_item_sk)",
    "SELECT i_item_sk FROM item AS i WHERE i.i_item_sk IN (SELECT ss.ss_item_sk FROM store_sales AS ss)",
    "SELECT ss_item_sk FROM store_sales UNION ALL SELECT cs_item_sk FROM catalog_sales",
    "SELECT ss_item_sk, RANK() OVER (PARTITION BY ss_store_sk ORDER BY ss_net_paid DESC) FROM store_sales",
    "SELECT CASE WHEN ss_quantity > 10 THEN 'many' ELSE 'few' END AS size, COUNT(*) FROM store_sales GROUP BY size",
    "SELECT ss_net_paid / NULLIF(ss_quantity, 0), CAST(ss_quantity AS numeric), ss_quantity::text FROM store_sales",
    "SELECT EXTRACT(year FROM d_date), DATE_TRUNC('month', d_date), d_date + INTERVAL '1 day' FROM date_dim",
    "SELECT c_last_name || ', ' || c_first_name FROM customer WHERE c_last_name ILIKE 'a%' AND c_birth_year BETWEEN 1970 AND 1980",
    "SELECT COUNT(*) FILTER (WHERE ss_quantity > 1), COUNT(DISTINCT ss_customer_sk) FROM store_sales",
    # A string that looks like a second statement is one string.
    "SELECT 'a; DROP TABLE store' AS text FROM store",
    "SELECT s_store_name FROM store -- ; DROP TABLE store",
    # An alias may be spelled like another table. It is still an alias.
    "SELECT store_returns.ss_item_sk FROM store_sales AS store_returns",
]


@pytest.mark.parametrize("sql", GOOD)
def test_a_read_over_the_schema_passes_every_check(sql) -> None:
    result = check(sql)
    assert [(f.code, f.message) for f in result.findings] == []
    assert (result.syntax, result.read_only, result.references) == ("pass", "pass", "pass")
    assert result.passed and not result.terminal and not result.retryable


def test_what_is_validated_is_the_text_that_was_handed_in_byte_for_byte() -> None:
    """Rule 3 of step 7. Odd spacing, a comment, a trailing semicolon, a
    percent sign: none of it is tidied."""
    sql = "select  s_store_name\n\tFROM store -- which stores\nWHERE s_store_name LIKE '%a%' ;  "
    validated = check(sql).validated
    assert isinstance(validated, ValidatedSql)
    assert validated.text is sql


def test_a_validated_sql_cannot_be_made_by_hand() -> None:
    """NFR-08: the executor takes a ValidatedSql, and validate() is the
    only thing that can make one."""
    with pytest.raises(TypeError, match="made by validate"):
        ValidatedSql("DROP TABLE store")
    with pytest.raises(TypeError, match="made by validate"):
        ValidatedSql("DROP TABLE store", object())


# --------------------------------------------------------------------------
# Syntax: retryable
# --------------------------------------------------------------------------


@pytest.mark.parametrize("sql", ["SELEC s_store_name FROM store", "SELECT FROM WHERE", "SELECT (s_store_name FROM store", "SELECT 'unclosed FROM store"])
def test_text_that_does_not_parse_fails_syntax_and_may_be_retried(sql) -> None:
    result = check(sql)
    assert (result.syntax, result.read_only, result.references) == ("fail", "not_run", "not_run")
    assert codes(sql) == ["not_parsed"]
    assert result.retryable and not result.terminal and result.validated is None


@pytest.mark.parametrize("sql", ["", "   ", ";", "-- only a comment"])
def test_text_with_no_statement_in_it_fails_syntax(sql) -> None:
    assert codes(sql) == ["empty"] and check(sql).retryable


# --------------------------------------------------------------------------
# read_only: terminal, every one (criterion 4, DD-15)
# --------------------------------------------------------------------------

WRITES = [
    ("DROP TABLE store", "not_a_select"),
    ("DELETE FROM store", "not_a_select"),
    ("UPDATE store SET s_store_name = 'x'", "not_a_select"),
    ("INSERT INTO store (s_store_sk) VALUES (1)", "not_a_select"),
    ("INSERT INTO store SELECT * FROM store", "not_a_select"),
    ("TRUNCATE store", "not_a_select"),
    ("CREATE TABLE t AS SELECT * FROM store", "not_a_select"),
    ("CREATE VIEW v AS SELECT * FROM store", "not_a_select"),
    ("ALTER TABLE store ADD COLUMN x int", "not_a_select"),
    ("MERGE INTO store USING item ON true WHEN MATCHED THEN DELETE", "not_a_select"),
    ("GRANT ALL ON store TO public", "not_a_select"),
    ("COPY store TO STDOUT", "not_a_select"),
    ("COPY (SELECT * FROM store) TO PROGRAM 'cat'", "not_a_select"),
    ("SET statement_timeout = 0", "not_a_select"),
    ("EXPLAIN ANALYZE DELETE FROM store", "not_a_select"),
    ("VACUUM store", "not_a_select"),
    ("DO $$ BEGIN DELETE FROM store; END $$", "not_a_select"),
    ("CALL something()", "not_a_select"),
    ("BEGIN", "not_a_select"),
    ("VALUES (1)", "not_a_select"),
    ("TABLE store", "not_a_select"),
    ("SELECT * INTO copy_of_store FROM store", "select_into"),
    ("SELECT * FROM store FOR UPDATE", "locking"),
    ("SELECT * FROM store FOR SHARE", "locking"),
    ("SELECT * FROM store AS s FOR NO KEY UPDATE OF s", "locking"),
    ("WITH gone AS (DELETE FROM store RETURNING *) SELECT * FROM gone", "write"),
    ("WITH new AS (INSERT INTO store (s_store_sk) VALUES (1) RETURNING *) SELECT * FROM new", "write"),
    ("WITH up AS (UPDATE store SET s_store_name = 'x' RETURNING *) SELECT * FROM up", "write"),
    ("WITH a AS (SELECT 1 AS x), b AS (DELETE FROM store RETURNING s_store_sk) SELECT * FROM a", "write"),
]


@pytest.mark.parametrize(("sql", "code"), WRITES)
def test_anything_that_is_not_a_read_fails_read_only_and_ends_the_question(sql, code) -> None:
    result = check(sql)
    assert code in codes(sql), codes(sql)
    assert (result.syntax, result.read_only, result.references) == ("pass", "fail", "not_run")
    assert result.terminal and not result.retryable and result.validated is None
    assert all(finding.terminal and finding.check == "read_only" for finding in result.findings)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; SELECT 2",
        "SELECT s_store_name FROM store; DROP TABLE store",
        "SELECT s_store_name FROM store; DELETE FROM store;",
        "DROP TABLE store; SELECT s_store_name FROM store",
        "SELECT 1;\n-- harmless\nUPDATE store SET s_store_name = 'x'",
    ],
)
def test_more_than_one_statement_ends_the_question_whatever_the_statements_are(sql) -> None:
    result = check(sql)
    assert codes(sql) == ["multiple_statements"]
    assert result.read_only == "fail" and result.terminal


DANGEROUS_CALLS = [
    "pg_sleep(10)", "pg_sleep_for('5 minutes')", "pg_sleep_until('tomorrow')", "set_config('statement_timeout', '0', false)",
    "pg_terminate_backend(1)", "pg_cancel_backend(1)", "pg_read_file('/etc/passwd')", "pg_read_binary_file('x')",
    "pg_ls_dir('.')", "pg_stat_file('x')", "lo_import('/etc/passwd')", "lo_export(1, '/tmp/x')", "lo_unlink(1)",
    "lo_creat(1)", "lo_from_bytea(0, 'x')", "dblink('host=x', 'select 1')", "dblink_exec('x', 'drop table t')",
    "pg_advisory_lock(1)", "pg_advisory_xact_lock(1)", "pg_try_advisory_lock(1)", "nextval('s')", "setval('s', 1)",
    "pg_notify('channel', 'x')", "pg_reload_conf()", "pg_rotate_logfile()", "pg_switch_wal()",
    "pg_create_restore_point('x')", "pg_logical_emit_message(true, 'a', 'b')", "pg_export_snapshot()",
    "pg_promote()", "pg_wal_replay_pause()", "pg_stat_reset()", "pg_drop_replication_slot('x')",
    "query_to_xml('select pg_sleep(100)', true, true, '')", "query_to_xml_and_xmlschema('s', true, true, '')",
    "table_to_xml('store', true, true, '')", "cursor_to_xml('c', 1, true, true, '')",
    "database_to_xml(true, true, '')", "schema_to_xml('public', true, true, '')",
    "PG_SLEEP(10)", "pg_catalog.pg_sleep(10)",
]  # fmt: skip


@pytest.mark.parametrize("call", DANGEROUS_CALLS)
def test_a_function_known_for_its_side_effect_ends_the_question_like_a_write(call) -> None:
    for sql in (
        f"SELECT {call}",
        f"SELECT s_store_name FROM store WHERE {call} IS NOT NULL",
        f"SELECT s_store_name, (SELECT {call}) FROM store",
        f"WITH w AS (SELECT {call} AS x) SELECT s_store_name FROM store, w",
        f"SELECT s_store_name FROM store ORDER BY {call}",
    ):
        result = check(sql)
        assert codes(sql) == ["dangerous_function"], (sql, codes(sql))
        assert result.read_only == "fail" and result.terminal and result.validated is None


def test_the_dangerous_list_is_spelled_in_lower_case_as_names_are_compared() -> None:
    for name in (*DANGEROUS_FUNCTIONS, *DANGEROUS_PREFIXES, *ALLOWED_BY_NAME):
        assert name == name.lower()
    assert not DANGEROUS_FUNCTIONS & ALLOWED_BY_NAME


# --------------------------------------------------------------------------
# references: retryable
# --------------------------------------------------------------------------

UNKNOWN = [
    ("SELECT name FROM shops", ["unknown_table", "unknown_column"]),
    ("SELECT s.s_store_name FROM shops AS s", ["unknown_table"]),
    ("SELECT s_store_name FROM store JOIN suppliers ON true", ["unknown_table"]),
    ("SELECT s_store_nam FROM store", ["unknown_column"]),
    ("SELECT s.s_store_nam FROM store AS s", ["unknown_column"]),
    # A real column, of a table this query does not read.
    ("SELECT i_category FROM store", ["unknown_column"]),
    ("SELECT store.i_category FROM store, item", ["unknown_column"]),
    # An alias hides the table's own name, as it does in Postgres.
    ("SELECT store.s_store_name FROM store AS s", ["unknown_column"]),
    ("SELECT x.s_store_name FROM store AS s", ["unknown_column"]),
    ("SELECT s_store_name FROM store WHERE s_nope = 1", ["unknown_column"]),
    ("SELECT s_store_name FROM store ORDER BY s_nope", ["unknown_column"]),
    ("SELECT ss.ss_item_sk FROM store_sales AS ss JOIN item AS i ON ss.ss_item_sk = i.i_nope", ["unknown_column"]),
    ("WITH c AS (SELECT s_store_sk AS k FROM store) SELECT c.s_store_sk FROM c", ["unknown_column"]),
    ("SELECT d.b FROM (SELECT s_store_sk AS a FROM store) AS d", ["unknown_column"]),
    ("SELECT s_store_name FROM store WHERE s_store_sk IN (SELECT ss_nope FROM store_sales)", ["unknown_column"]),
    # The system catalogs are not the warehouse.
    ("SELECT relname FROM pg_class", ["unknown_table", "unknown_column"]),
    ("SELECT c.relname FROM pg_catalog.pg_class AS c", ["schema_not_allowed"]),
    ("SELECT t.table_name FROM information_schema.tables AS t", ["schema_not_allowed"]),
    ("SELECT u.usename FROM pg_catalog.pg_user AS u", ["schema_not_allowed"]),
    ("SELECT s.s_store_name FROM other_db.public.store AS s", ["schema_not_allowed"]),
    ("SELECT s.passwd FROM pg_shadow AS s", ["unknown_table"]),
]


@pytest.mark.parametrize(("sql", "expected"), UNKNOWN)
def test_a_table_or_column_the_schema_does_not_hold_fails_references_and_may_be_retried(sql, expected) -> None:
    result = check(sql)
    assert codes(sql) == expected
    assert (result.syntax, result.read_only, result.references) == ("pass", "pass", "fail")
    assert result.retryable and not result.terminal and result.validated is None


def test_a_column_two_tables_here_both_hold_must_be_qualified() -> None:
    sql = "SELECT d_date_sk FROM date_dim AS a, date_dim AS b"
    assert codes(sql) == ["ambiguous_column"]
    assert "a, b" in check(sql).findings[0].message


def test_an_output_name_may_be_used_to_order_and_group_and_nowhere_else() -> None:
    assert check("SELECT SUM(ss_net_paid) AS paid FROM store_sales ORDER BY paid").passed
    assert check("SELECT ss_quantity * 2 AS twice, COUNT(*) FROM store_sales GROUP BY twice").passed
    assert codes("SELECT SUM(ss_net_paid) AS paid FROM store_sales WHERE paid > 0") == ["unknown_column"]


@pytest.mark.parametrize(
    "call",
    ["version()", "current_setting('server_version')", "pg_typeof(s_store_sk)", "generate_series(1, 10)",
     "txid_current()", "has_table_privilege('store', 'select')", "current_user", "random()", "md5(s_store_name)",
     "pg_database_size('x')", "made_up_function(s_store_sk)"],
)  # fmt: skip
def test_a_function_merely_not_on_the_allowlist_may_be_retried_and_is_named(call) -> None:
    sql = f"SELECT {call} FROM store"
    result = check(sql)
    assert codes(sql) == ["function_not_allowed"]
    assert result.read_only == "pass" and result.references == "fail"
    assert result.retryable and not result.terminal
    # sqlglot knows some of these by another name (version, random), and
    # the message uses the name it knows.
    assert result.findings[0].message.startswith("The function ") and "()" in result.findings[0].message
    if call.startswith(("made_up_function", "pg_typeof", "txid_current")):
        assert call.split("(")[0] + "()" in result.findings[0].message


ALLOWED_SPELLINGS = [
    "count(*)", "count(distinct a)", "sum(a)", "avg(a)", "min(a)", "max(a)", "stddev(a)", "stddev_pop(a)",
    "stddev_samp(a)", "variance(a)", "var_pop(a)", "var_samp(a)", "corr(a, b)", "covar_pop(a, b)",
    "string_agg(t, ',')", "array_agg(a)", "bool_and(a > 1)", "bool_or(a > 1)", "every(a > 1)",
    "percentile_cont(0.5) within group (order by a)", "percentile_disc(0.5) within group (order by a)",
    "mode() within group (order by a)", "row_number() over ()", "rank() over (order by a)",
    "dense_rank() over (order by a)", "percent_rank() over (order by a)", "cume_dist() over (order by a)",
    "ntile(4) over (order by a)", "lag(a) over (order by a)", "lead(a) over (order by a)",
    "first_value(a) over ()", "last_value(a) over ()", "nth_value(a, 2) over ()",
    "sum(a) over (partition by b order by a rows between unbounded preceding and current row)",
    "abs(a)", "ceil(a)", "ceiling(a)", "floor(a)", "round(a, 2)", "trunc(a)", "sqrt(a)", "cbrt(a)", "power(a, 2)",
    "exp(a)", "ln(a)", "log(a)", "sign(a)", "mod(a, 2)", "a % 2", "div(a, 2)", "greatest(a, b)", "least(a, b)",
    "pi()", "width_bucket(a, 1, 2, 3)", "coalesce(a, b)", "nullif(a, 0)", "cast(a as numeric)", "a::numeric",
    "case when a > 1 then 1 else 0 end", "upper(t)", "lower(t)", "length(t)", "char_length(t)",
    "substring(t, 1, 2)", "substring(t from 1 for 2)", "substr(t, 1, 2)", "trim(t)", "ltrim(t)", "rtrim(t)",
    "btrim(t)", "concat(t, t)", "concat_ws(',', t, t)", "left(t, 2)", "right(t, 2)", "replace(t, 'x', 'y')",
    "initcap(t)", "position('x' in t)", "strpos(t, 'x')", "lpad(t, 2, '0')", "rpad(t, 2, '0')",
    "split_part(t, ',', 1)", "reverse(t)", "repeat(t, 2)", "translate(t, 'x', 'y')", "t ~ 'x'",
    "regexp_replace(t, 'x', 'y')", "to_number(t, '9')", "t || t", "extract(year from d)", "date_part('year', d)",
    "date_trunc('month', d)", "current_date", "current_timestamp", "now()", "to_char(d, 'YYYY')",
    "to_date(t, 'YYYY')", "to_timestamp(t, 'YYYY')", "age(d, d)", "make_date(2001, 2, 3)", "date(d)",
    "date '2001-01-01'", "d + interval '1 day'", "count(*) filter (where a > 1)", "any_value(a)",
    "a in (1, 2)", "a between 1 and 2", "t like 'x%'", "a is null", "a is distinct from b", "a > 1 and b > 1 or a < 0",
    "t ~* 'x'", "a = any(array[1, 2])", "a = all(array[1, 2])", "a in (select ss_quantity from store_sales)",
    "exists (select 1 from store)", "array[1, 2]",
]  # fmt: skip


@pytest.mark.parametrize("spelling", ALLOWED_SPELLINGS)
def test_what_a_question_about_a_warehouse_needs_is_on_the_allowlist(spelling) -> None:
    """Each spelling as Postgres writes it. A false rejection here would
    cost a retry on an honest query."""
    sql = f"SELECT {spelling} FROM (SELECT ss_quantity AS a, ss_net_paid AS b, 'x' AS t, CURRENT_DATE AS d FROM store_sales) AS x"
    assert codes(sql) == []


# --------------------------------------------------------------------------
# What the model is told before it is asked again
# --------------------------------------------------------------------------


def test_feedback_names_every_fault_and_nothing_else() -> None:
    result = check("SELECT s.s_nope, made_up(s.s_store_sk) FROM store AS s JOIN shops ON true")
    text = feedback(result)
    assert text.startswith("The SQL was not accepted:\n- ")
    assert "There is no table named shops." in text
    assert "s.s_nope" in text and "made_up()" in text
    assert text.count("\n- ") == 3


def test_several_faults_of_one_kind_are_each_reported_once() -> None:
    assert codes("SELECT s_nope, s_nope FROM store WHERE s_nope > 1") == ["unknown_column"]
    assert codes("SELECT pg_sleep(1), pg_sleep(1)") == ["dangerous_function"]
    assert codes("SELECT s_nope, s_gone FROM store") == ["unknown_column", "unknown_column"]


def test_a_write_is_reported_as_a_write_and_its_references_are_not_discussed() -> None:
    """A DROP of a table that does not exist is a DROP."""
    assert codes("DROP TABLE no_such_table") == ["not_a_select"]
    assert codes("SELECT pg_sleep(1) FROM no_such_table") == ["dangerous_function"]
