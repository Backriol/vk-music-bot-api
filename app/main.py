from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from app.core.database import connect_to_mongo, close_mongo_connection
from app.core.config import settings
from app.bot import start_bot, stop_bot
from app.routers import auth, music
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: подключаемся к БД
    await connect_to_mongo()
    # Запускаем Telegram-бота в том же процессе
    if settings.run_bot:
        await start_bot()
    yield
    # Shutdown: останавливаем бота и отключаемся
    if settings.run_bot:
        await stop_bot()
    await close_mongo_connection()

# Описания тегов с эмодзи для красоты
tags_metadata = [
    {
        "name": "🎵 Music",
        "description": "Search, download, and get recommendations for music tracks from VK.",
    },
    {
        "name": "🔐 Authentication & User",
        "description": "User authentication via Telegram Mini App InitData and listening history tracking.",
    },
]

app = FastAPI(
    title="🎧 VK Music Bot API",
    description="""
## Telegram Mini App Backend для музыки из ВКонтакте

Этот API предоставляет полный функционал для поиска, скачивания и рекомендаций музыки из VK.

### Основные возможности:
* 🔍 **Поиск треков** с обложками альбомов
* ⬇️ **Скачивание MP3** напрямую с серверов VK
* 🎯 **Персональные рекомендации** на основе истории прослушиваний
* 🔐 **Безопасная авторизация** через Telegram WebApp InitData
* 📊 **История прослушиваний** для каждого пользователя

### Технологии:
- FastAPI + Motor (async MongoDB)
- VK Audio API
- Telegram Mini Apps Authentication
    """,
    version="1.0.0",
    contact={
        "name": "Music Bot Support",
        "url": "https://t.me/traftret",
    },
    license_info={
        "name": "MIT",
    },
    openapi_tags=tags_metadata,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

from fastapi.middleware.cors import CORSMiddleware

# Подключаем роутеры
app.include_router(auth.router, prefix="/api") 
app.include_router(music.router, prefix="/api")

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Разрешаем все домены (важно для Telegram Mini App)
    allow_credentials=True,
    allow_methods=["*"],  # Разрешаем все HTTP методы (GET, POST и т.д.)
    allow_headers=["*"],  # Разрешаем все заголовки
)

@app.get("/", tags=["System"])
async def root():
    """
    Root endpoint - API health check
    """
    return {
        "status": "online",
        "message": "VK Music Bot API is running", 
        "docs": "/docs",
        "redoc": "/redoc",
        "version": "1.0.0"
    }

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app", 
        host=settings.app_host, 
        port=settings.app_port, 
        reload=settings.debug,
        ssl_keyfile=settings.ssl_keyfile,
        ssl_certfile=settings.ssl_certfile
    )
