"""
Запуск бота: вместе с FastAPI (start_bot/stop_bot из lifespan)
или отдельным процессом (python -m app.bot).
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

import time
from datetime import datetime

from app.bot.accounts import router as accounts_router
from app.bot.friends import router as friends_router
from app.bot.handlers import router
from app.core.database import db
from app.core.config import settings

logger = logging.getLogger(__name__)

_bot: Bot | None = None
_dp: Dispatcher | None = None
_task: asyncio.Task | None = None


_seen: dict[int, float] = {}


async def remember_user(handler, event, data):
    """Запоминаем имя и @username каждого пользователя — нужно для поиска друзей."""
    u = data.get("event_from_user")
    if u and not u.is_bot and db.music_db is not None and time.monotonic() - _seen.get(u.id, -1e9) > 600:
        _seen[u.id] = time.monotonic()
        await db.music_db.users.update_one(
            {"id": u.id},
            {"$set": {"id": u.id, "first_name": u.first_name or "", "username": u.username or "",
                      "language_code": u.language_code or "en", "last_seen": datetime.utcnow()}},
            upsert=True,
        )
    return await handler(event, data)


def create_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(
        token=settings.bot_token.strip().strip("'\""),
        default=DefaultBotProperties(parse_mode=None),
    )
    dp = Dispatcher()
    dp.update.outer_middleware(remember_user)
    dp.include_router(accounts_router)
    dp.include_router(friends_router)  # раньше общего поиска, который ловит любой текст
    dp.include_router(router)
    return bot, dp


async def setup_bot(bot: Bot) -> None:
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать"),
        BotCommand(command="chart", description="Чарты"),
        BotCommand(command="wave", description="Моя волна — песни подряд"),
        BotCommand(command="recommend", description="Подборка списком"),
        BotCommand(command="friends", description="Друзья и их архивы"),
        BotCommand(command="yandex", description="Подключить свой Яндекс"),
        BotCommand(command="clear", description="Удалить присланные песни"),
        BotCommand(command="help", description="Помощь"),
    ])
    if settings.webapp_url:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="🎧 Плеер", web_app=WebAppInfo(url=settings.webapp_url))
        )


async def _polling(bot: Bot, dp: Dispatcher) -> None:
    try:
        await setup_bot(bot)
        await bot.delete_webhook(drop_pending_updates=False)
        me = await bot.get_me()
        print(f"🤖 Telegram bot @{me.username} started")
        await dp.start_polling(bot, handle_signals=False)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Telegram bot crashed")


async def start_bot() -> None:
    """Запустить бота в фоне внутри уже работающего event loop (FastAPI lifespan)."""
    global _bot, _dp, _task
    _bot, _dp = create_bot()
    _task = asyncio.create_task(_polling(_bot, _dp))


async def stop_bot() -> None:
    global _bot, _dp, _task
    if _dp is not None:
        try:
            await _dp.stop_polling()
        except RuntimeError:
            pass  # polling ещё не стартовал
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
    if _bot is not None:
        await _bot.session.close()
        print("🛑 Telegram bot stopped")
    _bot = _dp = _task = None


async def run_standalone() -> None:
    from app.core.database import close_mongo_connection, connect_to_mongo

    logging.basicConfig(level=logging.INFO)
    await connect_to_mongo()
    bot, dp = create_bot()
    try:
        await setup_bot(bot)
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await close_mongo_connection()
