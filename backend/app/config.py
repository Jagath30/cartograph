"""Runtime configuration, read from the environment (IR-15).

Nothing about a specific database is compiled in: both connection URLs
arrive as environment variables, and the two are separate values so that
no component can accidentally hold the wrong one (DD-02, rule 2).
"""

from dataclasses import dataclass
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_database_url: str
    warehouse_database_url: str
    redis_url: str

    # The overlay file for the warehouse behind warehouse_database_url
    # (DD-16). Optional: a warehouse whose catalog declares everything, and
    # whose names read like English, needs none.
    warehouse_overlay_path: str | None = None

    # The embedding provider's key (IR-14). Optional: a clean clone has
    # none and still starts. A SecretStr, so that printing the settings, or
    # an error that carries them, shows asterisks and never the value
    # (NFR-09).
    openai_api_key: SecretStr | None = None
    embedding_model: str = "text-embedding-3-small"
    # US dollars per million input tokens, for the cost logged with every
    # call (NFR-14). Confirmed by the owner, 8 October 2026.
    embedding_price_per_million: float = 0.02

    # The model that writes SQL (IR-13). Chosen at step 7; the reasons are
    # in CHECKPOINTS.md. No dated snapshot of it exists, so every call also
    # records the model ID the provider says it used.
    sql_model: str = "gpt-6-luna"
    # A temperature is accepted only at this effort (the provider's GPT-6
    # guide), and Design section 12's method is temperature 0.
    sql_model_reasoning_effort: str = "none"
    sql_model_temperature: float = 0.0
    # Every call sets a maximum output length. A reply cut off by it is
    # recorded as that and is not retried.
    sql_model_max_output_tokens: int = 800

    # Comma-separated rather than a list: pydantic-settings expects JSON for
    # complex types, and a plain string with an explicit split is one less
    # thing to get wrong in a deployment environment variable.
    frontend_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]


@dataclass(frozen=True)
class ModelPrice:
    """US dollars per million tokens, and the day they were read."""

    input: float
    cached_input: float
    output: float
    as_of: str


# The price table the cost logged with every model call is worked from
# (NFR-14). Read from the provider's pricing page on the date given. A
# model that is not here cannot be called: a cost is never made up.
MODEL_PRICES: dict[str, ModelPrice] = {
    "gpt-6-luna": ModelPrice(input=0.10, cached_input=0.01, output=0.50, as_of="2026-10-09"),
    "gpt-5.6-luna": ModelPrice(input=0.20, cached_input=0.02, output=1.20, as_of="2026-10-09"),
}


@lru_cache
def get_settings() -> Settings:
    """Read configuration on first use, not at import time.

    Constructing Settings at module scope would make `import app.main`
    fail without a full environment -- which would put a database
    requirement on tests that have no business needing one.
    """
    return Settings()
