from app.config import Settings


def test_cors_origins_parses_json_list_string():
    settings = Settings(CORS_ORIGINS='["http://localhost:5173", "https://example.com"]')
    assert settings.CORS_ORIGINS == ["http://localhost:5173", "https://example.com"]


def test_cors_origins_falls_back_to_comma_split_on_invalid_json():
    settings = Settings(CORS_ORIGINS="http://localhost:5173, https://example.com")
    assert settings.CORS_ORIGINS == ["http://localhost:5173", "https://example.com"]


def test_cors_origins_passthrough_when_already_a_list():
    settings = Settings(CORS_ORIGINS=["http://localhost:5173"])
    assert settings.CORS_ORIGINS == ["http://localhost:5173"]


def test_default_settings_load_without_env_file():
    settings = Settings(_env_file=None)
    assert settings.LLM_PROVIDER == "groq"
    assert settings.PORT == 8000
    assert settings.MAX_UPLOAD_SIZE_MB == 10
