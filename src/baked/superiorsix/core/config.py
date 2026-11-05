from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEVELOPMENT_SECRET = "insecure-development-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SUPERIORSIX_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "baked-superiorsix"
    environment: str = "development"
    database_url: str = "sqlite+aiosqlite:///./baked_superiorsix.db"
    jwt_secret_key: SecretStr = SecretStr(INSECURE_DEVELOPMENT_SECRET)
    jwt_algorithm: str = "HS256"
    jwt_key_id: str | None = None
    jwt_previous_secret_keys: list[SecretStr] = Field(default_factory=list)
    jwt_jwks_path: Path | None = None
    jwt_audience: str = "superiorsix"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    auth_token_url: str = "http://127.0.0.1:8010/v1/auth/token"
    # baked-user base URL for roster / display-name resolution by auth sub.
    user_service_url: str = "http://127.0.0.1:8001"
    user_app_name: str = "superiorsix"
    # Server-side auth-admin credentials for membership grants on invite accept.
    # Local-dev typically reuses AUTH_BOOTSTRAP_* (ops) with audience=auth.
    auth_admin_username: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SUPERIORSIX_AUTH_ADMIN_USERNAME",
            "AUTH_BOOTSTRAP_USERNAME",
        ),
    )
    auth_admin_password: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SUPERIORSIX_AUTH_ADMIN_PASSWORD",
            "AUTH_BOOTSTRAP_PASSWORD",
        ),
    )
    # Ingest API keys — prefer unprefixed deploy names; SUPERIORSIX_* aliases also work.
    football_data_api_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "FOOTBALL_DATA_API_TOKEN",
            "SUPERIORSIX_FOOTBALL_DATA_API_TOKEN",
        ),
    )
    goal_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GOAL_API_KEY",
            "SUPERIORSIX_GOAL_API_KEY",
        ),
    )

    @field_validator("environment")
    @classmethod
    def normalize_environment(cls, value: str) -> str:
        return value.lower()

    @field_validator("jwt_algorithm")
    @classmethod
    def normalize_algorithm(cls, value: str) -> str:
        algorithm = value.strip().upper()
        if algorithm == "NONE":
            raise ValueError("The 'none' JWT algorithm is not allowed.")
        return algorithm

    @field_validator("jwt_key_id")
    @classmethod
    def empty_key_id_as_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("jwt_jwks_path", mode="before")
    @classmethod
    def empty_jwks_path_as_none(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @field_validator("jwt_audience")
    @classmethod
    def normalize_jwt_audience(cls, value: str) -> str:
        audience = value.strip().lower()
        if not audience:
            raise ValueError("SUPERIORSIX_JWT_AUDIENCE must be a non-empty application slug.")
        return audience

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    using_default_secret = settings.jwt_secret_key.get_secret_value() == INSECURE_DEVELOPMENT_SECRET
    if settings.is_production and settings.jwt_jwks_path is None and using_default_secret:
        raise ValueError(
            "SUPERIORSIX_JWT_SECRET_KEY or SUPERIORSIX_JWT_JWKS_PATH must be set to a "
            "strong secret in production."
        )
    return settings
