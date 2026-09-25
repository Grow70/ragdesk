"""Environment-only application configuration."""

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", frozen=True)

    database_url: SecretStr
    model_provider: str
    model_name: str
    model_api_key: SecretStr | None = None

    @field_validator("database_url")
    @classmethod
    def database_url_is_not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("model_provider", "model_name")
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
