"""
Посвящения и реакции между друзьями.

Посвящение: под песней «💌 Посвятить другу» → выбрать друга → анонимно или от
своего имени → по желанию пара слов → подтвердить. Друг получает песню и может
ответить «❤️ Спасибо». Посвящать можно только друзьям; любой может запретить
себе анонимные посвящения.

Реакции: под песней, открытой из архива друга, — 🔥 ❤️ 😂 😢. Владелец архива
получает уведомление, а в архиве у песни показывается счётчик реакций.
"""
import secrets
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.friends import are_friends, display_name, friend_ids
from app.bot.handlers import (
    BTN_CHART, BTN_CLEAR, BTN_FRIENDS, BTN_MIX_OLD, BTN_MOOD, BTN_WAVE,
    TRACK_ID_RE, dedicate_button, deliver_track,
)
from app.core.database import db

router = Router(name="social")

REACTIONS = {"f": "🔥", "h": "❤️", "l": "😂", "s": "😢"}
MENU_BUTTONS = {BTN_CHART, BTN_CLEAR, BTN_FRIENDS, BTN_MIX_OLD, BTN_MOOD, BTN_WAVE}
MAX_TEXT = 200
DAILY_LIMIT = 30  # посвящений в сутки от одного человека — защита от спама


class Dedication(StatesGroup):
    text = State()


def _kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _track_title(track_id: str) -> str:
    doc = (await db.music_db.tg_audio.find_one({"track_id": track_id})
           or await db.music_db.history.find_one({"track_id": track_id}))
    if not doc:
        return "песню"
    return f"«{doc.get('artist', '')} — {doc.get('title', '')}»"


async def _anon_blocked(user_id: int) -> bool:
    u = await db.music_db.users.find_one({"id": user_id}) or {}
    return bool(u.get("anon_blocked"))


# ---------- Посвящения ----------

@router.callback_query(F.data.regexp(r"^ded:-?\d+_\d+$"))
async def choose_friend(callback: CallbackQuery) -> None:
    track_id = callback.data.split(":", 1)[1]
    ids = await friend_ids(callback.from_user.id)
    if not ids:
        await callback.answer("Посвящать песни можно друзьям. Добавьте друзей в «👥 Друзья».", show_alert=True)
        return
    rows = [[InlineKeyboardButton(text=f"👤 {await display_name(fid)}"[:60], callback_data=f"dto:{fid}:{track_id}")]
            for fid in ids]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="dcancel")])
    await callback.message.answer(f"💌 Кому посвятить {await _track_title(track_id)}?", reply_markup=_kb(rows))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^dto:\d+:-?\d+_\d+$"))
async def choose_mode(callback: CallbackQuery) -> None:
    _, fid, track_id = callback.data.split(":")
    fid = int(fid)
    if not await are_friends(callback.from_user.id, fid):
        await callback.answer("Посвящать можно только друзьям.", show_alert=True)
        return
    rows = []
    if not await _anon_blocked(fid):
        rows.append([InlineKeyboardButton(text="🤫 Анонимно", callback_data=f"dm:a:{fid}:{track_id}")])
    rows.append([InlineKeyboardButton(text="✍️ От моего имени", callback_data=f"dm:n:{fid}:{track_id}")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="dcancel")])
    note = "" if rows[0][0].callback_data.startswith("dm:a") else "\n(Этот друг не принимает анонимные посвящения.)"
    await callback.message.edit_text(f"Как подписать посвящение для {await display_name(fid)}?{note}",
                                     reply_markup=_kb(rows))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^dm:[an]:\d+:-?\d+_\d+$"))
async def ask_text(callback: CallbackQuery, state: FSMContext) -> None:
    _, mode, fid, track_id = callback.data.split(":")
    await state.set_state(Dedication.text)
    await state.update_data(anon=mode == "a", to=int(fid), track_id=track_id, text="")
    await callback.message.edit_text(
        f"✍️ Напишите пару слов к посвящению (до {MAX_TEXT} символов)\n"
        "или нажмите «Без подписи».",
        reply_markup=_kb([[InlineKeyboardButton(text="Без подписи", callback_data="dpreview")],
                          [InlineKeyboardButton(text="Отмена", callback_data="dcancel")]]),
    )
    await callback.answer()


async def _preview(target: Message, state: FSMContext, edit: bool) -> None:
    data = await state.get_data()
    who = "🤫 анонимно" if data["anon"] else "✍️ от вашего имени"
    text = (f"💌 Посвящение для {await display_name(data['to'])} ({who}):\n"
            f"{await _track_title(data['track_id'])}"
            + (f"\n\n«{data['text']}»" if data.get("text") else "")
            + "\n\nОтправить?")
    kb = _kb([[InlineKeyboardButton(text="✅ Отправить", callback_data="dsend"),
               InlineKeyboardButton(text="Отмена", callback_data="dcancel")]])
    if edit:
        await target.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.message(StateFilter(Dedication.text), F.text.in_(MENU_BUTTONS) | F.text.startswith("/"))
async def leave_dedication(message: Message, state: FSMContext) -> None:
    """Нажали кнопку меню или команду — посвящение отменяется, команда выполняется как обычно."""
    await state.clear()
    raise SkipHandler  # передаём сообщение дальше обычным обработчикам


@router.message(StateFilter(Dedication.text), F.text)
async def got_text(message: Message, state: FSMContext) -> None:
    if "to" not in await state.get_data():  # посвящение уже отменено
        await state.clear()
        raise SkipHandler
    await state.update_data(text=message.text.strip()[:MAX_TEXT])
    await _preview(message, state, edit=False)


@router.callback_query(F.data == "dpreview", StateFilter(Dedication.text))
async def no_text(callback: CallbackQuery, state: FSMContext) -> None:
    if "to" not in await state.get_data():
        await state.clear()
        await callback.answer("Посвящение устарело, начните заново.", show_alert=True)
        return
    await state.update_data(text="")
    await _preview(callback.message, state, edit=True)
    await callback.answer()


@router.callback_query(F.data == "dcancel")
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Посвящение отменено.")
    await callback.answer()


@router.callback_query(F.data == "dsend", StateFilter(Dedication.text))
async def send_dedication(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    if "to" not in data:
        await callback.answer("Посвящение устарело, начните заново.", show_alert=True)
        return
    me, to, track_id = callback.from_user.id, data["to"], data["track_id"]
    if not await are_friends(me, to):
        await callback.message.edit_text("Посвящать можно только друзьям.")
        return await callback.answer()
    anon = data["anon"] and not await _anon_blocked(to)
    since = datetime.utcnow() - timedelta(days=1)
    if await db.music_db.dedications.count_documents({"from": me, "created": {"$gte": since}}) >= DAILY_LIMIT:
        await callback.answer(f"Не больше {DAILY_LIMIT} посвящений в сутки.", show_alert=True)
        return

    ded_id = secrets.token_hex(4)
    header = ("💌 Кто-то посвятил вам эту песню 🤫" if anon
              else f"💌 {await display_name(me)} посвящает вам эту песню")
    caption = header + (f"\n\n«{data['text']}»" if data.get("text") else "")
    rows = [[InlineKeyboardButton(text="❤️ Спасибо", callback_data=f"thx:{ded_id}"),
             dedicate_button(track_id)]]
    if anon:
        rows.append([InlineKeyboardButton(text="🚫 Не получать анонимные посвящения", callback_data="anonoff")])

    await callback.answer("Отправляю…")
    try:
        sent = await deliver_track(callback.bot, to, track_id, user_id=me, reply_markup=_kb(rows),
                                   caption=caption, history=False)
    except Exception:
        sent = None
    if not sent:
        await callback.message.edit_text("⚠️ Не получилось отправить посвящение — возможно, друг остановил бота.")
        return
    await db.music_db.dedications.insert_one({
        "id": ded_id, "from": me, "to": to, "track_id": track_id,
        "anon": anon, "text": data.get("text", ""), "created": datetime.utcnow(),
    })
    await callback.message.edit_text(
        f"💌 Посвящение отправлено: {await display_name(to)}" + (" (анонимно 🤫)" if anon else "") + "."
    )


@router.callback_query(F.data.regexp(r"^thx:[0-9a-f]{8}$"))
async def thank(callback: CallbackQuery) -> None:
    ded = await db.music_db.dedications.find_one({"id": callback.data[4:]})
    if not ded or ded["to"] != callback.from_user.id:
        await callback.answer()
        return
    if ded.get("thanked"):
        await callback.answer("Вы уже поблагодарили ❤️")
        return
    await db.music_db.dedications.update_one({"id": ded["id"]}, {"$set": {"thanked": True}})
    try:
        await callback.bot.send_message(
            ded["from"],
            f"❤️ {await display_name(ded['to'])} благодарит за посвящение {await _track_title(ded['track_id'])}"
            + (" — и до сих пор не знает, что оно от вас 🤫" if ded.get("anon") else "!"),
        )
    except Exception:
        pass
    await callback.answer("Спасибо отправлено ❤️")


@router.callback_query(F.data == "anonoff")
async def anon_off(callback: CallbackQuery) -> None:
    await db.music_db.users.update_one({"id": callback.from_user.id}, {"$set": {"anon_blocked": True}}, upsert=True)
    await callback.answer("Анонимные посвящения больше не будут приходить. Вернуть можно в «👥 Друзья».",
                          show_alert=True)


@router.callback_query(F.data == "anonon")
async def anon_on(callback: CallbackQuery) -> None:
    await db.music_db.users.update_one({"id": callback.from_user.id}, {"$set": {"anon_blocked": False}}, upsert=True)
    await callback.answer("Анонимные посвящения снова разрешены.", show_alert=True)


# ---------- Песни из архива друга и реакции ----------

def reactions_keyboard(owner: int, track_id: str) -> InlineKeyboardMarkup:
    return _kb([
        [InlineKeyboardButton(text=e, callback_data=f"r:{owner}:{c}:{track_id}") for c, e in REACTIONS.items()],
        [dedicate_button(track_id)],
    ])


async def reaction_badges(owner: int, track_ids: list[str]) -> dict[str, str]:
    """{track_id: " · ❤️2🔥1"} — счётчики реакций на песни в архиве."""
    counts: dict[str, dict[str, int]] = {}
    async for r in db.music_db.reactions.find({"owner": owner, "track_id": {"$in": track_ids}}):
        counts.setdefault(r["track_id"], {}).setdefault(r["emoji"], 0)
        counts[r["track_id"]][r["emoji"]] += 1
    return {tid: " · " + "".join(f"{e}{n}" for e, n in c.items()) for tid, c in counts.items()}


@router.callback_query(F.data.regexp(r"^ft:\d+:-?\d+_\d+$"))
async def send_from_archive(callback: CallbackQuery) -> None:
    _, owner, track_id = callback.data.split(":")
    owner, me = int(owner), callback.from_user.id
    if owner != me and not await are_friends(me, owner):
        await callback.answer("Архив доступен только друзьям.", show_alert=True)
        return
    await callback.answer("⏳ Загружаю…")
    markup = (_kb([[dedicate_button(track_id)]]) if owner == me
              else reactions_keyboard(owner, track_id))
    await deliver_track(callback.bot, callback.message.chat.id, track_id, user_id=me, reply_markup=markup)


@router.callback_query(F.data.regexp(r"^r:\d+:[fhls]:-?\d+_\d+$"))
async def react(callback: CallbackQuery) -> None:
    _, owner, code, track_id = callback.data.split(":")
    owner, me, emoji = int(owner), callback.from_user.id, REACTIONS[code]
    if not TRACK_ID_RE.match(track_id) or not await are_friends(me, owner):
        await callback.answer("Реакции доступны только друзьям.", show_alert=True)
        return
    key = {"owner": owner, "track_id": track_id, "from": me}
    old = await db.music_db.reactions.find_one(key)
    if old and old.get("emoji") == emoji:
        await callback.answer(f"Вы уже поставили {emoji}")
        return
    await db.music_db.reactions.update_one(key, {"$set": {**key, "emoji": emoji, "at": datetime.utcnow()}},
                                           upsert=True)
    await callback.answer(f"{emoji} отправлено")
    try:
        await callback.bot.send_message(
            owner, f"{emoji} {await display_name(me)} отреагировал(а) на вашу песню {await _track_title(track_id)}"
        )
    except Exception:
        pass
