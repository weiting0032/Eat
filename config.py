import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default

    try:
        return int(value)
    except ValueError:
        raise ValueError(f"環境變數 {name} 必須為整數，目前值為: {value}")


@dataclass
class Settings:
    telegram_bot_token: str
    ai_provider: str

    gemini_api_key: str
    gemini_model: str

    openai_api_key: str
    openai_model: str

    db_path: str
    upload_dir: str
    timezone: str
    default_daily_calorie_target: int


settings = Settings(
    telegram_bot_token=_get_env("TELEGRAM_BOT_TOKEN"),
    ai_provider=_get_env("AI_PROVIDER", "gemini").lower(),
    gemini_api_key=_get_env("GEMINI_API_KEY"),
    gemini_model=_get_env("GEMINI_MODEL", "gemini-1.5-flash"),
    openai_api_key=_get_env("OPENAI_API_KEY"),
    openai_model=_get_env("OPENAI_MODEL", "gpt-4o-mini"),
    db_path=_get_env("DB_PATH", "data/app.db"),
    upload_dir=_get_env("UPLOAD_DIR", "uploads"),
    timezone=_get_env("TIMEZONE", "Asia/Taipei"),
    default_daily_calorie_target=_get_int_env("DEFAULT_DAILY_CALORIE_TARGET", 1232),
)


def validate_settings():
    errors = []

    if not settings.telegram_bot_token:
        errors.append("缺少 TELEGRAM_BOT_TOKEN")

    if settings.ai_provider not in ("gemini", "openai"):
        errors.append("AI_PROVIDER 只能是 gemini 或 openai")

    if settings.ai_provider == "gemini" and not settings.gemini_api_key:
        errors.append("AI_PROVIDER=gemini 時，必須設定 GEMINI_API_KEY")

    if settings.ai_provider == "openai" and not settings.openai_api_key:
        errors.append("AI_PROVIDER=openai 時，必須設定 OPENAI_API_KEY")

    db_dir = os.path.dirname(settings.db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    os.makedirs(settings.upload_dir, exist_ok=True)

    if errors:
        raise ValueError("設定錯誤：\n- " + "\n- ".join(errors))
