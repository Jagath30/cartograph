"""The rules of an account: what an email and a password may be, what a
session's token is kept as, and when a session ends (step 11, NFR-10,
DR-07).

Pure (DD-01). Hashing a password and making a token need randomness (a
salt, a token), so they are the shell's (app.shell.accounts); what each
must be is decided here.

A PASSWORD IS 8 TO 128 CHARACTERS, with no rules of composition. One
longer is refused before anything hashes it, at both doors: a hash of a
very long text is work an attacker could ask for at will (the owner's
ruling 4).

THE TOKEN IS NEVER STORED. The store keeps its sha256: a copy of the
sessions table signs no one in.
"""

import hashlib
from datetime import datetime, timedelta

MIN_PASSWORD = 8
MAX_PASSWORD = 128
MAX_EMAIL = 254
# From login, absolute, never extended.
SESSION_LIFETIME = timedelta(days=7)


class CredentialRefused(ValueError):
    """An email or a password that is not of the form an account takes.
    The message says which rule, and never repeats what was given."""


def normalise_email(raw: str) -> str:
    """Trimmed and lowercased, then a minimal check of form, not of
    deliverability: something, one @, a domain with a dot inside it, and
    no spaces anywhere."""
    email = raw.strip().lower()
    local, at, domain = email.partition("@")
    labels = domain.split(".")
    if (
        len(email) > MAX_EMAIL
        or not at
        or not local
        or "@" in domain
        or any(character.isspace() for character in email)
        or len(labels) < 2
        or not all(labels)
    ):
        raise CredentialRefused("That is not an email address.")
    return email


def check_password(password: str) -> str:
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise CredentialRefused(f"A password is {MIN_PASSWORD} to {MAX_PASSWORD} characters.")
    return password


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def expires_at(login: datetime) -> datetime:
    return login + SESSION_LIFETIME
