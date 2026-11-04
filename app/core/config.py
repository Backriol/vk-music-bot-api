from typing import Optional
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Telegram Bot
    bot_token: str
    
    # URL Telegram Mini App (https). Если задан — бот покажет кнопку открытия плеера
    webapp_url: Optional[str] = None
    # Запускать бота вместе с API (в lifespan FastAPI).
    # При uvicorn --workers > 1 выключите и запускайте бота отдельно: python -m app.bot
    run_bot: bool = True

    # Источник музыки: "yandex" или "vk". Пусто — yandex, если задан YANDEX_TOKEN
    music_source: str = ""

    # Яндекс Музыка (OAuth-токен; для полных треков нужна подписка Плюс)
    yandex_token: str = ""

    # VK API
    vk_token: str = ""
    vk_user_agent: str = "VKAndroidApp/5.52-4543"
    
    # MongoDB. Пусто — временная база в памяти (история сбрасывается при перезапуске)
    mongo_url: str = ""
    db_name: str = "music_bot_db"
    # Файл для данных, если MongoDB не подключена
    data_file: str = "bot_data.json"
    
    # Application
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = False

    # SSL
    ssl_keyfile: Optional[str] = None
    ssl_certfile: Optional[str] = None

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"

settings = Settings()
