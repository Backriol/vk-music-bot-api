"""
Запуск бота: вместе с FastAPI (start_bot/stop_bot из lifespan)
или отдельным процессом (python -m app.bot).
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from app.bot.handlers import router
from app.core.config import settings

logger = logging.getLogger(__name__)

_bot: Bot | None = None
_dp: Dispatcher | None = None
_task: asyncio.Task | None = None


def create_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(
        token=settings.bot_token.strip().strip("'\""),
        default=DefaultBotProperties(parse_mode=None),
    )
    dp = Dispatcher()
    dp.include_router(router)
    return bot, dp


async def setup_bot(bot: Bot) -> None:
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать"),
        BotCommand(command="chart", description="Чарты"),
        BotCommand(command="wave", description="Моя волна — песни подряд"),
        BotCommand(command="recommend", description="Подборка списком"),
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
