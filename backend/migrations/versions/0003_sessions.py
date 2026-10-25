"""sessions.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25

The third migration of the application store (DR-14), for step 11:
criterion 12's sessions that expire (NFR-10).

users     unchanged. The second migration made what DR-07 asks for: an
          identifier, an email, a hashed password and a creation time.
          The local user keeps its null hash, so nobody can log in as it,
          and keeps every query stored before this step (item 76).

sessions  one row per login. The token itself is never stored: the row
          holds its sha256, so a copy of this table signs no one in.
            expires_at   seven days from login, absolute, never extended
            revoked_at   null until logout; a revoked row is refused
          The Design's figure 4 has no sessions table; it is owed to the
          Design at step 12.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table sessions (
            id            bigint generated always as identity primary key,
            user_id       bigint not null references users (id),
            token_sha256  text not null unique check (token_sha256 ~ '^[0-9a-f]{64}$'),
            created_at    timestamptz not null,
            expires_at    timestamptz not null,
            revoked_at    timestamptz,
            check (expires_at > created_at)
        )
        """
    )


def downgrade() -> None:
    op.execute("drop table sessions")
