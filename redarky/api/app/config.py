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

    EMBEDDING_MODEL_ENABLED: bool = False
    EMBEDDING_MODEL_NAME: str = "all-MiniLM-L6-v2"
    
    LLM_ENABLED: bool = False
    LLM_API_URL: str = "https://api.openai.com/v1/chat/completions"
    LLM_API_KEY: str = ""
    LLM_MODEL_NAME: str = "gpt-4o-mini"

    # Celery beat sechedule
    SCRAPER_INTERVAL_MINUTES: int = 30
    CLEANUP_INTERVAL_HOURS: int = 24

    # Ingestion webhook shared secret
    INGESTION_WEBHOOK_SECRET: str = "change-it-in-production"

    # Apify
    APIFY_API_TOKEN: str
    APIFY_WEBHOOK_SECRET: str
    APIFY_REDDIT_ACTOR_ID: str = "default_actor_id_here"     
    APIFY_WEBHOOK_URL: str = "http://localhost:8000/ingestion/reddit"


    model_config = SettingsConfigDict(
        env_file="../.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()