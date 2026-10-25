"""The rules of an account, without a database (step 11, NFR-10, DR-07).

    an email      trimmed, lowercased, a minimal check of its form, at
                  most 254 characters
    a password    8 to 128 characters, no rules of composition; one
                  longer is refused before anything hashes it
    a hash        argon2id, salted, never the password
    a token       secrets.token_urlsafe(32); the store keeps its sha256
"""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from app.core.credentials import (
    MAX_PASSWORD,
    MIN_PASSWORD,
    SESSION_LIFETIME,
    CredentialRefused,
    check_password,
    expires_at,
    normalise_email,
    token_digest,
)
from app.shell import accounts as credentials
from app.shell.accounts import hash_password, new_token, verify_password

# --------------------------------------------------------------------------
# Emails
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("ada@example.com", "ada@example.com"),
        ("  Ada@Example.COM \n", "ada@example.com"),
        ("a.b+c@sub.example.org", "a.b+c@sub.example.org"),
    ],
)
def test_an_email_is_trimmed_and_lowercased(given, kept) -> None:
    assert normalise_email(given) == kept


@pytest.mark.parametrize(
    "given",
    ["", "   ", "ada", "ada@", "@example.com", "ada@example", "ada@@example.com", "a da@example.com",
     "ada@exa mple.com", "ada@.com", "ada@example.", "a@b@example.com", "x" * 250 + "@e.co"],
)  # fmt: skip
def test_an_email_not_of_the_form_is_refused(given) -> None:
    with pytest.raises(CredentialRefused):
        normalise_email(given)


def test_an_email_of_exactly_254_characters_is_taken() -> None:
    longest = "x" * 248 + "@e.com"
    assert len(longest) == 254 and normalise_email(longest) == longest


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------


@pytest.mark.parametrize("length", [MIN_PASSWORD, 9, 64, MAX_PASSWORD])
def test_a_password_of_8_to_128_characters_is_taken(length) -> None:
    assert (MIN_PASSWORD, MAX_PASSWORD) == (8, 128)
    assert check_password("p" * length) == "p" * length


@pytest.mark.parametrize("length", [0, 1, 7, 129, 10_000])
def test_a_password_outside_8_to_128_characters_is_refused(length) -> None:
    with pytest.raises(CredentialRefused):
        check_password("p" * length)


@pytest.mark.parametrize("given", ["password", "aaaaaaaa", "12345678", "        "])
def test_a_password_needs_no_composition(given) -> None:
    assert check_password(given) == given


def test_a_password_too_long_is_refused_before_anything_hashes_it(monkeypatch) -> None:
    """The owner's ruling 4: refused before any hashing."""
    calls: list = []
    monkeypatch.setattr(credentials, "_hasher", Recorder(calls))
    with pytest.raises(CredentialRefused):
        hash_password("p" * 129)
    assert verify_password(None, "p" * 129) is False
    assert verify_password("$argon2id$whatever", "p" * 129) is False
    assert calls == []


class Recorder:
    def __init__(self, calls: list) -> None:
        self.calls = calls

    def hash(self, password):
        self.calls.append("hash")
        return "h"

    def verify(self, stored, password):
        self.calls.append("verify")
        return True


def test_a_hash_is_argon2id_salted_and_not_the_password() -> None:
    first, second = hash_password("correct horse"), hash_password("correct horse")
    assert first.startswith("$argon2id$") and second.startswith("$argon2id$")
    assert first != second, "two hashes of one password differ by their salt"
    assert "correct horse" not in first


def test_a_hash_verifies_its_password_and_no_other() -> None:
    stored = hash_password("correct horse")
    assert verify_password(stored, "correct horse") is True
    assert verify_password(stored, "correct horsf") is False
    assert verify_password(stored, "") is False


def test_no_hash_verifies_nothing_and_still_does_the_work(monkeypatch) -> None:
    """A user with no password (the local user), or no user at all: the
    answer is no, after the same work as a wrong password, so that the
    time taken does not tell the two apart."""
    calls: list = []
    monkeypatch.setattr(credentials, "_hasher", Recorder(calls))
    monkeypatch.setattr(credentials, "_DUMMY", "$argon2id$dummy")
    assert verify_password(None, "correct horse") is False
    assert calls == ["verify"]


def test_a_hash_that_is_not_one_verifies_nothing() -> None:
    assert verify_password("not a hash", "correct horse") is False


# --------------------------------------------------------------------------
# Tokens and sessions
# --------------------------------------------------------------------------


def test_a_token_is_32_random_bytes_url_safe() -> None:
    tokens = {new_token() for _ in range(50)}
    assert len(tokens) == 50
    for token in tokens:
        assert len(token) == 43 and set(token) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def test_a_token_is_kept_as_its_sha256() -> None:
    token = new_token()
    assert token_digest(token) == hashlib.sha256(token.encode()).hexdigest()
    assert token not in token_digest(token)


def test_a_session_lasts_seven_days_from_login() -> None:
    login = datetime(2026, 10, 25, 9, 30, tzinfo=UTC)
    assert SESSION_LIFETIME == timedelta(days=7)
    assert expires_at(login) == datetime(2026, 11, 1, 9, 30, tzinfo=UTC)
