"""The accounts the smoke commands make, listed, and with --yes deleted
with their sessions, queries and traces (item 111, step 14).

    docker compose exec -T backend python -m app.clean_accounts          # a dry run: counts only
    docker compose exec -T backend python -m app.clean_accounts --yes    # deletes them
    ./scripts/neon.sh accounts [--yes]                                   # the same, on Neon

IT MATCHES ONLY TWO EXACT PATTERNS, whole, in Python and never with a SQL
pattern:

  smoke-<yyyymmdd>t<hhmmss>z-<8 hex>@cartograph.invalid
            what app.smoke_api.smoke_email makes, for smoke.sh and
            smoke_deployed (step 11)
  check-<yyyymmdd>t<hhmmss>-<8 hex>@cartograph.invalid
            the check account of step 11's C4, as the store holds it

Never the seeded local user (local@cartograph.invalid), which is refused
by name even if a pattern were ever widened to reach it, and never any
other address.

THE DEFAULT IS A DRY RUN that prints counts and nothing else: no email,
no id. --yes deletes, in one transaction, the matched accounts' traces,
then their queries, then their sessions, then the accounts (no key
cascades), and prints the counts deleted. On Neon it is run only after
the owner's ruling on the dry run's counts.

Exit: 0 done; 2 the arguments were not understood.
"""

import re
import sys
from dataclasses import dataclass

import psycopg

from app.config import get_settings
from app.shell.trace_store import LOCAL_USER_EMAIL

PATTERNS = {
    "smoke": re.compile(r"smoke-\d{8}t\d{6}z-[0-9a-f]{8}@cartograph\.invalid"),
    "check": re.compile(r"check-\d{8}t\d{6}-[0-9a-f]{8}@cartograph\.invalid"),
}


def kind_of(email: str) -> str | None:
    """'smoke' or 'check' for an address one of the two patterns matches
    whole; None for every other, and always for the local user."""
    if email == LOCAL_USER_EMAIL:
        return None
    for kind, pattern in PATTERNS.items():
        if pattern.fullmatch(email):
            return kind
    return None


@dataclass(frozen=True)
class Counts:
    smoke: int
    check: int
    sessions: int
    queries: int
    traces: int

    def line(self, verb: str) -> str:
        return (
            f"{verb} {self.smoke + self.check} accounts ({self.smoke} smoke, {self.check} check), "
            f"{self.sessions} sessions, {self.queries} queries, {self.traces} traces"
        )


def matched(connection) -> dict[int, str]:
    rows = connection.execute("select id, email from users order by id").fetchall()
    found = {user_id: kind_of(email) for user_id, email in rows}
    return {user_id: kind for user_id, kind in found.items() if kind is not None}


def count(connection, users: dict[int, str]) -> Counts:
    ids = list(users)
    sessions, queries, traces = connection.execute(
        """
        select (select count(*) from sessions where user_id = any(%(ids)s)),
               (select count(*) from queries where user_id = any(%(ids)s)),
               (select count(*) from traces t join queries q on q.id = t.query_id where q.user_id = any(%(ids)s))
        """,
        {"ids": ids},
    ).fetchone()
    kinds = list(users.values())
    return Counts(kinds.count("smoke"), kinds.count("check"), sessions, queries, traces)


def clean(dsn: str, delete: bool) -> tuple[Counts, Counts | None]:
    """The counts found, and with `delete` the counts deleted, from one
    transaction that holds the rows it counts."""
    with psycopg.connect(dsn) as connection:
        users = matched(connection)
        found = count(connection, users)
        if not delete:
            connection.rollback()
            return found, None
        ids = {"ids": list(users)}
        traces = connection.execute(
            "delete from traces where query_id in (select id from queries where user_id = any(%(ids)s))", ids
        ).rowcount
        queries = connection.execute("delete from queries where user_id = any(%(ids)s)", ids).rowcount
        sessions = connection.execute("delete from sessions where user_id = any(%(ids)s)", ids).rowcount
        deleted = connection.execute("delete from users where id = any(%(ids)s) and email <> %(local)s",
                                     ids | {"local": LOCAL_USER_EMAIL}).rowcount  # fmt: skip
        done = Counts(found.smoke, found.check, sessions, queries, traces)
        if deleted != found.smoke + found.check or done != found:
            connection.rollback()
            raise RuntimeError("the rows deleted were not the rows counted; nothing was deleted")
        return found, done


def main(argv: list[str]) -> int:
    if argv not in ([], ["--yes"]):
        print("usage: python -m app.clean_accounts [--yes]", file=sys.stderr)
        return 2
    found, done = clean(get_settings().app_database_url, delete=argv == ["--yes"])
    print(found.line("FOUND  "))
    print(done.line("DELETED") if done else "DRY RUN  nothing was deleted; --yes deletes these")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
