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
