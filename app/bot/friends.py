"""
Друзья в боте и архив последних прослушанных песен.

- Дружба только взаимная: второй человек должен нажать «Принять».
- Добавить друга можно по личной ссылке-приглашению (t.me/<бот>?start=f_<id>)
  или отправив боту @username человека, который уже пользовался ботом.
- Архив — последние ARCHIVE_SIZE разных песен из истории (коллекция history).
"""
import re
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.handlers import BTN_FRIENDS, _main_keyboard, _save_user, _send_results
from app.bot.names import bot_username
from app.core.database import db

router = Router(name="friends")

ARCHIVE_SIZE = 50
MAX_FRIENDS = 100
USERNAME_RE = re.compile(r"^@([A-Za-z0-9_]{1,32})$")


# ---------- Хранилище ----------

def _pair(a: int, b: int) -> dict:
    lo, hi = sorted((a, b))
    return {"a": lo, "b": hi}


async def are_friends(a: int, b: int) -> bool:
    return await db.music_db.friends.find_one(_pair(a, b)) is not None


async def friend_ids(user_id: int) -> list[int]:
    docs = await db.music_db.friends.find({"$or": [{"a": user_id}, {"b": user_id}]}).to_list(length=MAX_FRIENDS)
    return [d["b"] if d["a"] == user_id else d["a"] for d in docs]


async def add_friends(a: int, b: int) -> None:
    await db.music_db.friends.update_one(
        _pair(a, b), {"$setOnInsert": {**_pair(a, b), "since": datetime.utcnow()}}, upsert=True
    )
    await db.music_db.friend_requests.delete_many({"$or": [{"from": a, "to": b}, {"from": b, "to": a}]})


async def remove_friends(a: int, b: int) -> None:
    await db.music_db.friends.delete_one(_pair(a, b))


async def display_name(user_id: int) -> str:
    u = await db.music_db.users.find_one({"id": user_id})
    if not u:
        return f"Пользователь {user_id}"
    name = u.get("first_name") or "Без имени"
    return f"{name} (@{u['username']})" if u.get("username") else name


async def invites_blocked(user_id: int) -> bool:
    u = await db.music_db.users.find_one({"id": user_id})
    return bool(u and u.get("invites_blocked"))


async def set_invites_blocked(user_id: int, blocked: bool) -> None:
    await db.music_db.users.update_one({"id": user_id}, {"$set": {"invites_blocked": blocked}}, upsert=True)
    if blocked:  # ожидающие заявки больше не нужны
        await db.music_db.friend_requests.delete_many({"to": user_id})


async def archive(user_id: int, limit: int = ARCHIVE_SIZE) -> list[dict]:
    """Последние разные песни пользователя, новые сверху."""
    cursor = db.music_db.history.find({"user_id": user_id}).sort("listened_at", -1)
    tracks, seen = [], set()
    async for h in cursor:
        if h["track_id"] in seen:
            continue
        seen.add(h["track_id"])
        tracks.append({
            "id": h["track_id"],
            "title": h.get("title", ""),
            "artist": h.get("artist", ""),
            "duration": h.get("duration", 0),
        })
        if len(tracks) >= limit:
            break
    return tracks


# ---------- Клавиатуры ----------

async def _friends_menu(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    ids = await friend_ids(user_id)
    rows = [[InlineKeyboardButton(text="📜 Мой архив", callback_data="fa:me")]]
    for fid in ids:
        rows.append([
            InlineKeyboardButton(text=f"👤 {await display_name(fid)}"[:60], callback_data=f"fa:{fid}"),
            InlineKeyboardButton(text="❌", callback_data=f"fx:{fid}"),
        ])
    rows.append([InlineKeyboardButton(text="➕ Добавить друга", callback_data="fr:add")])
    blocked = await invites_blocked(user_id)
    rows.append([InlineKeyboardButton(
        text="🔔 Разрешить приглашения" if blocked else "🔕 Запретить приглашения",
        callback_data="fr:allow" if blocked else "fr:block",
    )])
    u = await db.music_db.users.find_one({"id": user_id}) or {}
    rows.append([InlineKeyboardButton(
        text="🤫 Разрешить анонимные посвящения" if u.get("anon_blocked") else "🙅 Запретить анонимные посвящения",
        callback_data="fr:anon:1" if u.get("anon_blocked") else "fr:anon:0",
    )])
    if ids:
        text = f"👥 Ваши друзья ({len(ids)}). Нажмите на друга, чтобы открыть его архив — последние {ARCHIVE_SIZE} песен."
    else:
        text = "👥 У вас пока нет друзей в боте.\nНажмите «➕ Добавить друга»."
    if blocked:
        text += "\n\n🔕 Приглашения в друзья запрещены — заявки от других вам не приходят."
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _request_keyboard(from_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"fq:y:{from_id}"),
        InlineKeyboardButton(text="✖️ Отклонить", callback_data=f"fq:n:{from_id}"),
    ]])


# ---------- Меню друзей ----------

@router.message(F.text == BTN_FRIENDS)
@router.message(Command("friends"))
async def friends_menu(message: Message) -> None:
    await _save_user(message)
    text, kb = await _friends_menu(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "fr:menu")
async def friends_menu_cb(callback: CallbackQuery) -> None:
    text, kb = await _friends_menu(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.in_({"fr:block", "fr:allow"}))
async def toggle_invites(callback: CallbackQuery) -> None:
    blocked = callback.data == "fr:block"
    await set_invites_blocked(callback.from_user.id, blocked)
    await callback.answer(
        "🔕 Приглашения запрещены. Заявки больше не будут приходить." if blocked
        else "🔔 Приглашения снова разрешены.",
        show_alert=True,
    )
    text, kb = await _friends_menu(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.in_({"fr:anon:0", "fr:anon:1"}))
async def toggle_anon(callback: CallbackQuery) -> None:
    allow = callback.data.endswith("1")
    await db.music_db.users.update_one({"id": callback.from_user.id},
                                       {"$set": {"anon_blocked": not allow}}, upsert=True)
    await callback.answer("Анонимные посвящения разрешены." if allow
                          else "Анонимные посвящения больше не будут приходить.", show_alert=True)
    text, kb = await _friends_menu(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data == "fr:add")
async def add_friend_help(callback: CallbackQuery) -> None:
    link = f"https://t.me/{await bot_username(callback.bot)}?start=f_{callback.from_user.id}"
    await callback.message.answer(
        "➕ Как добавить друга — два способа:\n\n"
        f"1️⃣ Перешлите другу эту ссылку:\n{link}\n"
        "Он откроет бота и нажмёт «Принять».\n\n"
        "2️⃣ Или отправьте мне его @username (например, @ivan). "
        "Сработает, если друг уже пользовался ботом.\n\n"
        f"Друзья видят последние {ARCHIVE_SIZE} песен друг друга.",
        disable_web_page_preview=True,
    )
    await callback.answer()


# ---------- Заявки ----------

async def _offer_friendship(message: Message, from_id: int) -> None:
    """Показать пользователю заявку от from_id."""
    me_id = message.from_user.id if message.from_user else message.chat.id
    if from_id == me_id:
        await message.answer("Это ваша собственная ссылка — отправьте её другу 🙂")
        return
    if await are_friends(from_id, me_id):
        await message.answer(f"Вы уже друзья с {await display_name(from_id)} 👥")
        return
    if not await db.music_db.users.find_one({"id": from_id}):
        await message.answer("Ссылка-приглашение недействительна.")
        return
    # Человек сам открыл ссылку — показываем заявку даже при запрете приглашений
    await db.music_db.friend_requests.update_one(
        {"from": from_id, "to": me_id},
        {"$set": {"from": from_id, "to": me_id, "created": datetime.utcnow()}},
        upsert=True,
    )
    await message.answer(
        f"👋 {await display_name(from_id)} хочет добавить вас в друзья.\n"
        f"Вы будете видеть последние {ARCHIVE_SIZE} песен друг друга.",
        reply_markup=_request_keyboard(from_id),
    )


@router.message(CommandStart(deep_link=True, magic=F.args.regexp(r"^f_\d+$")))
async def start_with_invite(message: Message, command: CommandObject) -> None:
    await _save_user(message)
    await message.answer("🎵 Добро пожаловать!", reply_markup=_main_keyboard())
    await _offer_friendship(message, int(command.args[2:]))


@router.message(F.text.regexp(USERNAME_RE.pattern))
async def add_by_username(message: Message) -> None:
    await _save_user(message)
    username = USERNAME_RE.match(message.text.strip()).group(1)
    target = await db.music_db.users.find_one(
        {"username": {"$regex": f"^{re.escape(username)}$", "$options": "i"}}
    )
    me_id = message.from_user.id
    if not target:
        await message.answer(
            f"🤷 @{username} ещё не пользовался ботом.\n"
            "Нажмите «👥 Друзья» → «➕ Добавить друга» и перешлите ему ссылку."
        )
        return
    if target["id"] == me_id:
        await message.answer("Это вы 🙂")
        return
    if await are_friends(me_id, target["id"]):
        await message.answer(f"Вы уже друзья с @{username} 👥")
        return
    if target.get("invites_blocked"):
        await message.answer(f"🔕 @{username} сейчас не принимает приглашения в друзья.")
        return

    await db.music_db.friend_requests.update_one(
        {"from": me_id, "to": target["id"]},
        {"$set": {"from": me_id, "to": target["id"], "created": datetime.utcnow()}},
        upsert=True,
    )
    try:
        await message.bot.send_message(
            target["id"],
            f"👋 {await display_name(me_id)} хочет добавить вас в друзья.\n"
            f"Вы будете видеть последние {ARCHIVE_SIZE} песен друг друга.",
            reply_markup=_request_keyboard(me_id),
        )
    except Exception:
        await message.answer(f"Не получилось написать @{username}: возможно, он остановил бота.")
        return
    await message.answer(f"📨 Заявка отправлена @{username}. Как только он примет её — вы станете друзьями.")


@router.callback_query(F.data.regexp(r"^fq:[yn]:\d+$"))
async def answer_request(callback: CallbackQuery) -> None:
    _, answer, from_id = callback.data.split(":")
    from_id, me_id = int(from_id), callback.from_user.id
    if answer == "n":
        await db.music_db.friend_requests.delete_many({"from": from_id, "to": me_id})
        await callback.message.edit_text("Заявка отклонена.")
        await callback.answer()
        return

    if not await db.music_db.friend_requests.find_one({"from": from_id, "to": me_id}):
        await callback.message.edit_text("Эта заявка уже недействительна.")
        await callback.answer()
        return
    if len(await friend_ids(me_id)) >= MAX_FRIENDS:
        await callback.answer(f"У вас уже {MAX_FRIENDS} друзей — это максимум.", show_alert=True)
        return
    await add_friends(from_id, me_id)
    await callback.message.edit_text(f"✅ Теперь вы друзья с {await display_name(from_id)}!\nОткройте «{BTN_FRIENDS}».")
    await callback.answer()
    try:
        await callback.bot.send_message(from_id, f"🎉 {await display_name(me_id)} принял(а) вашу заявку в друзья!")
    except Exception:
        pass


# ---------- Архивы и удаление ----------

@router.callback_query(F.data.regexp(r"^fa:(me|\d+)$"))
async def open_archive(callback: CallbackQuery) -> None:
    who = callback.data.split(":")[1]
    me_id = callback.from_user.id
    if who == "me":
        owner, header = me_id, f"📜 Ваш архив — последние {ARCHIVE_SIZE} песен:"
    else:
        owner = int(who)
        if not await are_friends(me_id, owner):
            await callback.answer("Архив доступен только друзьям.", show_alert=True)
            return
        header = f"📜 Архив: {await display_name(owner)}\nНажмите на песню — я пришлю её вам."
    await callback.answer()
    tracks = await archive(owner)
    if not tracks:
        await callback.message.answer("Архив пока пуст — здесь появятся песни, которые человек слушал через бота.")
        return
    from app.bot.social import reaction_badges  # здесь, чтобы не было циклического импорта

    badges = await reaction_badges(owner, [t["id"] for t in tracks])
    for t in tracks:
        t["cb"] = f"ft:{owner}:{t['id']}"  # песня из архива: с реакциями и посвящением
        t["badge"] = badges.get(t["id"], "")
    if owner != me_id:
        header += "\nПод песней можно поставить реакцию — друг её увидит."
    await _send_results(callback.message, tracks, header)


@router.callback_query(F.data.regexp(r"^fx:\d+$"))
async def ask_remove(callback: CallbackQuery) -> None:
    fid = int(callback.data.split(":")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да, удалить", callback_data=f"fy:{fid}"),
        InlineKeyboardButton(text="Отмена", callback_data="fr:menu"),
    ]])
    await callback.message.edit_text(f"Удалить {await display_name(fid)} из друзей?", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^fy:\d+$"))
async def do_remove(callback: CallbackQuery) -> None:
    fid = int(callback.data.split(":")[1])
    await remove_friends(callback.from_user.id, fid)
    await callback.answer("Удалено")
    text, kb = await _friends_menu(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)
