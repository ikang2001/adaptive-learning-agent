from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["local", "test", "staging", "production"] = "local"
    app_name: str = "千人千案 Agent Harness"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/learning_agent"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: SecretStr = Field(default=SecretStr("local-jwt-secret-change-me-32chars"))
    jwt_issuer: str = "learning-agent"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    otp_hmac_secret: SecretStr = Field(default=SecretStr("local-otp-secret-change-me-32chars"))
    pii_hmac_secret: SecretStr = Field(default=SecretStr("local-pii-secret-change-me-32chars"))
    pii_encryption_key: SecretStr = Field(default=SecretStr(""))

    sms_provider: Literal["fixed", "unconfigured"] = "fixed"
    fixed_sms_code: SecretStr = Field(default=SecretStr("246810"))

    qwen_api_key: SecretStr = Field(default=SecretStr(""))
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_plus_model: str = "qwen3.7-plus-2026-05-26"
    qwen_flash_model: str = "qwen3.7-flash-2026-07-15"
    qwen_embedding_model: str = "qwen3.7-text-embedding"
    use_fake_model: bool = True

    agent_max_steps: int = 8
    agent_max_model_calls: int = 10
    agent_max_tool_calls: int = 12
    agent_max_input_tokens: int = 64_000
    agent_max_output_tokens: int = 8_192
    agent_max_total_tokens: int = 72_192
    agent_max_runtime_seconds: int = 600
    agent_max_repair_calls: int = 1
    agent_model_max_output_tokens: int = 2_048
    agent_lease_seconds: int = 45
    agent_heartbeat_seconds: int = 10
    agent_shadow_enabled: bool = False
    agent_shadow_model: str | None = None
    agent_shadow_prompt_version: str = "diagnosis_shadow_v1"
    agent_tool_feature_flags: list[str] = Field(default_factory=list)

    job_max_attempts: int = 3
    job_retry_base_seconds: float = 1.0
    job_retry_max_seconds: float = 60.0
    job_reconciliation_seconds: int = 90

    resource_storage_root: str = "D:/CodexTemp/qianrenqianan/resources"
    resource_max_document_bytes: int = 100 * 1024 * 1024
    resource_max_image_bytes: int = 10 * 1024 * 1024
    resource_max_image_count: int = 50
    planning_timezone: str = "Asia/Shanghai"

    allowed_origins: list[str] = Field(default_factory=list)
    otel_exporter_otlp_endpoint: str | None = None

    @model_validator(mode="after")
    def validate_production_safety(self) -> Settings:
        if self.agent_heartbeat_seconds >= self.agent_lease_seconds:
            raise ValueError("AGENT_HEARTBEAT_SECONDS must be less than AGENT_LEASE_SECONDS")
        if self.app_env != "production":
            return self
        if self.sms_provider in {"fixed", "unconfigured"}:
            raise ValueError("production requires a real SMS provider adapter")
        if self.use_fake_model:
            raise ValueError("production cannot use the fake model gateway")
        if not self.qwen_api_key.get_secret_value():
            raise ValueError("production requires QWEN_API_KEY")
        if not self.pii_encryption_key.get_secret_value():
            raise ValueError("production requires PII_ENCRYPTION_KEY")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
