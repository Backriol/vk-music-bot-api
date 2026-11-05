"""
Личные аккаунты Яндекс Музыки пользователей бота.

Кто подключил свой Яндекс — получает свою настоящую «Мою волну», а если у него
есть Плюс, песни скачиваются через его подписку. Остальные пользуются основным
аккаунтом бота: волна строится по их истории в боте в режиме инкогнито,
чтобы не портить рекомендации владельцу.
"""
import asyncio
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.core.database import db
from app.services.source import music_service

router = Router(name="accounts")

# Опросы подтверждения входа, которые идут прямо сейчас: user_id -> задача
_pending: dict[int, asyncio.Task] = {}


def yandex_enabled() -> bool:
    return hasattr(music_service, "account_info")


async def user_tokens(user_id: int) -> tuple[Optional[str], Optional[str]]:
    """
    (токен для волны, токен для скачивания) пользователя.
    None — использовать основной аккаунт бота.
    """
    u = await db.music_db.users.find_one({"id": user_id}) or {}
    token = u.get("yandex_token")
    if not token:
        return None, None
    return token, (token if u.get("yandex_plus") else None)


def connect_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="🔗 Подключить свой Яндекс", callback_data="ya:connect")


ABOUT = (
    "Бот работает и без этого: искать, слушать и отправлять песни можно сразу, "
    "своя подписка Яндекса не нужна.\n\n"
    "Подключение нужно только для одного — чтобы «🌊 Моя волна» собиралась "
    "из вашего аккаунта Яндекса и знала ваш вкус: любимых исполнителей, лайки, историю.\n\n"
    "• Пароль вводить не нужно — вход по коду на странице Яндекса.\n"
    "• Есть Яндекс Плюс — песни будут скачиваться через вашу подписку. Нет — ничего страшного, "
    "волна всё равно будет ваша.\n"
    "• Отключить можно в любой момент командой /yandex."
)


async def _status(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    u = await db.music_db.users.find_one({"id": user_id}) or {}
    if u.get("yandex_token"):
        plus = "есть ✅" if u.get("yandex_plus") else "нет — песни скачиваются через основной аккаунт бота"
        text = (
            f"🔗 Подключён ваш Яндекс: {u.get('yandex_login') or 'аккаунт'}\n"
            f"Подписка Плюс: {plus}\n\n"
            "«🌊 Моя волна» собирается из вашей собственной волны Яндекса.\n"
            "Если отключить Яндекс, бот продолжит работать — волна будет подбираться "
            "по песням, которые вы слушали в боте."
        )
        kb = [[InlineKeyboardButton(text="❌ Отключить Яндекс", callback_data="ya:off")]]
    else:
        text = (
            "🔗 Подключение Яндекс Музыки — по желанию\n\n" + ABOUT + "\n\n"
            "Сейчас волна подбирается по песням, которые вы слушали в боте."
        )
        kb = [[connect_button()]]
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


@router.message(Command("yandex"))
async def cmd_yandex(message: Message) -> None:
    if not yandex_enabled():
        await message.answer("Бот сейчас работает без Яндекс Музыки.")
        return
    text, kb = await _status(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "ya:connect")
async def connect(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    if not yandex_enabled():
        await callback.answer("Бот сейчас работает без Яндекс Музыки.", show_alert=True)
        return
    old = _pending.pop(user_id, None)
    if old:
        old.cancel()

    from yandex_music import ClientAsync

    try:
        code = await ClientAsync().request_device_code(device_name="Telegram music bot")
    except Exception as e:
        await callback.answer()
        await callback.message.answer(f"⚠️ Яндекс не ответил: {e}. Попробуйте позже.")
        return

    await callback.answer()
    await callback.message.answer(
        "🔗 Подключение Яндекс Музыки\n"
        "Подписка Плюс не обязательна — нужен только аккаунт Яндекса.\n\n"
        "1. Нажмите кнопку ниже — откроется страница Яндекса.\n"
        "2. Войдите в свой аккаунт, если попросит.\n"
        f"3. Введите код:  {code.user_code}\n"
        "4. Нажмите «Разрешить».\n\n"
        f"Код действует {max(1, code.expires_in // 60)} мин. Я сам замечу, когда вы подтвердите вход.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Открыть страницу Яндекса", url=code.verification_url)],
            [InlineKeyboardButton(text="Отмена", callback_data="ya:cancel")],
        ]),
    )
    _pending[user_id] = asyncio.create_task(
        _wait_for_confirmation(callback.bot, callback.message.chat.id, user_id, code)
    )


async def _wait_for_confirmation(bot, chat_id: int, user_id: int, code) -> None:
    from yandex_music import ClientAsync

    client = ClientAsync()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + code.expires_in
    try:
        while loop.time() < deadline:
            await asyncio.sleep(max(code.interval, 2))
            try:
                token = await client.poll_device_token(code.device_code)
            except Exception as e:  # отказ, истёкший код и т.п.
                await bot.send_message(chat_id, f"⚠️ Подключение не удалось: {e}")
                return
            if token is None:
                continue

            info = await music_service.account_info(token.access_token)
            await db.music_db.users.update_one(
                {"id": user_id},
                {"$set": {"id": user_id, "yandex_token": token.access_token,
                          "yandex_plus": info["has_plus"], "yandex_login": info["login"]}},
                upsert=True,
            )
            # Следующая волна начнётся заново — уже по вашему аккаунту
            await db.music_db.waves.update_one({"chat_id": chat_id}, {"$unset": {"session_id": ""}})
            note = "" if info["has_plus"] else (
                "\n\nℹ️ У этого аккаунта нет Плюса, поэтому песни будут скачиваться "
                "через основной аккаунт бота. Волна всё равно будет ваша."
            )
            await bot.send_message(
                chat_id,
                f"✅ Яндекс подключён: {info['login'] or 'аккаунт'}!\n"
                f"Нажмите «🌊 Моя волна» — теперь это ваша собственная волна.{note}",
            )
            return
        await bot.send_message(chat_id, "⌛ Код истёк. Нажмите «🔗 Подключить свой Яндекс» ещё раз.")
    except asyncio.CancelledError:
        pass
    finally:
        if _pending.get(user_id) is asyncio.current_task():
            _pending.pop(user_id, None)


@router.callback_query(F.data == "ya:cancel")
async def cancel(callback: CallbackQuery) -> None:
    task = _pending.pop(callback.from_user.id, None)
    if task:
        task.cancel()
    await callback.message.edit_text("Подключение Яндекса отменено.")
    await callback.answer()


@router.callback_query(F.data == "ya:off")
async def disconnect(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    u = await db.music_db.users.find_one({"id": user_id}) or {}
    if u.get("yandex_token") and hasattr(music_service, "forget"):
        music_service.forget(u["yandex_token"])
    await db.music_db.users.update_one(
        {"id": user_id}, {"$unset": {"yandex_token": "", "yandex_plus": "", "yandex_login": ""}}
    )
    await db.music_db.waves.update_one({"chat_id": callback.message.chat.id}, {"$unset": {"session_id": ""}})
    await callback.answer("Яндекс отключён")
    text, kb = await _status(user_id)
    await callback.message.edit_text(text, reply_markup=kb)
