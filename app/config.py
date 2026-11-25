from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    CLUB_ID: str = "682077"
    CLUB_NAME: str = "MVL ES"
    PLATFORM: str = "common-gen5"
    DB_PATH: str = "data/fc27_clubs.db"
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000
    INGEST_INTERVAL_SECONDS: int = 300
    INGEST_JITTER_SECONDS: int = 30
    BACKUP_DIR: str = "data/backups"
    BACKUP_INTERVAL_SECONDS: int = 21600  # 6 hours
    BACKUP_KEEP: int = 30
    EA_TIMEOUT_SECONDS: float = 20.0
    EA_MAX_ATTEMPTS: int = 4
    EA_MIN_REQUEST_INTERVAL_S: float = 1.0
    CIRCUIT_BREAKER_THRESHOLD: int = 5
    CIRCUIT_BREAKER_BACKOFF_SECONDS: int = 1800
    LOG_LEVEL: str = "INFO"
    PARSER_VERSION: str = "parser-3.0"

    # Revisão de resultados / evidências (print da partida)
    EVIDENCE_DIR: str = "data/evidence"
    EVIDENCE_MAX_BYTES: int = 10 * 1024 * 1024
    # Extrator multimodal (API compatível com OpenAI). Deixe a chave vazia
    # para usar somente OCR local (pytesseract) ou preenchimento manual.
    VISION_API_URL: str = "https://api.openai.com/v1/chat/completions"
    VISION_API_KEY: str = ""
    VISION_MODEL: str = "gpt-4o-mini"
    VISION_TIMEOUT_SECONDS: float = 30.0


settings = Settings()
