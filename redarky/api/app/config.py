from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    ENVIRONMENT: str = "dev"
    GO_SCRAPER_URL: str = "http://localhost:8081/scrape"
    REDIS_URL: str = "redis://localhost:6379/0"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 30
    LOCAL_S3_BASE_PATH: str = "../redarky_data_s3"

    # AI services (Stage 3)
    OPENAI_API_KEY: str = ""

    # Embedding provider for Stage-2 semantic matching:
    #   "auto"   (default) — OpenAI when OPENAI_API_KEY is set, else local sentence-transformers when enabled, else lexical
    #   "openai" | "local" | "none"
    EMBEDDING_PROVIDER: str = "auto"
    EMBEDDING_MODEL_ENABLED: bool = False          # enables the local provider
    EMBEDDING_MODEL_NAME: str = "all-MiniLM-L6-v2"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # LLM (Stage 3 lead agent). LLM_ENABLED=None means "auto": enabled when
    # any API key is present. Set LLM_ENABLED=false to force the
    # no-LLM auto-promotion path.
    LLM_ENABLED: Optional[bool] = None
    LLM_API_URL: str = "https://api.openai.com/v1/chat/completions"
    LLM_API_KEY: str = ""
    LLM_MODEL_NAME: str = "gpt-4o-mini"

    # Celery beat schedule
    SCRAPER_INTERVAL_MINUTES: int = 30
    LLM_INTERNAL_MINUTES: int = 10
    CLEANUP_INTERVAL_HOURS: int = 24

    # Ingestion webhook shared secret
    INGESTION_WEBHOOK_SECRET: str = "change-it-in-production"

    # Apify
    APIFY_API_TOKEN: str
    APIFY_WEBHOOK_SECRET: str
    APIFY_REDDIT_ACTOR_ID: str = "default_actor_id"
    APIFY_WEBHOOK_URL: str = "http://localhost:8000/ingestion/reddit"

    # ALLOWED ORIGINS (stored as raw string from env, fallback defaults provided)
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Feedback email verification
    FEEDBACK_CODE_EXPIRE_MINUTES: int = 30

    # SMTP (optional — used for feedback verification codes + email integrations)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "Redarky <no-reply@redarky.com>"
    SMTP_USE_TLS: bool = True

    @property
    def llm_api_key(self) -> str:
        """LLM key resolution: explicit LLM_API_KEY wins, else OPENAI_API_KEY."""
        return self.LLM_API_KEY or self.OPENAI_API_KEY

    @property
    def llm_enabled(self) -> bool:
        """True when the Stage-3 lead agent may call an LLM."""
        if self.LLM_ENABLED is not None:
            return self.LLM_ENABLED
        return bool(self.llm_api_key)

    @property
    def cors_origins(self) -> list[str]:
        """Parses ALLOWED_ORIGINS into a clean list of strings for CORSMiddleware."""
        if isinstance(self.ALLOWED_ORIGINS, list):
            return self.ALLOWED_ORIGINS
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    model_config = SettingsConfigDict(
        env_file="../.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

