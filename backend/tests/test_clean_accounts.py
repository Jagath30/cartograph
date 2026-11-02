"""The smoke accounts' cleanup (item 111, step 14): two exact patterns,
never the local user, a dry run that deletes nothing, and --yes deleting
the accounts with their sessions, queries and traces, and nothing else."""

import uuid

import psycopg
import pytest
from tracing import document, reply

from app.clean_accounts import Counts, clean, kind_of, main
from app.smoke_api import smoke_email

BILL = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"
SMOKE = "smoke-20261101t101500z-0a1b2c3d@cartograph.invalid"
CHECK = "check-20261025t124621-a25a2201@cartograph.invalid"
LOCAL = "local@cartograph.invalid"


# --------------------------------------------------------------------------
# The patterns
# --------------------------------------------------------------------------


def test_what_smoke_makes_is_matched_as_smoke() -> None:
    for _ in range(20):
        assert kind_of(smoke_email()) == "smoke"


def test_the_two_patterns() -> None:
    assert kind_of(SMOKE) == "smoke"
    assert kind_of(CHECK) == "check"


def test_the_local_user_is_never_matched() -> None:
    assert kind_of(LOCAL) is None


@pytest.mark.parametrize(
    "email",
    [
        "smoke-20261101t101500z-0a1b2c3d@cartograph.invalid.example",
        "xsmoke-20261101t101500z-0a1b2c3d@cartograph.invalid",
        "smoke-20261101t101500z-0a1b2c3d@cartograph.invalidx",
        "smoke-20261101t101500z-0a1b2c3@cartograph.invalid",
        "smoke-20261101t101500z-0a1b2c3dd@cartograph.invalid",
        "smoke-20261101t101500z-0A1B2C3D@cartograph.invalid",
        "smoke-20261101t101500-0a1b2c3d@cartograph.invalid",
        "smoke-20261101T101500Z-0a1b2c3d@cartograph.invalid",
        "smoke-20261101t101500z-0a1b2c3d@example.com",
        "check-20261025t124621z-a25a2201@cartograph.invalid",
        "check-20261025t124621-a25a220@cartograph.invalid",
        "check-anything@cartograph.invalid",
        "smoke@cartograph.invalid",
        "ada@example.com",
        "local@cartograph.invalid ",
        "",
    ],
)
def test_every_other_address_is_never_matched(email) -> None:
    assert kind_of(email) is None


def test_it_takes_no_argument_but_yes(capsys) -> None:
    assert main(["--delete"]) == 2 and main(["yes"]) == 2 and main(["--yes", "--yes"]) == 2


# --------------------------------------------------------------------------
# Against a database
# --------------------------------------------------------------------------


def account(connection, email: str, queries: int = 0, sessions: int = 0) -> int:
    (user,) = connection.execute("insert into users (email) values (%s) returning id", (email,)).fetchone()
    for number in range(sessions):
        connection.execute(
            "insert into sessions (user_id, token_sha256, created_at, expires_at) "
            "values (%s, %s, now(), now() + interval '7 days')",
            (user, f"{user:032x}{number:032x}"),
        )
    for _ in range(queries):
        written = document([reply(BILL)], "catalog_sales", "customer_address").model_copy(
            update={"user_id": user, "query_id": uuid.uuid4()}
        )
        connection.execute(
            "insert into queries (id, user_id, question, created_at, outcome, had_ambiguity, conformance_result, "
            "cost_estimate, duration_ms) values (%s, %s, %s, now(), 'answered', false, 'conforms', 0.0001, 5)",
            (written.query_id, user, written.question),
        )
        connection.execute(
            "insert into traces (query_id, trace_version, body) values (%s, %s, %s::jsonb)",
            (written.query_id, written.trace_version, written.model_dump_json()),
        )
    return user


def everything(url: str) -> tuple:
    with psycopg.connect(url) as connection:
        return connection.execute(
            "select (select array_agg(email order by email) from users), (select count(*) from sessions), "
            "(select count(*) from queries), (select count(*) from traces)"
        ).fetchone()


@pytest.fixture
def populated(scratch_database):
    with psycopg.connect(scratch_database) as connection:
        local = connection.execute("select id from users where email = %s", (LOCAL,)).fetchone()[0]
        account(connection, SMOKE, queries=2, sessions=1)
        account(connection, "smoke-20261102t090000z-ffffffff@cartograph.invalid", queries=1, sessions=2)
        account(connection, CHECK)
        account(connection, "ada@example.com", queries=1, sessions=1)
        account(connection, "smoke-20261101t101500z-0a1b2c3@cartograph.invalid", queries=1)
        connection.execute(
            "insert into queries (id, user_id, question, created_at, outcome, had_ambiguity, cost_estimate, duration_ms) "
            "values (%s, %s, 'q', now(), 'answered', false, 0, 1)",
            (uuid.uuid4(), local),
        )
    return scratch_database


def test_the_dry_run_counts_and_deletes_nothing(populated) -> None:
    before = everything(populated)
    found, done = clean(populated, delete=False)
    assert found == Counts(smoke=2, check=1, sessions=3, queries=3, traces=3)
    assert done is None
    assert everything(populated) == before


def test_yes_deletes_only_the_matched_accounts_and_their_rows(populated) -> None:
    found, done = clean(populated, delete=True)
    assert done == found == Counts(smoke=2, check=1, sessions=3, queries=3, traces=3)
    emails, sessions, queries, traces = everything(populated)
    assert emails == sorted([LOCAL, "ada@example.com", "smoke-20261101t101500z-0a1b2c3@cartograph.invalid"])
    assert (sessions, queries, traces) == (1, 3, 2)


def test_run_again_it_finds_nothing(populated) -> None:
    clean(populated, delete=True)
    assert clean(populated, delete=False)[0] == Counts(0, 0, 0, 0, 0)


def test_the_dry_run_prints_counts_and_no_address(populated, monkeypatch, capsys) -> None:
    from app import clean_accounts

    class Settings:
        app_database_url = populated

    monkeypatch.setattr(clean_accounts, "get_settings", lambda: Settings())
    assert main([]) == 0
    printed = capsys.readouterr().out
    assert "FOUND   3 accounts (2 smoke, 1 check), 3 sessions, 3 queries, 3 traces" in printed
    assert "DRY RUN" in printed and "@" not in printed
