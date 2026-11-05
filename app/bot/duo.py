"""
Карточка друга, музыкальная совместимость и «волна на двоих».

Совместимость считается по истории в боте: насколько похоже распределение
исполнителей (пересечение долей) плюс бонус за общие песни.
Волна на двоих строится по последним любимым песням обоих друзей.
"""
import time
from collections import Counter

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.friends import are_friends, display_name
from app.bot.handlers import _send_wave
from app.core.database import db

router = Router(name="duo")

HISTORY_DEPTH = 300
MIN_TRACKS = 5
_notified: dict[tuple[int, int], float] = {}  # не чаще раза в час для пары друзей


def _kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _short_name(user_id: int) -> str:
    u = await db.music_db.users.find_one({"id": user_id}) or {}
    return u.get("first_name") or "друг"


# ---------- Карточка друга ----------

@router.callback_query(F.data.regexp(r"^fc:\d+$"))
async def friend_card(callback: CallbackQuery) -> None:
    fid = int(callback.data[3:])
    if not await are_friends(callback.from_user.id, fid):
        await callback.answer("Вы больше не друзья.", show_alert=True)
        return
    await callback.message.answer(
        f"👤 {await display_name(fid)}",
        reply_markup=_kb([
            [InlineKeyboardButton(text="📜 Архив песен", callback_data=f"fa:{fid}")],
            [InlineKeyboardButton(text="🤝 Совместимость", callback_data=f"cmp:{fid}"),
             InlineKeyboardButton(text="🌊 Волна на двоих", callback_data=f"duo:{fid}")],
            [InlineKeyboardButton(text="❌ Удалить из друзей", callback_data=f"fx:{fid}")],
        ]),
    )
    await callback.answer()


# ---------- Совместимость ----------

async def _taste(user_id: int) -> tuple[Counter, dict[str, str], int]:
    """(вес исполнителей, {track_id: «Исполнитель — Песня»}, сколько записей)."""
    artists: Counter = Counter()
    tracks: dict[str, str] = {}
    n = 0
    cursor = db.music_db.history.find({"user_id": user_id}).sort("listened_at", -1).limit(HISTORY_DEPTH)
    async for h in cursor:
        n += 1
        weight = 1 if h.get("source") == "wave" else 2  # выбранное самим человеком важнее
        for a in (h.get("artist") or "").split(","):
            a = a.strip()
            if a and a != "Unknown":
                artists[a.lower()] += weight
        tracks.setdefault(h["track_id"], f"{h.get('artist', '')} — {h.get('title', '')}")
    return artists, tracks, n


def _verdict(pct: int) -> str:
    if pct >= 80:
        return "👯 Музыкальные близнецы!"
    if pct >= 60:
        return "🔥 Отлично совпадаете — можно смело делиться плейлистами"
    if pct >= 40:
        return "🙂 Есть общее, а остальное — повод открыть что-то новое"
    if pct >= 20:
        return "🎲 Вкусы разные — тем интереснее волна на двоих"
    return "😅 Противоположности притягиваются"


@router.callback_query(F.data.regexp(r"^cmp:\d+$"))
async def compatibility(callback: CallbackQuery) -> None:
    me, fid = callback.from_user.id, int(callback.data[4:])
    if not await are_friends(me, fid):
        await callback.answer("Совместимость доступна только с друзьями.", show_alert=True)
        return
    await callback.answer()
    (a1, t1, n1), (a2, t2, n2) = await _taste(me), await _taste(fid)
    name = await display_name(fid)
    if n1 < MIN_TRACKS or n2 < MIN_TRACKS:
        await callback.message.answer(
            f"🤝 Пока мало данных: каждому нужно послушать через бота хотя бы {MIN_TRACKS} песен.\n"
            f"Сейчас: вы — {n1}, {name} — {n2}."
        )
        return

    s1, s2 = sum(a1.values()), sum(a2.values())
    overlap = sum(min(a1[a] / s1, a2[a] / s2) for a in a1.keys() & a2.keys())
    shared_tracks = [t1[t] for t in t1.keys() & t2.keys()]
    pct = round(100 * min(1.0, overlap + 0.03 * len(shared_tracks)))

    common = sorted(a1.keys() & a2.keys(), key=lambda a: -(a1[a] / s1 + a2[a] / s2))
    # Оригинальное написание имени исполнителя из истории
    pretty = {a.lower(): a for title in list(t1.values()) + list(t2.values())
              for a in (x.strip() for x in title.split(" — ")[0].split(","))}
    lines = [f"🤝 Вы и {name}: совместимость {pct}%", _verdict(pct)]
    if common:
        lines.append("\n🎤 Общие исполнители: " + ", ".join(pretty.get(a, a) for a in common[:5]))
    if shared_tracks:
        lines.append("🎵 Общие песни: " + "; ".join(shared_tracks[:3]))
    await callback.message.answer(
        "\n".join(lines),
        reply_markup=_kb([[InlineKeyboardButton(text="🌊 Волна на двоих", callback_data=f"duo:{fid}")]]),
    )


# ---------- Волна на двоих ----------

async def _top_seeds(user_id: int, n: int = 2) -> list[str]:
    seeds, seen = [], set()
    for source in ({"$ne": "wave"}, "wave"):
        cursor = db.music_db.history.find({"user_id": user_id, "source": source}).sort("listened_at", -1)
        async for h in cursor:
            tid = h["track_id"].split("_")[0]
            if tid not in seen:
                seen.add(tid)
                seeds.append(f"track:{tid}")
            if len(seeds) >= n:
                return seeds
    return seeds


@router.callback_query(F.data.regexp(r"^duo2?:\d+$"))
async def duo_wave(callback: CallbackQuery) -> None:
    kind, fid = callback.data.split(":")
    me, fid = callback.from_user.id, int(fid)
    if not await are_friends(me, fid):
        await callback.answer("Волна на двоих доступна только с друзьями.", show_alert=True)
        return
    mine, theirs = await _top_seeds(me), await _top_seeds(fid)
    if not mine and not theirs:
        await callback.answer("Послушайте хотя бы пару песен через бота — и волна соберётся.", show_alert=True)
        return
    seeds = [x for pair in zip(mine + [None] * 2, theirs + [None] * 2) for x in pair if x][:4]
    await callback.answer("Собираю волну на двоих…")
    await _send_wave(callback.bot, callback.message.chat.id, me, continue_wave=False,
                     duo=(seeds, f"на двоих: вы и {await _short_name(fid)}"))

    # Сообщаем другу (только если волну включили сами, а не в ответ — и не чаще раза в час)
    key = (me, fid)
    if kind == "duo" and time.monotonic() - _notified.get(key, -1e9) > 3600:
        _notified[key] = time.monotonic()
        try:
            await callback.bot.send_message(
                fid,
                f"🌊 {await display_name(me)} слушает волну на двоих с вами — по вашим общим вкусам.",
                reply_markup=_kb([[InlineKeyboardButton(text="🌊 Включить у себя", callback_data=f"duo2:{me}")]]),
            )
        except Exception:
            pass
