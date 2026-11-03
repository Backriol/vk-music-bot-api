"""
Получение токена Яндекс Музыки без ввода пароля в программу.

Запуск:  python get_yandex_token.py

Программа покажет код. Его нужно ввести на странице Яндекса (ya.ru/device)
в браузере, где вы вошли в свой аккаунт. Пароль программа не видит.
"""
import asyncio
import webbrowser

from yandex_music import ClientAsync


def _show_code(code) -> None:
    print("\n" + "=" * 60)
    print(f"  Откройте в браузере:  {code.verification_url}")
    print(f"  и введите код:        {code.user_code}")
    print("=" * 60)
    print("Жду подтверждения (окно браузера должно открыться само)...")
    try:
        webbrowser.open(code.verification_url)
    except Exception:
        pass


async def _obtain():
    client = ClientAsync()
    token = await client.device_auth(on_code=_show_code)
    client = await ClientAsync(token.access_token).init()
    has_plus = bool(client.me and client.me.plus and client.me.plus.has_plus)
    return token.access_token, has_plus


def obtain_yandex_token():
    """Возвращает токен или None."""
    print("Вход в Яндекс Музыку")
    try:
        token, has_plus = asyncio.run(_obtain())
    except Exception as e:
        print(f"\n[!] Не получилось войти в Яндекс: {e}")
        return None
    if not has_plus:
        print("\n[!] У аккаунта нет подписки Яндекс Плюс: бот сможет присылать")
        print("    только 30-секундные отрывки песен.")
    return token


def main():
    token = obtain_yandex_token()
    if not token:
        print("Покажите текст выше Claude — разберёмся.")
        return
    print("\n[OK] Готово! Вставьте эту строку в файл .env:\n")
    print(f"YANDEX_TOKEN={token}")
    print("\nНикому не показывайте токен — это доступ к вашему аккаунту Яндекса.")


if __name__ == "__main__":
    main()
