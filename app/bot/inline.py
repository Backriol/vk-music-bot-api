"""
Inline-режим: песни в любом чате.

Человек пишет «@бот название» в любой переписке и выбирает песню из списка.
- Песни, которые бот уже загружал, уходят сразу (по file_id).
- Остальные: в чат уходит «⏳ Загружаю…», бот скачивает песню, загружает её
  к себе (в личку пользователя, без звука, и сразу удаляет) и превращает
  сообщение в аудио. Telegram не разрешает загружать новые файлы прямо
  в inline-сообщения — только по file_id.

В @BotFather нужно включить: /setinline и /setinlinefeedback.
"""
import logging

from aiogram import F, Router
from aiogram.types import (
    ChosenInlineResult,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedAudio,
    InlineQueryResultsButton,
    InputMediaAudio,
    InputTextMessageContent,
)

from app.bot.accounts import user_tokens
from app.bot.audio import AudioDownloadError
from app.bot.friends import archive
from app.bot.handlers import BOT_ID, _add_history, _cache_file_id, _fmt_duration, _prepare_audio
from app.bot.names import bot_username
from app.core.database import db
from app.services.source import music_service

logger = logging.getLogger(__name__)
router = Router(name="inline")

RESULTS = 20


def _bot_button(username: str) -> InlineKeyboardMarkup:
    # Кнопка нужна ещё и потому, что без клавиатуры Telegram не даёт отредактировать сообщение
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"🎧 Найти музыку в @{username}", url=f"https://t.me/{username}")
    ]])


def _thumb(track: dict) -> str | None:
    url = track.get("cover_url")
    return url.replace("400x400", "100x100") if url else None


async def _started_bot(user_id: int) -> bool:
    u = await db.music_db.users.find_one({"id": user_id}) or {}
    return BOT_ID in (u.get("started_bots") or [])


@router.inline_query()
async def inline_search(query: InlineQuery) -> None:
    text = query.query.strip()
    if text:
        tracks = await music_service.search_tracks(text, limit=RESULTS)
    else:
        # Пустой запрос — свои последние песни, а если их нет — чарт
        tracks = await archive(query.from_user.id, RESULTS)
        if not tracks and hasattr(music_service, "chart_tracks"):
            tracks = await music_service.chart_tracks(RESULTS)

    name = await bot_username(query.bot)
    kb = _bot_button(name)
    ids = [t["id"] for t in tracks]
    cached = {
        d["track_id"]: d["file_id"]
        async for d in db.music_db.tg_audio.find({"track_id": {"$in": ids}, "bot_id": BOT_ID})
    }

    results = []
    for t in tracks:
        if t["id"] in cached:
            results.append(InlineQueryResultCachedAudio(
                id=f"c:{t['id']}", audio_file_id=cached[t["id"]], reply_markup=kb,
            ))
            continue
        dur = f" · {_fmt_duration(t['duration'])}" if t.get("duration") else ""
        results.append(InlineQueryResultArticle(
            id=f"a:{t['id']}",
            title=t["title"] or "Без названия",
            description=f"{t['artist']}{dur}",
            thumbnail_url=_thumb(t),
            input_message_content=InputTextMessageContent(
                message_text=f"⏳ Загружаю: {t['artist']} — {t['title']}"
            ),
            reply_markup=kb,
        ))

    button = None
    if not await _started_bot(query.from_user.id):
        button = InlineQueryResultsButton(text="▶️ Сначала откройте бота — нажмите здесь", start_parameter="inline")
    elif not results:
        button = InlineQueryResultsButton(text="Ничего не нашлось — открыть бота", start_parameter="inline")

    await query.answer(results, cache_time=10, is_personal=True, button=button)


@router.chosen_inline_result(F.result_id.startswith("a:"))
async def inline_chosen(chosen: ChosenInlineResult) -> None:
    track_id = chosen.result_id[2:]
    inline_id = chosen.inline_message_id
    if not inline_id:
        logger.warning("chosen_inline_result без inline_message_id — включите /setinlinefeedback")
        return
    bot = chosen.bot
    user_id = chosen.from_user.id
    name = await bot_username(bot)
    kb = _bot_button(name)

    async def fail(text: str) -> None:
        try:
            await bot.edit_message_text(inline_message_id=inline_id, text=text, reply_markup=kb)
        except Exception:
            pass

    _, download_token = await user_tokens(user_id)
    try:
        prepared = await _prepare_audio(track_id, download_token)
    except AudioDownloadError as e:
        await fail(f"⚠️ Не удалось скачать песню: {e}")
        return
    if not prepared:
        await fail("🚫 Песня недоступна (удалена или ограничена правообладателем).")
        return
    media, meta, cached = prepared

    if cached:
        file_id = media
    else:
        # Загружаем файл к себе, чтобы получить file_id: в личку пользователя без звука и сразу удаляем
        try:
            msg = await bot.send_audio(
                user_id, media, title=meta["title"], performer=meta["artist"],
                duration=meta["duration"], disable_notification=True,
            )
        except Exception as e:
            logger.warning("inline upload to %s failed: %s", user_id, e)
            await fail(f"Чтобы отправлять песни, откройте @{name} и нажмите «Start», затем попробуйте снова.")
            return
        file_id = msg.audio.file_id
        await _cache_file_id(track_id, msg, meta)
        try:
            await bot.delete_message(user_id, msg.message_id)
        except Exception:
            pass

    try:
        await bot.edit_message_media(
            inline_message_id=inline_id, media=InputMediaAudio(media=file_id), reply_markup=kb,
        )
    except Exception as e:
        logger.error("inline edit failed for %s: %s", track_id, e)
        await fail("⚠️ Не удалось отправить песню. Попробуйте ещё раз.")
        return
    await _add_history(user_id, track_id, meta["title"], meta["artist"], meta["duration"], source="inline")
