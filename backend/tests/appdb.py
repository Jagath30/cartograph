"""Alembic, pointed at a database of the caller's choosing. A module of its
own, not conftest.py, so that a test can import it by a name that means
one thing: tests/core has a conftest.py too."""

from pathlib import Path

from alembic.config import Config

BACKEND = Path(__file__).resolve().parents[1]


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    # A literal % would be read as interpolation by the ini parser.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config
