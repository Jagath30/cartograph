"""The account store: the repository for users and their sessions (step
11, DR-07, NFR-10).

Impure (DD-01): the only code that touches these two tables, and the one
place a password is hashed or a token made, both needing randomness.
What an email and a password may be is app.core.credentials'.

ARGON2ID, with the library's own parameters. A user with no hash (the
local user, who cannot log in), and an email no account holds, are each
checked against a hash of nothing, so that a failed login takes the same
time whatever failed. A password over 128 characters is refused before
anything hashes it (the owner's ruling 4).

THE TOKEN is secrets.token_urlsafe(32), handed to the caller once and
never stored: the row holds its sha256.

A FAILED LOGIN IS None, whatever failed: an email no account holds, a
wrong password, or the local user, who has no password. The caller says
one thing for all three.

A SESSION IS A ROW HOLDING THE TOKEN'S sha256. It is good from login
until seven days after, and until logout revokes it; nothing extends it.
`now` is the caller's, so that a test can stand at any moment.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.credentials import MAX_PASSWORD, check_password, expires_at, normalise_email, token_digest

_hasher = PasswordHasher()
# Made on first use: a hash costs about as much as a login.
_DUMMY: str | None = None


def hash_password(password: str) -> str:
    return _hasher.hash(check_password(password))


def verify_password(stored: str | None, password: str) -> bool:
    """True only for the password the stored hash was made from."""
    if len(password) > MAX_PASSWORD:
        return False
    try:
        if stored is None:
            _hasher.verify(_dummy(), password)
            return False
        return _hasher.verify(stored, password) is True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _dummy() -> str:
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = _hasher.hash(secrets.token_urlsafe(16))
    return _DUMMY


def new_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class SignedIn:
    user_id: int
    email: str


class EmailTaken(Exception):
    """An account already holds this email."""


class Accounts:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def register(self, email: str, password: str) -> SignedIn:
        email = normalise_email(email)
        hashed = hash_password(check_password(password))
        try:
            with psycopg.connect(self._dsn) as connection:
                (user_id,) = connection.execute(
                    "insert into users (email, password_hash) values (%s, %s) returning id", (email, hashed)
                ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise EmailTaken(email) from None
        return SignedIn(user_id, email)

    def log_in(self, email: str, password: str, now: datetime) -> tuple[str, SignedIn] | None:
        """The token, given once and never stored, and who it signs in."""
        email = email.strip().lower()
        with psycopg.connect(self._dsn) as connection:
            row = connection.execute("select id, password_hash from users where email = %s", (email,)).fetchone()
        user_id, hashed = row if row is not None else (None, None)
        if not verify_password(hashed, password) or user_id is None:
            return None
        token = new_token()
        with psycopg.connect(self._dsn) as connection:
            connection.execute(
                "insert into sessions (user_id, token_sha256, created_at, expires_at) values (%s, %s, %s, %s)",
                (user_id, token_digest(token), now, expires_at(now)),
            )
        return token, SignedIn(user_id, email)

    def signed_in(self, token: str, now: datetime) -> SignedIn | None:
        with psycopg.connect(self._dsn) as connection:
            row = connection.execute(
                """
                select users.id, users.email from sessions join users on users.id = sessions.user_id
                where sessions.token_sha256 = %s and sessions.revoked_at is null and sessions.expires_at > %s
                """,
                (token_digest(token), now),
            ).fetchone()
        return SignedIn(*row) if row is not None else None

    def log_out(self, token: str, now: datetime) -> None:
        with psycopg.connect(self._dsn) as connection:
            connection.execute(
                "update sessions set revoked_at = %s where token_sha256 = %s and revoked_at is null",
                (now, token_digest(token)),
            )
