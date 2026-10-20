"""Fixtures for tests that need the application database.

Such a test never touches the application's own database: it gets a
scratch database on the same server, migrated to head by Alembic, and
dropped afterwards. So a test may insert, delete, or migrate down and up,
and the stored snapshot and its embeddings are never at risk.

Skipped where there is no database to connect to, which is the case in CI.
"""

import os
import secrets

import psycopg
import pytest
from alembic import command
from appdb import alembic_config
from ledger_guard import ledger_fingerprint

from app.shell.model_client import LEDGER


@pytest.fixture(autouse=True)
def the_real_ledger_is_never_written():
    """The spend ledger records what was paid for. No test may change it:
    a test that opens a pipeline gives it a ledger of its own (step 10b,
    the owner's ruling at stop C1)."""
    before = ledger_fingerprint(LEDGER)
    yield
    if ledger_fingerprint(LEDGER) != before:
        pytest.fail(f"this test changed the real spend ledger, {LEDGER}")


@pytest.fixture(scope="session")
def app_database_url() -> str:
    url = os.environ.get("APP_DATABASE_URL")
    if not url:
        pytest.skip("APP_DATABASE_URL is not set")
    try:
        psycopg.connect(url, connect_timeout=3).close()
    except psycopg.OperationalError:
        pytest.skip("the application database is not reachable")
    return url


@pytest.fixture
def empty_database(app_database_url):
    """The URL of a new, empty database: no tables, no extension."""
    name = f"cartograph_test_{secrets.token_hex(6)}"
    with psycopg.connect(app_database_url, autocommit=True) as connection:
        connection.execute(f'create database "{name}"')
    try:
        yield app_database_url.rsplit("/", 1)[0] + "/" + name
    finally:
        with psycopg.connect(app_database_url, autocommit=True) as connection:
            connection.execute(f'drop database "{name}" with (force)')


@pytest.fixture
def scratch_database(empty_database):
    """The URL of a scratch database migrated to head."""
    command.upgrade(alembic_config(empty_database), "head")
    return empty_database
