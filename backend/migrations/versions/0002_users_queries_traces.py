"""users, queries and traces.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10

The second migration of the application store (DR-14, Design section 06,
DD-07, DD-17), for step 8: the trace persisted and served.

users     identifier, email, hashed password, creation time (DR-07). The
          password hash is nullable until authentication arrives at step
          11: a null hash is a user nobody can log in as.
          ONE ROW IS SEEDED HERE: the local user every query belongs to
          until step 11 (the owner's ruling 3 at step 8). It is made by the
          migration so that it exists on any database at head, a scratch
          one in a test included, and so that nothing creates a user as a
          side effect of serving a request.

queries   one row per question asked. user_id is here from the moment the
          table exists (DR-08), not retrofitted. The id is a UUID made by
          the application, so that the trace holds its own query id before
          anything is written. DD-07's five extracted columns sit HERE and
          not on traces (figure 4; ruling 1): a history list reads many of
          these rows and never the bodies (DD-17).
            outcome             one of the five the Design names
            had_ambiguity       an arbitrary choice touched the answer
                                given (DD-21 as amended at step 8)
            conformance_result  null when no SQL was checked
            cost_estimate       US dollars, every model call of the query
            duration_ms         the stages' durations, summed

traces    one per query: the one document in a relational schema (DD-07).
          Written once, read whole, never updated. The warehouse's rows
          are not in it: the row count is (DR-15).
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

LOCAL_USER_EMAIL = "local@cartograph.invalid"


def upgrade() -> None:
    op.execute(
        """
        create table users (
            id             bigint generated always as identity primary key,
            email          text not null unique,
            password_hash  text,
            created_at     timestamptz not null default now()
        )
        """
    )
    op.execute(f"insert into users (email) values ('{LOCAL_USER_EMAIL}')")

    op.execute(
        """
        create table queries (
            id                  uuid primary key,
            user_id             bigint not null references users (id),
            question            text not null,
            created_at          timestamptz not null,
            outcome             text not null check (outcome in
                                  ('answered', 'not_answerable', 'validation_failed', 'model_failed', 'execution_failed')),
            had_ambiguity       boolean not null,
            conformance_result  text check (conformance_result in ('conforms', 'diverged', 'incomplete', 'not_checked')),
            cost_estimate       numeric(12, 6) not null,
            duration_ms         integer not null
        )
        """
    )
    op.execute("create index queries_by_user on queries (user_id, created_at desc)")

    op.execute(
        """
        create table traces (
            query_id       uuid primary key references queries (id),
            trace_version  integer not null,
            body           jsonb not null
        )
        """
    )


def downgrade() -> None:
    op.execute("drop table traces")
    op.execute("drop table queries")
    op.execute("drop table users")
