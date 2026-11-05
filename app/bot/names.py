"""Имя бота, которое показываем людям (у бота может быть несколько @username)."""
from app.core.config import settings


async def bot_username(bot) -> str:
    """BOT_USERNAME из .env, иначе основное имя бота из Telegram."""
    name = (settings.bot_username or "").strip().lstrip("@")
    if name:
        return name
    return (await bot.me()).username
