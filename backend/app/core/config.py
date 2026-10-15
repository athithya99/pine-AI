from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "Pinesphere AI Commerce"
    ENVIRONMENT: str = "development"  # development | staging | production
    DEBUG: bool = False

    DATABASE_URL: str = "postgresql+psycopg://pinesphere:pinesphere@localhost:5432/pinesphere"
    REDIS_URL: str = "redis://localhost:6379/0"

    SECRET_KEY: str = "change-me-in-production"
    ACCESS_TOKEN_MINUTES: int = 15
    REFRESH_TOKEN_DAYS: int = 7

    OTP_LENGTH: int = 6
    OTP_TTL_MINUTES: int = 5
    OTP_MAX_REQUESTS: int = 5          # per number
    OTP_WINDOW_MINUTES: int = 15       # in this window (FR-M1-07)

    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    MAX_UPLOAD_ROWS: int = 10000

    # WhatsApp Cloud API
    WHATSAPP_API_VERSION: str = "v23.0"
    WHATSAPP_GRAPH_URL: str = "https://graph.facebook.com"
    # token Meta sends back on webhook verification (GET); set the same value in the Meta app
    WHATSAPP_VERIFY_TOKEN: str = "pinesphere-verify"
    # App secret of the Meta app (used to verify webhook signatures). A tenant can also store its own.
    WHATSAPP_APP_SECRET: str = ""
    WHATSAPP_WINDOW_HOURS: int = 24
    ENABLE_SIMULATOR: bool = True  # turn off in production

    MEDIA_ROOT: str = "media"

    # AI order capture (M5). "mock" is a built-in rule-based parser for development; no key needed.
    AI_PROVIDER: str = "mock"          # mock | anthropic | openai
    AI_API_KEY: str = ""
    AI_MODEL_FAST: str = ""            # text messages; blank = provider default
    AI_MODEL_SMART: str = ""           # photos of order lists; blank = provider default
    AI_TIMEOUT_SECONDS: float = 30
    ANTHROPIC_API_URL: str = "https://api.anthropic.com/v1"
    OPENAI_API_URL: str = "https://api.openai.com/v1"
    # speech-to-text for voice notes
    STT_PROVIDER: str = "none"         # none | openai
    STT_API_KEY: str = ""              # blank = reuse AI_API_KEY when AI_PROVIDER=openai
    STT_MODEL: str = "gpt-4o-mini-transcribe"

    # run Celery tasks inline (no Redis/worker needed) - dev and tests
    CELERY_EAGER: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
