"""
Лимит песен в день и подписка «⭐ Премиум» за Telegram Stars.

Пока DAILY_SONG_LIMIT=0 (по умолчанию), лимита нет и Премиум не продаётся —
бот полностью бесплатный. Чтобы включить: DAILY_SONG_LIMIT=50 в .env и перезапуск.

Без лимита всегда: подписчики Премиума, ADMIN_IDS и те, кто подключил
свой Яндекс с Плюсом (они скачивают через свою подписку).
"""
import logging
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from app.core.config import settings
from app.core.database import db

logger = logging.getLogger(__name__)
router = Router(name="premium")

PAYLOAD = "premium_month"
PERIOD = 30 * 24 * 3600  # Telegram поддерживает подписки ровно на 30 дней


def limit_enabled() -> bool:
    return settings.daily_song_limit > 0


def _admins() -> set[int]:
    return {int(x) for x in settings.admin_ids.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}


def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


async def is_premium(user_id: int) -> bool:
    u = await db.music_db.users.find_one({"id": user_id}) or {}
    until = u.get("premium_until")
    return bool(until and until > datetime.utcnow())


async def is_unlimited(user_id: int) -> bool:
    if not limit_enabled() or user_id in _admins():
        return True
    u = await db.music_db.users.find_one({"id": user_id}) or {}
    if u.get("yandex_token") and u.get("yandex_plus"):
        return True
    until = u.get("premium_until")
    return bool(until and until > datetime.utcnow())


async def songs_left(user_id: int) -> int | None:
    """Сколько песен ещё можно сегодня; None — без ограничений."""
    if await is_unlimited(user_id):
        return None
    doc = await db.music_db.usage.find_one({"user_id": user_id, "day": _today()}) or {}
    return max(0, settings.daily_song_limit - doc.get("count", 0))


async def count_songs(user_id: int, n: int = 1) -> None:
    if not limit_enabled() or n <= 0:
        return
    await db.music_db.usage.update_one(
        {"user_id": user_id, "day": _today()},
        {"$inc": {"count": n}, "$set": {"user_id": user_id, "day": _today()}},
        upsert=True,
    )


def _songs(n: int) -> str:
    word = ("песня" if n % 10 == 1 and n % 100 != 11 else
            "песни" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "песен")
    return f"{n} {word}"


def limit_text() -> str:
    return (
        f"😔 На сегодня лимит — {_songs(settings.daily_song_limit)}. Завтра снова можно слушать.\n\n"
        f"Хотите без ограничений?\n"
        f"⭐ Премиум — {settings.premium_stars} звёзд в месяц\n"
        "🔗 или подключите свой Яндекс с Плюсом — /yandex"
    )


def limit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"⭐ Премиум — {settings.premium_stars} ⭐/мес", callback_data="prem:buy")
    ]])


async def send_limit_message(bot, chat_id: int) -> None:
    await bot.send_message(chat_id, limit_text(), reply_markup=limit_keyboard())


# ---------- Покупка ----------

async def _status_text(user_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    u = await db.music_db.users.find_one({"id": user_id}) or {}
    until = u.get("premium_until")
    if until and until > datetime.utcnow():
        text = (f"⭐ Премиум активен до {until:%d.%m.%Y}.\n"
                + ("Продление отключено — после этой даты подписка закончится."
                   if u.get("premium_canceled") else
                   f"Продлевается автоматически каждые 30 дней за {settings.premium_stars} ⭐."))
        kb = None
        if u.get("premium_charge_id") and not u.get("premium_canceled"):
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Отключить продление", callback_data="prem:cancel")]])
        elif u.get("premium_charge_id"):
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Включить продление снова", callback_data="prem:renew")]])
        return text, kb
    if not limit_enabled():
        return "🎉 Сейчас бот полностью бесплатный — слушайте сколько угодно, Премиум не нужен.", None
    left = await songs_left(user_id)
    return (
        f"⭐ Премиум — песни без ограничений.\n"
        f"Цена: {settings.premium_stars} звёзд за 30 дней, продлевается автоматически, отключить можно в любой момент.\n\n"
        + (f"Сегодня осталось бесплатных песен: {left} из {settings.daily_song_limit}." if left is not None
           else "У вас и так нет ограничений."),
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"Оформить за {settings.premium_stars} ⭐", callback_data="prem:buy")]]),
    )


@router.message(Command("premium"))
async def cmd_premium(message: Message) -> None:
    text, kb = await _status_text(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "prem:buy")
async def buy(callback: CallbackQuery) -> None:
    if not limit_enabled():
        await callback.answer("Сейчас бот полностью бесплатный — Премиум не нужен 🎉", show_alert=True)
        return
    if await is_premium(callback.from_user.id):
        await callback.answer("У вас уже есть Премиум ⭐", show_alert=True)
        return
    link = await callback.bot.create_invoice_link(
        title="⭐ Премиум на месяц",
        description="Песни без ограничений. Продлевается каждые 30 дней, отключить можно в любой момент.",
        payload=PAYLOAD,
        currency="XTR",  # Telegram Stars
        prices=[LabeledPrice(label="Премиум на 30 дней", amount=settings.premium_stars)],
        subscription_period=PERIOD,
    )
    await callback.message.answer(
        f"⭐ Премиум: {settings.premium_stars} звёзд за 30 дней.\nНажмите кнопку, чтобы оплатить звёздами Telegram:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"Оплатить {settings.premium_stars} ⭐", url=link)]]),
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    ok = query.invoice_payload == PAYLOAD and query.currency == "XTR"
    await query.answer(ok=ok, error_message=None if ok else "Этот счёт устарел, оформите заново через /premium.")


@router.message(F.successful_payment)
async def paid(message: Message) -> None:
    sp = message.successful_payment
    if sp.invoice_payload != PAYLOAD:
        return
    user_id = message.from_user.id
    until = (datetime.utcfromtimestamp(sp.subscription_expiration_date) if sp.subscription_expiration_date
             else datetime.utcnow() + timedelta(seconds=PERIOD))
    await db.music_db.users.update_one(
        {"id": user_id},
        {"$set": {"id": user_id, "premium_until": until, "premium_charge_id": sp.telegram_payment_charge_id,
                  "premium_canceled": False}},
        upsert=True,
    )
    await db.music_db.payments.insert_one({
        "user_id": user_id, "stars": sp.total_amount, "charge_id": sp.telegram_payment_charge_id,
        "recurring": bool(sp.is_recurring), "first": bool(sp.is_first_recurring), "until": until,
        "at": datetime.utcnow(),
    })
    renewal = sp.is_recurring and not sp.is_first_recurring
    await message.answer(
        (f"⭐ Премиум продлён до {until:%d.%m.%Y}. Спасибо, что вы с нами!" if renewal else
         f"🎉 Спасибо! Премиум активен до {until:%d.%m.%Y} — слушайте без ограничений.\n"
         "Подписка продлевается автоматически, управлять ей — /premium.")
    )


async def _set_renewal(callback: CallbackQuery, canceled: bool) -> None:
    u = await db.music_db.users.find_one({"id": callback.from_user.id}) or {}
    if not u.get("premium_charge_id"):
        await callback.answer()
        return
    try:
        await callback.bot.edit_user_star_subscription(
            user_id=callback.from_user.id, telegram_payment_charge_id=u["premium_charge_id"], is_canceled=canceled,
        )
    except Exception as e:
        logger.warning("edit_user_star_subscription failed: %s", e)
        await callback.answer("Не получилось. Управлять подпиской можно в Telegram: Настройки → Мои звёзды.",
                              show_alert=True)
        return
    await db.music_db.users.update_one({"id": callback.from_user.id}, {"$set": {"premium_canceled": canceled}})
    await callback.answer("Продление отключено" if canceled else "Продление снова включено")
    text, kb = await _status_text(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data == "prem:cancel")
async def cancel_renewal(callback: CallbackQuery) -> None:
    await _set_renewal(callback, True)


@router.callback_query(F.data == "prem:renew")
async def renew(callback: CallbackQuery) -> None:
    await _set_renewal(callback, False)


@router.message(Command("paysupport"))
async def paysupport(message: Message) -> None:
    # Telegram требует от ботов с оплатой звёздами команду поддержки по платежам
    contact = settings.support_contact or "владельцу бота"
    await message.answer(
        "💬 Вопросы об оплате и возврате звёзд: напишите " + contact + ".\n"
        "Управлять подпиской можно командой /premium или в Telegram: Настройки → Мои звёзды."
    )
