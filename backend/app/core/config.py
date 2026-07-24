"""
Application configuration.

Centralizes all environment-driven settings behind a single, typed,
validated Settings object. Using pydantic-settings instead of raw
`os.getenv()` calls scattered across the codebase gives us:

  1. Fail-fast startup: a missing/malformed required env var raises a
     validation error immediately when the app boots, not deep inside
     a request handler at 2am in production.
  2. Type safety: every setting has a declared type, so `settings.debug`
     is an actual bool, not the string "true".
  3. A single source of truth that every module (auth, DB, AI clients,
     speech, etc.) imports from, instead of each module parsing env
     vars independently.

The `Settings` object is cached via `lru_cache` so it's constructed
once per process and reused everywhere via `get_settings()`.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Strongly-typed application settings, populated from environment
    variables (or a local `.env` file during development).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App metadata ---
    app_name: str = Field(default="HireMind AI API")
    app_version: str = Field(default="0.1.0")
    environment: Literal["development", "staging", "production"] = Field(
        default="development"
    )
    debug: bool = Field(default=True)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO"
    )

    # --- API ---
    api_v1_prefix: str = Field(default="/api/v1")
    cors_origins: str = Field(default="http://localhost:5173")

    # --- MongoDB Atlas ---
    mongodb_uri: str = Field(...)
    mongodb_db_name: str = Field(default="hiremind_ai")

    # --- JWT (consumed starting Module 2) ---
    jwt_secret_key: str = Field(default="change-me-to-a-long-random-secret")
    jwt_algorithm: str = Field(default="HS256")
    access_token_expire_minutes: int = Field(default=30)
    refresh_token_expire_days: int = Field(default=7)

    # --- Gemini API (consumed starting Module 5) ---
    gemini_api_key: str = Field(default="")

    # --- Deepgram (consumed starting Module 6) ---
    deepgram_api_key: str = Field(default="")

    # --- Judge0 (consumed starting Module 9) ---
    judge0_api_url: str = Field(default="https://judge0-ce.p.rapidapi.com")
    judge0_api_key: str = Field(default="")

    @field_validator("environment")
    @classmethod
    def _validate_environment(cls, value: str) -> str:
        """Guard against typos like 'prod' vs 'production' at startup."""
        allowed = {"development", "staging", "production"}
        if value not in allowed:
            raise ValueError(f"environment must be one of {allowed}, got '{value}'")
        return value

    @property
    def cors_origins_list(self) -> list[str]:
        """
        Parses the comma-separated CORS_ORIGINS env var into a clean list.
        Kept as a derived property (not a stored field) so the raw env
        string stays human-editable while the app consumes a proper list.
        """
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """
    Returns a cached Settings singleton.

    `lru_cache` ensures the environment is parsed and validated exactly
    once per process, and every module that calls `get_settings()`
    receives the same instance — cheap, consistent, and easy to
    override in tests via dependency injection.
    """
    return Settings()
