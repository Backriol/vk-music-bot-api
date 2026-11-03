"""
Хендлеры Telegram-бота: поиск, отправка MP3, рекомендации.
Используют те же сервисы и коллекции MongoDB, что и API.
"""
import logging
import re
from collections import OrderedDict
from datetime import datetime

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from app.bot.audio import AudioDownloadError, fetch_mp3
from app.core.config import settings
from app.core.database import db
from app.services.source import music_service

logger = logging.getLogger(__name__)
router = Router(name="music")

PAGE_SIZE = 8
SEARCH_LIMIT = 40
TRACK_ID_RE = re.compile(r"^-?\d+_\d+$")

# Результаты поиска по сообщению: (chat_id, message_id) -> list[track]
_results: "OrderedDict[tuple[int, int], list[dict]]" = OrderedDict()
_RESULTS_MAX = 2000


def _remember(chat_id: int, message_id: int, tracks: list[dict]) -> None:
    _results[(chat_id, message_id)] = tracks
    _results.move_to_end((chat_id, message_id))
    while len(_results) > _RESULTS_MAX:
        _results.popitem(last=False)


def _fmt_duration(seconds: int | None) -> str:
    seconds = seconds or 0
    return f"{seconds // 60}:{seconds % 60:02d}"


def _results_keyboard(tracks: list[dict], page: int) -> InlineKeyboardMarkup:
    pages = max(1, (len(tracks) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = tracks[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    rows = []
    for t in chunk:
        text = f"{t['artist']} — {t['title']} ({_fmt_duration(t['duration'])})"
        rows.append([InlineKeyboardButton(text=text[:64], callback_data=f"t:{t['id']}")])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"p:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"p:{page + 1}"))
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _main_keyboard() -> ReplyKeyboardMarkup | None:
    if not settings.webapp_url:
        return None
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🎧 Открыть плеер", web_app=WebAppInfo(url=settings.webapp_url))]],
        resize_keyboard=True,
    )


async def _save_user(message: Message) -> None:
    u = message.from_user
    if not u:
        return
    await db.music_db.users.update_one(
        {"id": u.id},
        {"$set": {
            "id": u.id,
            "first_name": u.first_name or "",
            "username": u.username or "",
            "language_code": u.language_code or "en",
            "last_login": datetime.utcnow(),
        }},
        upsert=True,
    )


async def _send_results(message: Message, tracks: list[dict], header: str) -> None:
    if not tracks:
        await message.answer("😔 Ничего не нашлось. Попробуйте другой запрос.")
        return
    sent = await message.answer(header, reply_markup=_results_keyboard(tracks, 0))
    _remember(sent.chat.id, sent.message_id, tracks)


# ---------- Команды ----------

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await _save_user(message)
    text = (
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "Я ищу музыку и присылаю MP3.\n"
        "Просто напишите название песни или исполнителя.\n\n"
        "/recommend — рекомендации по вашей истории\n"
        "/help — помощь"
    )
    await message.answer(text, reply_markup=_main_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "🔍 Отправьте текст — например, «Макс Корж» или «Жить в кайф».\n"
        "Нажмите на трек в списке — я пришлю его файлом.\n"
        "/recommend — подборка на основе того, что вы слушали."
    )


@router.message(Command("recommend"))
async def cmd_recommend(message: Message) -> None:
    last = await db.music_db.history.find_one(
        {"user_id": message.from_user.id}, sort=[("listened_at", -1)]
    )
    if last and last.get("artist") and last["artist"] != "Unknown":
        tracks = await music_service.search_tracks(last["artist"], limit=SEARCH_LIMIT)
        tracks = [t for t in tracks if t["id"] != last.get("track_id")]
        header = f"🎯 Похожее на «{last['artist']}»:"
    else:
        tracks = await music_service.search_tracks("Top 100", limit=SEARCH_LIMIT)
        header = "🔥 Популярное:"
    await _send_results(message, tracks, header)


# ---------- Поиск ----------

@router.message(F.text & ~F.text.startswith("/"))
async def search(message: Message) -> None:
    query = message.text.strip()
    if not query:
        return
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    tracks = await music_service.search_tracks(query, limit=SEARCH_LIMIT)
    await _send_results(message, tracks, f"🔍 Результаты по запросу «{query[:100]}»:")


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("p:"))
async def paginate(callback: CallbackQuery) -> None:
    key = (callback.message.chat.id, callback.message.message_id)
    tracks = _results.get(key)
    if not tracks:
        await callback.answer("Результаты устарели, повторите поиск", show_alert=True)
        return
    page = int(callback.data.split(":", 1)[1])
    await callback.message.edit_reply_markup(reply_markup=_results_keyboard(tracks, page))
    await callback.answer()


# ---------- Отправка трека ----------

@router.callback_query(F.data.startswith("t:"))
async def send_track(callback: CallbackQuery) -> None:
    track_id = callback.data.split(":", 1)[1]
    if not TRACK_ID_RE.match(track_id):
        await callback.answer("Некорректный трек", show_alert=True)
        return

    await callback.answer("⏳ Загружаю…")
    chat_id = callback.message.chat.id
    bot = callback.bot
    await bot.send_chat_action(chat_id, ChatAction.UPLOAD_VOICE)

    # 1. Telegram уже хранит этот файл — отправляем по file_id мгновенно
    cached = await db.music_db.tg_audio.find_one({"track_id": track_id})
    if cached:
        try:
            await bot.send_audio(chat_id, cached["file_id"])
            await _add_history(callback.from_user.id, track_id, cached["title"], cached["artist"])
            return
        except Exception as e:  # file_id мог стать невалидным
            logger.warning("cached file_id failed for %s: %s", track_id, e)
            await db.music_db.tg_audio.delete_one({"track_id": track_id})

    # 2. Получаем ссылку из VK и скачиваем
    song = await music_service.get_audio_url(track_id)
    if not song or not song.url:
        await bot.send_message(chat_id, "🚫 Трек недоступен (удалён или ограничен правообладателем).")
        return

    try:
        data = await fetch_mp3(song.url)
    except AudioDownloadError as e:
        logger.error("download failed for %s: %s", track_id, e)
        await bot.send_message(chat_id, f"⚠️ Не удалось скачать трек: {e}")
        return

    filename = f"{song.artist} - {song.title}"[:120] + ".mp3"
    msg = await bot.send_audio(
        chat_id,
        BufferedInputFile(data, filename=filename),
        title=song.title,
        performer=song.artist,
        duration=int(song.duration or 0),
    )

    if msg.audio:
        await db.music_db.tg_audio.update_one(
            {"track_id": track_id},
            {"$set": {
                "track_id": track_id,
                "file_id": msg.audio.file_id,
                "title": song.title,
                "artist": song.artist,
                "saved_at": datetime.utcnow(),
            }},
            upsert=True,
        )
    await _add_history(callback.from_user.id, track_id, song.title, song.artist)


async def _add_history(user_id: int, track_id: str, title: str, artist: str) -> None:
    # Тот же формат, что у POST /api/auth/history — рекомендации общие для бота и Mini App
    await db.music_db.history.insert_one({
        "user_id": user_id,
        "track_id": track_id,
        "title": title,
        "artist": artist or "Unknown",
        "listened_at": datetime.utcnow(),
        "source": "bot",
    })
