"""queries.served_from_cache.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02

The fourth migration of the application store (DR-14), for step 14's
answer cache (FR-35, NFR-25, item 116).

queries.served_from_cache
          true for a query answered from the cache. Such an answer called
          no model and cost nothing, and it does not count towards the
          user's questions for the day: the daily count reads this
          column. False for every row before it, by a constant default,
          which Postgres adds without rewriting the table.
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table queries add column served_from_cache boolean not null default false")


def downgrade() -> None:
    op.execute("alter table queries drop column served_from_cache")
