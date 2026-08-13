import json
from typing import List, Union
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    ENV: str = "development"
    PORT: int = 8000
    DB_NAME: str = "rag_pdf_db"
    CLEAR_DB_ON_STARTUP: bool = False
    
    # CORS Origins (will be parsed from JSON string to list)
    CORS_ORIGINS: Union[str, List[str]] = '["http://localhost:5173"]'
    
    # LLM Settings
    LLM_PROVIDER: str = "groq"
    GROQ_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "openrouter/free"
    GENERATE_SITUATIONAL_CONTEXT: bool = False
    
    # Ollama Settings (used only when LLM_PROVIDER=ollama)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3"
    
    # MongoDB Settings
    MONGODB_URI: str = "mongodb://localhost:27017"
    ATLAS_SEARCH_INDEX: str = "search_index"
    ATLAS_VECTOR_INDEX: str = "vector_index"
    
    # Web Search Settings
    TAVILY_API_KEY: str = ""

    # Upload Limits
    # Matches the frontend's own self-imposed 10MB cap (DocumentUpload.jsx) -
    # a larger backend limit would let a request that skips the UI push a
    # document big enough to OOM the 512MB Render free instance.
    MAX_UPLOAD_SIZE_MB: int = 10

    # Anonymous session isolation: each visitor's documents/chunks are tagged
    # with a per-browser-tab session id and hard-expire after this many hours.
    SESSION_MAX_AGE_HOURS: int = 5



    # Configuration for loading from .env
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                # Fallback to splitting by comma if not valid JSON
                return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

settings = Settings()
