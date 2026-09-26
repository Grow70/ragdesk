"""Environment-only application configuration."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", frozen=True)

    database_url: SecretStr
    rerank_enabled: bool = False
    cohere_api_key: SecretStr | None = None
    rerank_model: Literal["rerank-v3.5"] = "rerank-v3.5"
    rerank_connect_timeout_seconds: float = Field(
        default=3.0, gt=0, allow_inf_nan=False
    )
    rerank_read_timeout_seconds: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    openai_api_key: SecretStr | None = None
    chat_model: str = "gpt-4.1-mini-2025-04-14"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=1, le=1536)
    retrieval_embedding_backend: Literal["openai", "fake"] = "openai"
    model_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    model_read_timeout_seconds: float = Field(default=30.0, gt=0)
    model_max_attempts: int = Field(default=3, ge=1, le=3)
    jwt_secret: SecretStr
    access_token_ttl_minutes: int = Field(default=30, ge=1, le=1440)
    upload_storage_dir: Path = Path(__file__).resolve().parents[1] / "var" / "uploads"

    @field_validator("database_url")
    @classmethod
    def database_url_is_not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("jwt_secret")
    @classmethod
    def jwt_secret_is_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode("utf-8")) < 32:
            raise ValueError("must have at least 32 bytes")
        return value

    @field_validator("chat_model", "embedding_model")
    @classmethod
    def model_setting_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]).upper() for error in exc.errors()})
        raise RuntimeError(
            f"Missing or invalid configuration: {', '.join(fields)}"
        ) from None
