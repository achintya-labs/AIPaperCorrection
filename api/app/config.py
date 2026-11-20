from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://grader:grader@localhost:5432/grader"
    storage_dir: str = "./storage"
    max_upload_mb: int = 100

    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 720
    # bcrypt work factor. 12 is a sound production default; the test suite sets 4 (the minimum) to run fast.
    bcrypt_rounds: int = Field(12, ge=4, le=31)
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:4321"

    grader_provider: str = "claude_cli"
    claude_model: str = "claude-sonnet-5-5"
    grader_timeout_seconds: int = 600
    grader_max_attempts: int = 2
    claude_bin: str = "claude"
    # --bare needs ANTHROPIC_API_KEY (no OAuth/keychain); turn on in containers.
    claude_bare: bool = False
    # Dedicated `claude` container (GRADER_PROVIDER=claude_runner)
    claude_runner_url: str = ""
    claude_runner_token: str = ""
    claude_runner_concurrency: int = 2
    claude_runner_max_request_mb: int = 300
    render_dpi: int = 150
    worker_concurrency: int = 2
    job_poll_seconds: float = 2.0

    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
