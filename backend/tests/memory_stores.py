"""The two stores the API reads, kept in memory, for the tests that run
without a database (CI). Each does what its real one does through the
same doors; the tests that take `scratch_database` run the real ones.
"""

import uuid
from datetime import datetime

from app.core.credentials import check_password, expires_at, normalise_email, token_digest
from app.core.trace_document import TraceDocument
from app.shell.accounts import EmailTaken, SignedIn, hash_password, new_token, verify_password
from app.shell.trace_store import HISTORY_LIMIT, Listed


class Memory:
    """A trace store that keeps what it was given as the JSON it would
    have written, so that reading back goes through the same door."""

    def __init__(self, refuse: Exception | None = None) -> None:
        self.bodies: dict[uuid.UUID, str] = {}
        self.refuse = refuse

    def save(self, document: TraceDocument) -> None:
        if self.refuse:
            raise self.refuse
        self.bodies[document.query_id] = document.model_dump_json()

    def load(self, query_id: uuid.UUID, owner: int | None = None) -> TraceDocument | None:
        body = self.bodies.get(query_id)
        if body is None:
            return None
        document = TraceDocument.model_validate_json(body)
        return document if owner is None or document.user_id == owner else None

    def history(self, owner: int, limit: int = HISTORY_LIMIT) -> list[Listed]:
        documents = [TraceDocument.model_validate_json(body) for body in self.bodies.values()]
        mine = sorted((d for d in documents if d.user_id == owner), key=lambda d: d.created_at, reverse=True)
        return [Listed(d.query_id, d.question, d.created_at, d.outcome, d.had_ambiguity) for d in mine[:limit]]


class MemoryAccounts:
    """Users and sessions as the real store keeps them: the hash of a
    password, the sha256 of a token."""

    def __init__(self) -> None:
        # email -> (id, password hash)
        self.users: dict[str, tuple[int, str | None]] = {"local@cartograph.invalid": (1, None)}
        # token sha256 -> [user id, created, expires, revoked]
        self.sessions: dict[str, list] = {}

    def register(self, email: str, password: str) -> SignedIn:
        email = normalise_email(email)
        hashed = hash_password(check_password(password))
        if email in self.users:
            raise EmailTaken(email)
        user_id = max(user for user, _ in self.users.values()) + 1
        self.users[email] = (user_id, hashed)
        return SignedIn(user_id, email)

    def log_in(self, email: str, password: str, now: datetime) -> tuple[str, SignedIn] | None:
        email = email.strip().lower()
        user_id, hashed = self.users.get(email, (None, None))
        if not verify_password(hashed, password) or user_id is None:
            return None
        token = new_token()
        self.sessions[token_digest(token)] = [user_id, now, expires_at(now), None]
        return token, SignedIn(user_id, email)

    def signed_in(self, token: str, now: datetime) -> SignedIn | None:
        held = self.sessions.get(token_digest(token))
        if held is None:
            return None
        user_id, _, expires, revoked = held
        if revoked is not None or now >= expires:
            return None
        email = next(email for email, (user, _) in self.users.items() if user == user_id)
        return SignedIn(user_id, email)

    def log_out(self, token: str, now: datetime) -> None:
        held = self.sessions.get(token_digest(token))
        if held is not None and held[3] is None:
            held[3] = now
