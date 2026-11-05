"""
Хендлеры Telegram-бота: поиск, отправка MP3, рекомендации.
Используют те же сервисы и коллекции MongoDB, что и API.
"""
import asyncio
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
    InputMediaAudio,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from app.bot.accounts import connect_button, user_tokens, yandex_enabled
from app.bot.audio import AudioDownloadError, fetch_mp3
from app.core.config import settings
from app.core.database import db
from app.services.source import music_service

logger = logging.getLogger(__name__)
router = Router(name="music")

# file_id в Telegram действует только для бота, который загрузил файл,
# поэтому кеш песен хранится отдельно для каждого бота
BOT_ID = settings.bot_token.strip().strip("'\"").split(":")[0]

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


def _songs(n: int) -> str:
    """1 песня, 3 песни, 10 песен."""
    if n % 10 == 1 and n % 100 != 11:
        word = "песня"
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = "песни"
    else:
        word = "песен"
    return f"{n} {word}"


def _fmt_duration(seconds: int | None) -> str:
    seconds = seconds or 0
    return f"{seconds // 60}:{seconds % 60:02d}"


def _results_keyboard(tracks: list[dict], page: int) -> InlineKeyboardMarkup:
    pages = max(1, (len(tracks) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = tracks[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    rows = []
    for t in chunk:
        dur = f" ({_fmt_duration(t['duration'])})" if t.get("duration") else ""
        text = f"{t['artist']} — {t['title']}{dur}{t.get('badge', '')}"
        # cb — своё действие для кнопки (например, песня из архива друга)
        rows.append([InlineKeyboardButton(text=text[:64], callback_data=t.get("cb") or f"t:{t['id']}")])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"p:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"p:{page + 1}"))
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


BTN_CHART = "📈 Чарты"
BTN_WAVE = "🌊 Моя волна"
BTN_MIX_OLD = "🎧 Мой микс"  # кнопка из старой клавиатуры — тоже запускает волну
BTN_CLEAR = "🧹 Очистить чат"
BTN_FRIENDS = "👥 Друзья"
BTN_MOOD = "🎭 Настроение"
MOODS = {"active": "🔥 Бодрое", "fun": "😊 Весёлое", "calm": "😌 Спокойное",
         "sad": "😢 Грустное", "sport": "🏃 Для спорта", "sleep": "😴 Для сна"}
WAVE_SIZE = 10  # максимум треков в одном альбоме Telegram


def _main_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_CHART), KeyboardButton(text=BTN_WAVE)],
        [KeyboardButton(text=BTN_MOOD), KeyboardButton(text=BTN_FRIENDS)],
        [KeyboardButton(text=BTN_CLEAR)],
    ]
    if settings.webapp_url:
        rows.append([KeyboardButton(text="▶️ Открыть плеер", web_app=WebAppInfo(url=settings.webapp_url))])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, input_field_placeholder="Название песни или исполнителя")


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
        f"{BTN_CHART} — самые популярные песни\n"
        f"{BTN_WAVE} — песни подряд, как плейлист\n"
        f"{BTN_FRIENDS} — друзья и их последние 50 песен\n"
        f"{BTN_CLEAR} — убрать присланные песни\n"
        + ("/yandex — подключить свой Яндекс для личной волны\n" if yandex_enabled() else "")
        + "/help — помощь"
    )
    await message.answer(text, reply_markup=_main_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "🔍 Отправьте текст — например, «Макс Корж» или «Жить в кайф».\n"
        "Нажмите на трек в списке — я пришлю его файлом.\n"
        f"{BTN_CHART} — самые популярные песни.\n"
        f"{BTN_WAVE} — 10 песен альбомом: нажмите на первую, и они будут играть подряд.\n"
        "Перед новой волной старая удаляется, чтобы плеер не играл прошлые песни.\n"
        f"{BTN_MOOD} — волна под настроение: бодрое, спокойное, для спорта, для сна…\n"
        f"{BTN_FRIENDS} — друзья и их последние 50 песен.\n"
        f"{BTN_CLEAR} — удалить из чата все песни от бота (Telegram позволяет только за последние 48 часов).\n"
        + ("/yandex — подключить свой Яндекс, чтобы волна знала ваш вкус (по желанию).\n" if yandex_enabled() else "")
    )


@router.message(F.text == BTN_CHART)
@router.message(Command("chart"))
async def cmd_chart(message: Message) -> None:
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    chart = getattr(music_service, "chart_tracks", None)
    tracks = await chart(SEARCH_LIMIT) if chart else []
    if not tracks:
        tracks = await music_service.search_tracks("Top 100", limit=SEARCH_LIMIT)
    await _send_results(message, tracks, "📈 Чарт — самое популярное сейчас:")


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


# ---------- Подготовка и отправка треков ----------

async def _prepare_audio(track_id: str, token: str | None = None):
    """
    Возвращает (media, meta, cached): media — file_id или загруженный файл.
    None, если трек недоступен. Бросает AudioDownloadError при сбое скачивания.
    """
    cached = await db.music_db.tg_audio.find_one({"track_id": track_id, "bot_id": BOT_ID})
    if cached:
        meta = {"title": cached["title"], "artist": cached["artist"], "duration": cached.get("duration", 0)}
        return cached["file_id"], meta, True

    song = await music_service.get_audio_url(track_id, token=token)
    if not song or not song.url:
        return None
    data = await fetch_mp3(song.url)
    filename = f"{song.artist} - {song.title}"[:120] + ".mp3"
    meta = {"title": song.title, "artist": song.artist, "duration": int(song.duration or 0)}
    return BufferedInputFile(data, filename=filename), meta, False


async def _cache_file_id(track_id: str, msg: Message, meta: dict) -> None:
    if not msg.audio:
        return
    await db.music_db.tg_audio.update_one(
        {"track_id": track_id, "bot_id": BOT_ID},
        {"$set": {"track_id": track_id, "bot_id": BOT_ID, "file_id": msg.audio.file_id,
                  "title": meta["title"], "artist": meta["artist"], "duration": meta["duration"],
                  "saved_at": datetime.utcnow()}},
        upsert=True,
    )


async def _remember_sent(chat_id: int, message_ids: list[int], kind: str) -> None:
    """Запоминаем отправленные песни, чтобы потом их можно было удалить."""
    if message_ids:
        await db.music_db.sent_audio.insert_many([
            {"chat_id": chat_id, "message_id": m, "kind": kind, "sent_at": datetime.utcnow()}
            for m in message_ids
        ])


async def _delete_sent(bot, chat_id: int, kind: str | None = None) -> int:
    query = {"chat_id": chat_id}
    if kind:
        query["kind"] = kind
    docs = await db.music_db.sent_audio.find(query).to_list(length=None)
    ids = [d["message_id"] for d in docs]
    deleted = 0
    for i in range(0, len(ids), 100):  # Telegram удаляет до 100 сообщений за раз
        chunk = ids[i:i + 100]
        try:
            await bot.delete_messages(chat_id, chunk)
            deleted += len(chunk)
        except Exception:
            # Сообщения старше 48 часов бот удалить не может — пробуем по одному
            for m in chunk:
                try:
                    await bot.delete_message(chat_id, m)
                    deleted += 1
                except Exception:
                    pass
    await db.music_db.sent_audio.delete_many(query)
    return deleted


def dedicate_button(track_id: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text="💌 Посвятить другу", callback_data=f"ded:{track_id}")


async def deliver_track(bot, chat_id: int, track_id: str, *, user_id: int | None = None,
                        reply_markup=None, caption: str | None = None, history: bool = True):
    """
    Присылает песню в чат. user_id — чей аккаунт Яндекса использовать для скачивания
    и в чью историю записать. Возвращает (сообщение, данные песни) или None
    (тогда пользователю уже сообщено об ошибке).
    """
    _, download_token = await user_tokens(user_id) if user_id else (None, None)
    for attempt in range(2):
        try:
            prepared = await _prepare_audio(track_id, download_token)
        except AudioDownloadError as e:
            logger.error("download failed for %s: %s", track_id, e)
            await bot.send_message(chat_id, f"⚠️ Не удалось скачать трек: {e}")
            return None
        if not prepared:
            await bot.send_message(chat_id, "🚫 Трек недоступен (удалён или ограничен правообладателем).")
            return None
        media, meta, cached = prepared
        extra = {} if cached else {"title": meta["title"], "performer": meta["artist"], "duration": meta["duration"]}
        try:
            msg = await bot.send_audio(chat_id, media, reply_markup=reply_markup, caption=caption, **extra)
            break
        except Exception as e:
            if cached and attempt == 0:  # file_id мог стать невалидным — скачаем заново
                logger.warning("cached file_id failed for %s: %s", track_id, e)
                await db.music_db.tg_audio.delete_one({"track_id": track_id, "bot_id": BOT_ID})
                continue
            raise

    if not cached:
        await _cache_file_id(track_id, msg, meta)
    await _remember_sent(chat_id, [msg.message_id], "track")
    if history and user_id:
        await _add_history(user_id, track_id, meta["title"], meta["artist"], meta["duration"])
    return msg, meta


@router.callback_query(F.data.startswith("t:"))
async def send_track(callback: CallbackQuery) -> None:
    track_id = callback.data.split(":", 1)[1]
    if not TRACK_ID_RE.match(track_id):
        await callback.answer("Некорректный трек", show_alert=True)
        return
    await callback.answer("⏳ Загружаю…")
    await callback.bot.send_chat_action(callback.message.chat.id, ChatAction.UPLOAD_VOICE)
    await deliver_track(
        callback.bot, callback.message.chat.id, track_id, user_id=callback.from_user.id,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[dedicate_button(track_id)]]),
    )


# ---------- Моя волна: песни альбомом, играют подряд ----------

WAVE_MODES = {"default": "🎲 Обычная", "favorite": "❤️ Любимое",
              "discover": "✨ Незнакомое", "popular": "🔥 Популярное"}


def _mood_keyboard(current: str | None) -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(text=("✅ " if m == current else "") + label, callback_data=f"mood:{m}")
            for m, label in MOODS.items()]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([InlineKeyboardButton(text=("✅ " if not current else "") + "✖️ Без настроения",
                                      callback_data="mood:none")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _wave_keyboard(mode: str = "default", own_account: bool = True,
                   mood: str | None = None) -> InlineKeyboardMarkup:
    modes = [InlineKeyboardButton(text=("✅ " if m == mode else "") + label, callback_data=f"wave:mode:{m}")
             for m, label in WAVE_MODES.items()]
    rows = [
        [InlineKeyboardButton(text=f"▶️ Ещё {WAVE_SIZE}", callback_data="wave:more"),
         InlineKeyboardButton(text="🔄 Новая волна", callback_data="wave:new")],
        modes[:2],
        modes[2:],
        [InlineKeyboardButton(text=f"🎭 Настроение: {MOODS[mood]}" if mood in MOODS else "🎭 Настроение",
                              callback_data="mood:menu")],
    ]
    if not own_account:
        rows.append([connect_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _wave_seeds(user_id: int) -> tuple[list[str], str]:
    """
    Сиды волны для тех, кто не подключил свой Яндекс: последние разные песни
    из истории в боте (сначала выбранные самим человеком, потом из волны).
    Если истории нет — популярное из чарта.
    """
    picked, seen = [], set()
    for sources in ({"$ne": "wave"}, "wave"):
        cursor = db.music_db.history.find({"user_id": user_id, "source": sources}).sort("listened_at", -1)
        async for h in cursor:
            tid = h["track_id"].split("_")[0]
            if tid not in seen:
                seen.add(tid)
                picked.append(f"track:{tid}")
            if len(picked) >= 3:
                break
        if len(picked) >= 3:
            break
    if picked:
        return picked, "по вашей истории в боте"
    chart = getattr(music_service, "chart_tracks", None)
    top = await chart(3) if chart else []
    return [f"track:{t['id'].split('_')[0]}" for t in top] or ["user:onyourwave"], \
        "пока по популярному — послушайте пару песен, и она подстроится под вас"


async def _send_album(bot, chat_id: int, prepared: list) -> list[Message]:
    await bot.send_chat_action(chat_id, ChatAction.UPLOAD_VOICE)

    def extra(meta, cached):
        return {} if cached else {"title": meta["title"], "performer": meta["artist"], "duration": meta["duration"]}

    if len(prepared) == 1:
        _, (file, meta, cached) = prepared[0]
        return [await bot.send_audio(chat_id, file, **extra(meta, cached))]
    media = [InputMediaAudio(media=file, **extra(meta, cached)) for _, (file, meta, cached) in prepared]
    return await bot.send_media_group(chat_id, media=media)


async def _send_wave(bot, chat_id: int, user_id: int, continue_wave: bool,
                     duo: tuple[list[str], str] | None = None) -> None:
    """duo=(сиды, подпись) — «волна на двоих» по любимым песням двух друзей."""
    mix = getattr(music_service, "my_mix", None)
    if mix is None:
        await bot.send_message(chat_id, "Моя волна доступна только с Яндекс Музыкой. Напишите название песни — я её найду.")
        return

    state = await db.music_db.waves.find_one({"chat_id": chat_id}) or {}
    status = await bot.send_message(chat_id, "🌊 Собираю волну… это займёт до минуты.")

    if not continue_wave:
        # Удаляем прошлую волну, чтобы плеер Telegram не уходил в старые песни
        await _delete_sent(bot, chat_id, kind="wave")
        if state.get("control_id"):
            try:
                await bot.delete_message(chat_id, state["control_id"])
            except Exception:
                pass
    elif state.get("control_id"):
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=state["control_id"], reply_markup=None)
        except Exception:
            pass

    wave_token, download_token = await user_tokens(user_id)
    duo_note = None
    if duo:
        seeds, duo_note = duo
    elif continue_wave and state.get("duo_note") and state.get("seeds"):
        seeds, duo_note = state["seeds"], state["duo_note"]  # продолжаем волну на двоих
    if duo_note:
        source_note = duo_note
    elif wave_token:
        seeds, source_note = None, "ваша волна Яндекса"
    else:
        seeds, source_note = await _wave_seeds(user_id)
        if continue_wave and state.get("seeds"):
            seeds = state["seeds"]  # продолжаем ту же волну
    wave_state = {"session_id": state.get("session_id"), "played": state.get("played", []),
                  "mode": state.get("mode", "default"), "seeds": state.get("seeds"),
                  "mood": state.get("mood")}
    tracks, wave_state = await mix(WAVE_SIZE, state=wave_state, new=not continue_wave,
                                   token=wave_token, seeds=seeds,
                                   incognito=not wave_token or bool(duo_note),
                                   mood=state.get("mood"))
    if not tracks:
        await bot.edit_message_text(chat_id=chat_id, message_id=status.message_id, text="😔 Не удалось получить волну. Попробуйте позже.")
        return

    # Скачиваем параллельно, но не больше 3 треков одновременно
    sem = asyncio.Semaphore(3)

    async def prep(t):
        async with sem:
            try:
                return t, await _prepare_audio(t["id"], download_token)
            except Exception as e:
                logger.warning("wave: skip %s: %s", t["id"], e)
                return t, None

    prepared = [(t, p) for t, p in await asyncio.gather(*(prep(t) for t in tracks)) if p]
    if not prepared:
        await bot.edit_message_text(chat_id=chat_id, message_id=status.message_id, text="😔 Не удалось скачать песни волны. Попробуйте позже.")
        return

    try:
        msgs = await _send_album(bot, chat_id, prepared)
    except Exception as e:
        if not any(p[2] for _, p in prepared):
            raise
        # Какой-то сохранённый file_id стал невалидным — скачиваем такие песни заново
        logger.warning("wave: cached file_id failed, re-uploading: %s", e)
        fresh = []
        for t, p in prepared:
            if p[2]:
                await db.music_db.tg_audio.delete_one({"track_id": t["id"], "bot_id": BOT_ID})
                try:
                    p = await _prepare_audio(t["id"], download_token)
                except Exception:
                    p = None
            if p:
                fresh.append((t, p))
        prepared = fresh
        if not prepared:
            await bot.edit_message_text(chat_id=chat_id, message_id=status.message_id, text="😔 Не удалось отправить волну. Попробуйте позже.")
            return
        msgs = await _send_album(bot, chat_id, prepared)

    for (t, (_, meta, cached)), msg in zip(prepared, msgs):
        if not cached:
            await _cache_file_id(t["id"], msg, meta)
    await _remember_sent(chat_id, [m.message_id for m in msgs], "wave")
    for t, (_, meta, _c) in prepared:
        await _add_history(user_id, t["id"], meta["title"], meta["artist"],
                           meta["duration"] or t.get("duration", 0), source="wave")

    try:
        await bot.delete_message(chat_id, status.message_id)
    except Exception:
        pass
    control = await bot.send_message(
        chat_id,
        f"🌊 Моя волна ({WAVE_MODES.get(wave_state['mode'], '')}"
        + (f", {MOODS[wave_state['mood']]}" if wave_state.get("mood") in MOODS else "")
        + f"): {_songs(len(msgs))} — {source_note}.\n"
        "Нажмите ▶️ на первой песне — остальные будут играть подряд.\n"
        "Ниже можно выбрать, какой будет следующая волна."
        + ("" if wave_token else (
            "\n\n🔗 Хотите, чтобы волна знала ваш вкус? Подключите свой Яндекс — "
            "это по желанию, подписка не нужна.")),
        reply_markup=_wave_keyboard(wave_state["mode"], own_account=bool(wave_token),
                                    mood=wave_state.get("mood")),
    )
    await db.music_db.waves.update_one(
        {"chat_id": chat_id},
        {"$set": {"chat_id": chat_id, "user_id": user_id, "duo_note": duo_note,
                  "control_id": control.message_id, **wave_state}},
        upsert=True,
    )


@router.message(F.text.in_({BTN_WAVE, BTN_MIX_OLD}))
@router.message(Command("wave"))
async def cmd_wave(message: Message) -> None:
    await _send_wave(message.bot, message.chat.id, message.from_user.id, continue_wave=False)


@router.callback_query(F.data.in_({"wave:more", "wave:new"}))
async def wave_buttons(callback: CallbackQuery) -> None:
    await callback.answer("⏳ Собираю…")
    await _send_wave(callback.bot, callback.message.chat.id, callback.from_user.id,
                     continue_wave=callback.data == "wave:more")


@router.message(F.text == BTN_MOOD)
@router.message(Command("mood"))
async def cmd_mood(message: Message) -> None:
    if not hasattr(music_service, "my_mix"):
        await message.answer("Настроение доступно только с Яндекс Музыкой.")
        return
    state = await db.music_db.waves.find_one({"chat_id": message.chat.id}) or {}
    await message.answer("🎭 Какое у вас настроение? Соберу волну под него:",
                         reply_markup=_mood_keyboard(state.get("mood")))


@router.callback_query(F.data == "mood:menu")
async def mood_menu(callback: CallbackQuery) -> None:
    state = await db.music_db.waves.find_one({"chat_id": callback.message.chat.id}) or {}
    await callback.message.answer("🎭 Какое у вас настроение? Соберу волну под него:",
                                  reply_markup=_mood_keyboard(state.get("mood")))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^mood:(none|active|fun|calm|sad|sport|sleep)$"))
async def mood_chosen(callback: CallbackQuery) -> None:
    mood = callback.data.split(":")[1]
    mood = None if mood == "none" else mood
    chat_id = callback.message.chat.id
    await db.music_db.waves.update_one({"chat_id": chat_id}, {"$set": {"chat_id": chat_id, "mood": mood}}, upsert=True)
    await callback.answer(f"Настроение: {MOODS[mood]}. Собираю волну…" if mood else "Обычная волна. Собираю…")
    try:
        await callback.message.delete()  # меню выбора больше не нужно
    except Exception:
        pass
    await _send_wave(callback.bot, chat_id, callback.from_user.id, continue_wave=False)


@router.callback_query(F.data.regexp(r"^wave:mode:(default|favorite|discover|popular)$"))
async def wave_mode(callback: CallbackQuery) -> None:
    mode = callback.data.rsplit(":", 1)[1]
    await db.music_db.waves.update_one(
        {"chat_id": callback.message.chat.id}, {"$set": {"chat_id": callback.message.chat.id, "mode": mode}}, upsert=True
    )
    await callback.answer(f"Волна: {WAVE_MODES[mode]}. Собираю…")
    await _send_wave(callback.bot, callback.message.chat.id, callback.from_user.id, continue_wave=False)


@router.message(F.text == BTN_CLEAR)
@router.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    deleted = await _delete_sent(message.bot, message.chat.id)
    state = await db.music_db.waves.find_one({"chat_id": message.chat.id})
    if state and state.get("control_id"):
        try:
            await message.bot.delete_message(message.chat.id, state["control_id"])
        except Exception:
            pass
        # Режим волны и список уже присланных песен сохраняем — повторов не будет
        await db.music_db.waves.update_one({"chat_id": message.chat.id}, {"$unset": {"control_id": ""}})
    try:
        await message.delete()
    except Exception:
        pass
    note = await message.answer(f"🧹 Удалено: {_songs(deleted)}." + (
        "" if deleted else "\nTelegram не даёт боту удалять сообщения старше 48 часов — "
                           "их можно удалить вручную или очистить историю чата."))
    await asyncio.sleep(5)
    try:
        await note.delete()
    except Exception:
        pass


# ---------- Поиск ----------

@router.message(F.text & ~F.text.startswith("/"))
async def search(message: Message) -> None:
    query = message.text.strip()
    if not query:
        return
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    tracks = await music_service.search_tracks(query, limit=SEARCH_LIMIT)
    await _send_results(message, tracks, f"🔍 Результаты по запросу «{query[:100]}»:")


async def _add_history(user_id: int, track_id: str, title: str, artist: str,
                       duration: int = 0, source: str = "bot") -> None:
    # Тот же формат, что у POST /api/auth/history — рекомендации общие для бота и Mini App
    await db.music_db.history.insert_one({
        "user_id": user_id,
        "track_id": track_id,
        "title": title,
        "artist": artist or "Unknown",
        "duration": duration or 0,
        "listened_at": datetime.utcnow(),
        "source": source,
    })
