"""
Игры с друзьями.

🎵 Угадай мелодию — 15 секунд песни без названия и 4 варианта ответа.
🕵️ Чей это трек? — песня из архива одного из друзей, угадать чья.
⚔️ Батл треков — турнир из 4 песен участников, все голосуют, победитель идёт дальше.
🏆 Рейтинг — очки за игры у вас и ваших друзей.
"""
import random
import secrets
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.accounts import user_tokens
from app.bot.audio import AudioDownloadError, fetch_mp3, make_clip
from app.bot.friends import archive, display_name, friend_ids
from app.bot.handlers import BTN_GAMES
from app.core.database import db
from app.services.source import music_service

router = Router(name="games")

GUESS_OPTIONS = 4
BATTLE_SIZE = 4  # 4 песни: два полуфинала и финал


def _kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _label(t: dict) -> str:
    return f"{t['artist']} — {t['title']}"


async def _add_points(user_id: int, game: str, points: int = 1) -> int:
    await db.music_db.scores.update_one(
        {"user_id": user_id}, {"$inc": {game: points, "total": points}, "$set": {"user_id": user_id}}, upsert=True
    )
    doc = await db.music_db.scores.find_one({"user_id": user_id})
    return doc.get("total", 0)


def _games_menu() -> InlineKeyboardMarkup:
    return _kb([
        [InlineKeyboardButton(text="🎵 Угадай мелодию", callback_data="gm:guess")],
        [InlineKeyboardButton(text="🕵️ Чей это трек?", callback_data="gm:whose")],
        [InlineKeyboardButton(text="⚔️ Батл треков", callback_data="gm:battle")],
        [InlineKeyboardButton(text="🏆 Рейтинг друзей", callback_data="gm:top")],
    ])


@router.message(F.text == BTN_GAMES)
@router.message(Command("games"))
async def games_menu(message: Message) -> None:
    await message.answer(
        "🎮 Игры\n\n"
        "🎵 Угадай мелодию — 15 секунд песни, угадайте, что это.\n"
        "🕵️ Чей это трек? — песня из архива друга, угадайте, кто её слушал.\n"
        "⚔️ Батл треков — турнир песен ваших друзей, все голосуют.\n\n"
        "За правильные ответы — очки в рейтинге друзей.",
        reply_markup=_games_menu(),
    )


# ---------- 🎵 Угадай мелодию ----------

async def _song_pool(user_id: int) -> list[dict]:
    pool: dict[str, dict] = {}
    chart = getattr(music_service, "chart_tracks", None)
    for t in (await chart(50) if chart else []):
        pool[t["id"]] = t
    for uid in [user_id, *await friend_ids(user_id)]:
        for t in await archive(uid, 30):
            pool.setdefault(t["id"], t)
    return [t for t in pool.values() if t.get("title") and t.get("artist")]


@router.callback_query(F.data.in_({"gm:guess", "g:next"}))
async def guess_round(callback: CallbackQuery) -> None:
    await callback.answer("🎵 Готовлю отрывок…")
    bot, chat_id, me = callback.bot, callback.message.chat.id, callback.from_user.id
    pool = await _song_pool(me)
    if len(pool) < GUESS_OPTIONS:
        await bot.send_message(chat_id, "Пока мало песен для игры — послушайте что-нибудь через бота.")
        return

    _, download_token = await user_tokens(me)
    random.shuffle(pool)
    answer, clip = None, None
    for candidate in pool[:5]:  # если песня недоступна — берём следующую
        song = await music_service.get_audio_url(candidate["id"], token=download_token)
        if not song or not song.url:
            continue
        try:
            clip = make_clip(await fetch_mp3(song.url))
            answer = candidate
            break
        except AudioDownloadError:
            continue
    if not answer:
        await bot.send_message(chat_id, "😔 Не получилось подготовить отрывок. Попробуйте ещё раз.")
        return

    others = [t for t in pool if t["artist"] != answer["artist"]]
    options = random.sample(others, min(GUESS_OPTIONS - 1, len(others))) + [answer]
    random.shuffle(options)
    qid = secrets.token_hex(4)
    await db.music_db.game_questions.insert_one({
        "id": qid, "user_id": me, "game": "guess", "answer": options.index(answer),
        "options": [_label(t) for t in options], "track_id": answer["id"], "created": datetime.utcnow(),
    })
    await bot.send_audio(
        chat_id, BufferedInputFile(clip, filename="guess.mp3"),
        title="🎵 Угадай мелодию", performer="???", duration=15,
        caption="🎵 Что это за песня?",
        reply_markup=_kb([[InlineKeyboardButton(text=_label(t)[:60], callback_data=f"g:{qid}:{i}")]
                          for i, t in enumerate(options)]),
    )


@router.callback_query(F.data.regexp(r"^g:[0-9a-f]{8}:\d$"))
async def guess_answer(callback: CallbackQuery) -> None:
    _, qid, idx = callback.data.split(":")
    q = await db.music_db.game_questions.find_one({"id": qid})
    if not q or q["user_id"] != callback.from_user.id or q.get("done"):
        await callback.answer("Этот вопрос уже закрыт.")
        return
    await db.music_db.game_questions.update_one({"id": qid}, {"$set": {"done": True}})
    right = q["options"][q["answer"]]
    if int(idx) == q["answer"]:
        total = await _add_points(callback.from_user.id, "guess")
        text = f"✅ Верно! Это {right}\n+1 очко, всего: {total}"
    else:
        text = f"❌ Не угадали. Это была песня {right}"
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(text, reply_markup=_kb([
        [InlineKeyboardButton(text="▶️ Следующая", callback_data="g:next")],
        [InlineKeyboardButton(text="🎧 Прислать песню целиком", callback_data=f"t:{q['track_id']}")],
    ]))


# ---------- 🕵️ Чей это трек? ----------

@router.callback_query(F.data.in_({"gm:whose", "w:next"}))
async def whose_round(callback: CallbackQuery) -> None:
    me = callback.from_user.id
    candidates = {}
    for fid in await friend_ids(me):
        tracks = await archive(fid, 30)
        if len(tracks) >= 3:
            candidates[fid] = tracks
    if len(candidates) < 2:
        await callback.answer(
            "Нужно хотя бы 2 друга, у которых в архиве есть 3+ песни. Позовите друзей послушать музыку!",
            show_alert=True,
        )
        return
    await callback.answer()
    owner = random.choice(list(candidates))
    # Песня, которой нет в архивах других вариантов — чтобы ответ был однозначным
    names = random.sample([f for f in candidates if f != owner], min(3, len(candidates) - 1)) + [owner]
    others_tracks = {t["id"] for f in names if f != owner for t in candidates[f]}
    pick = [t for t in candidates[owner] if t["id"] not in others_tracks] or candidates[owner]
    track = random.choice(pick)
    random.shuffle(names)
    qid = secrets.token_hex(4)
    await db.music_db.game_questions.insert_one({
        "id": qid, "user_id": me, "game": "whose", "answer": names.index(owner), "options": names,
        "track_id": track["id"], "created": datetime.utcnow(),
    })
    rows = [[InlineKeyboardButton(text=f"👤 {await display_name(f)}"[:60], callback_data=f"w:{qid}:{i}")]
            for i, f in enumerate(names)]
    rows.append([InlineKeyboardButton(text="🎧 Послушать", callback_data=f"t:{track['id']}")])
    await callback.message.answer(f"🕵️ Чей это трек?\n\n🎵 {_label(track)}", reply_markup=_kb(rows))


@router.callback_query(F.data.regexp(r"^w:[0-9a-f]{8}:\d$"))
async def whose_answer(callback: CallbackQuery) -> None:
    _, qid, idx = callback.data.split(":")
    q = await db.music_db.game_questions.find_one({"id": qid})
    if not q or q["user_id"] != callback.from_user.id or q.get("done"):
        await callback.answer("Этот вопрос уже закрыт.")
        return
    await db.music_db.game_questions.update_one({"id": qid}, {"$set": {"done": True}})
    owner = q["options"][q["answer"]]
    if int(idx) == q["answer"]:
        total = await _add_points(callback.from_user.id, "whose")
        text = f"✅ Верно! Это из архива: {await display_name(owner)}\n+1 очко, всего: {total}"
    else:
        text = f"❌ Мимо! Это слушал(а) {await display_name(owner)}"
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(text, reply_markup=_kb([[InlineKeyboardButton(text="▶️ Ещё", callback_data="w:next")]]))


# ---------- 🏆 Рейтинг ----------

@router.callback_query(F.data == "gm:top")
async def leaderboard(callback: CallbackQuery) -> None:
    me = callback.from_user.id
    people = [me, *await friend_ids(me)]
    rows = []
    for uid in people:
        s = await db.music_db.scores.find_one({"user_id": uid}) or {}
        rows.append((s.get("total", 0), s.get("guess", 0), s.get("whose", 0), uid))
    rows.sort(reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 Рейтинг друзей\n"]
    for i, (total, guess, whose, uid) in enumerate(rows):
        who = "Вы" if uid == me else await display_name(uid)
        lines.append(f"{medals[i] if i < 3 else f'{i + 1}.'} {who} — {total} (🎵 {guess} · 🕵️ {whose})")
    await callback.answer()
    await callback.message.answer("\n".join(lines))


# ---------- ⚔️ Батл треков ----------

async def _save(b: dict) -> None:
    await db.music_db.battles.update_one({"id": b["id"]}, {"$set": {k: v for k, v in b.items() if k != "_id"}})


async def _battle(bid: str) -> dict | None:
    return await db.music_db.battles.find_one({"id": bid})


def _lobby_kb(bid: str) -> InlineKeyboardMarkup:
    return _kb([[InlineKeyboardButton(text="▶️ Начать", callback_data=f"bs:{bid}"),
                 InlineKeyboardButton(text="Отмена", callback_data=f"bc:{bid}")]])


async def _lobby_text(b: dict) -> str:
    names = [await display_name(u) for u in b["players"]]
    return ("⚔️ Батл треков\n\nПриглашения отправлены друзьям. "
            "Когда соберутся — нажмите «Начать».\n\nУчастники: " + ", ".join(names))


@router.callback_query(F.data == "gm:battle")
async def battle_create(callback: CallbackQuery) -> None:
    me = callback.from_user.id
    friends = await friend_ids(me)
    if not friends:
        await callback.answer("Для батла нужны друзья — добавьте их в «👥 Друзья».", show_alert=True)
        return
    await callback.answer()
    bid = secrets.token_hex(4)
    b = {"id": bid, "creator": me, "players": [me], "state": "lobby", "created": datetime.utcnow()}
    lobby = await callback.message.answer(await _lobby_text(b), reply_markup=_lobby_kb(bid))
    b["lobby_msg"] = lobby.message_id
    await db.music_db.battles.insert_one(b)
    for fid in friends:
        try:
            await callback.bot.send_message(
                fid, f"⚔️ {await display_name(me)} зовёт вас в батл треков! Песни участников сражаются, "
                     "все голосуют за лучшую.",
                reply_markup=_kb([[InlineKeyboardButton(text="✅ Участвую", callback_data=f"bj:{bid}")]]),
            )
        except Exception:
            pass


@router.callback_query(F.data.regexp(r"^bj:[0-9a-f]{8}$"))
async def battle_join(callback: CallbackQuery) -> None:
    b = await _battle(callback.data[3:])
    me = callback.from_user.id
    if not b or b["state"] != "lobby":
        await callback.answer("Этот батл уже начался или отменён.", show_alert=True)
        return
    if me not in b["players"]:
        await db.music_db.battles.update_one({"id": b["id"]}, {"$push": {"players": me}})
        b["players"].append(me)
        try:
            await callback.bot.edit_message_text(chat_id=b["creator"], message_id=b["lobby_msg"],
                                                 text=await _lobby_text(b), reply_markup=_lobby_kb(b["id"]))
        except Exception:
            pass
    await callback.answer("Вы в игре! Ждём начала.")
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.regexp(r"^bc:[0-9a-f]{8}$"))
async def battle_cancel(callback: CallbackQuery) -> None:
    b = await _battle(callback.data[3:])
    if not b or b["creator"] != callback.from_user.id:
        await callback.answer()
        return
    await db.music_db.battles.update_one({"id": b["id"]}, {"$set": {"state": "cancelled"}})
    await callback.message.edit_text("Батл отменён.")
    await callback.answer()


async def _battle_songs(players: list[int]) -> list[dict]:
    songs: dict[str, dict] = {}
    archives = [await archive(p, 10) for p in players]
    for i in range(10):  # по очереди от каждого участника
        for tracks in archives:
            if i < len(tracks):
                songs.setdefault(tracks[i]["id"], tracks[i])
            if len(songs) >= BATTLE_SIZE:
                return list(songs.values())
    chart = getattr(music_service, "chart_tracks", None)
    for t in (await chart(20) if chart else []):
        songs.setdefault(t["id"], t)
        if len(songs) >= BATTLE_SIZE:
            break
    return list(songs.values())


async def _send_all(bot, b: dict, text: str, kb=None) -> None:
    for p in b["players"]:
        try:
            await bot.send_message(p, text, reply_markup=kb)
        except Exception:
            pass


async def _announce_match(bot, b: dict) -> None:
    m = b["matches"][b["current"]]
    s1, s2 = b["songs"][m[0]], b["songs"][m[1]]
    stage = "🏆 ФИНАЛ" if b["current"] == len(b["matches"]) - 1 else f"Матч {b['current'] + 1}"
    kb = _kb([
        [InlineKeyboardButton(text="▶️ Песня 1", callback_data=f"t:{s1['id']}"),
         InlineKeyboardButton(text="▶️ Песня 2", callback_data=f"t:{s2['id']}")],
        [InlineKeyboardButton(text=f"1️⃣ {_label(s1)}"[:60], callback_data=f"bv:{b['id']}:{b['current']}:0")],
        [InlineKeyboardButton(text=f"2️⃣ {_label(s2)}"[:60], callback_data=f"bv:{b['id']}:{b['current']}:1")],
    ])
    await _send_all(bot, b, f"⚔️ {stage}\n\n1️⃣ {_label(s1)}\n      VS\n2️⃣ {_label(s2)}\n\nПослушайте и голосуйте!", kb)
    try:
        await bot.send_message(b["creator"], "Когда все проголосуют, матч закроется сам. Или закройте его сейчас:",
                               reply_markup=_kb([[InlineKeyboardButton(text="⏭ Подвести итог",
                                                                       callback_data=f"bn:{b['id']}:{b['current']}")]]))
    except Exception:
        pass


@router.callback_query(F.data.regexp(r"^bs:[0-9a-f]{8}$"))
async def battle_start(callback: CallbackQuery) -> None:
    b = await _battle(callback.data[3:])
    if not b or b["creator"] != callback.from_user.id or b["state"] != "lobby":
        await callback.answer()
        return
    songs = await _battle_songs(b["players"])
    if len(songs) < BATTLE_SIZE:
        await callback.answer("Не хватает песен для батла — послушайте что-нибудь через бота.", show_alert=True)
        return
    random.shuffle(songs)
    b.update(state="playing", songs=songs, matches=[[0, 1], [2, 3], [None, None]], current=0, votes={}, winners=[])
    await _save(b)
    await callback.answer()
    await callback.message.edit_text(await _lobby_text(b) + "\n\nБатл начался! ⚔️")
    await _announce_match(callback.bot, b)


async def _close_match(bot, b: dict) -> None:
    votes = b.get("votes", {})
    count = [sum(1 for v in votes.values() if v == 0), sum(1 for v in votes.values() if v == 1)]
    m = b["matches"][b["current"]]
    choice = 0 if count[0] > count[1] else 1 if count[1] > count[0] else random.randint(0, 1)
    winner = m[choice]
    tie = " (ничья — решил жребий 🎲)" if count[0] == count[1] else ""
    b["winners"].append(winner)
    text = f"🏁 Победила песня {_label(b['songs'][winner])} — {count[choice]}:{count[1 - choice]}{tie}"

    if b["current"] == len(b["matches"]) - 1:
        b["state"] = "finished"
        await _save(b)
        await _send_all(bot, b, text + f"\n\n👑 Победитель батла: {_label(b['songs'][winner])}!",
                        _kb([[InlineKeyboardButton(text="🎧 Слушать победителя", callback_data=f"t:{b['songs'][winner]['id']}")]]))
        return
    b["current"] += 1
    if b["current"] == len(b["matches"]) - 1:
        b["matches"][-1] = b["winners"][:2]
    b["votes"] = {}
    await _save(b)
    await _send_all(bot, b, text)
    await _announce_match(bot, b)


@router.callback_query(F.data.regexp(r"^bv:[0-9a-f]{8}:\d:[01]$"))
async def battle_vote(callback: CallbackQuery) -> None:
    _, bid, match, choice = callback.data.split(":")
    b = await _battle(bid)
    me = callback.from_user.id
    if not b or b["state"] != "playing" or int(match) != b["current"] or me not in b["players"]:
        await callback.answer("Голосование по этому матчу уже закрыто.")
        return
    b["votes"][str(me)] = int(choice)
    await db.music_db.battles.update_one({"id": bid}, {"$set": {f"votes.{me}": int(choice)}})
    await callback.answer("Голос принят ✅")
    if len(b["votes"]) >= len(b["players"]):
        await _close_match(callback.bot, b)


@router.callback_query(F.data.regexp(r"^bn:[0-9a-f]{8}:\d$"))
async def battle_next(callback: CallbackQuery) -> None:
    _, bid, match = callback.data.split(":")
    b = await _battle(bid)
    if not b or b["creator"] != callback.from_user.id or b["state"] != "playing" or int(match) != b["current"]:
        await callback.answer("Этот матч уже закрыт.")
        return
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await _close_match(callback.bot, b)
