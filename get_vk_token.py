"""
Получение токена ВКонтакте для бота по логину и паролю.

Запуск:  python get_vk_token.py

Логин и пароль отправляются только на oauth.vk.com и нигде не сохраняются.
В конце программа покажет две строки — их нужно вставить в файл .env.
"""
import getpass
import json
import urllib.error
import urllib.parse
import urllib.request

# Приложения, через которые VK выдаёт доступ к аудио
CLIENTS = [
    {
        "name": "VK для Android",
        "client_id": "2274003",
        "client_secret": "hHbZxrka2uZ6jB1inYsH",
        "user_agent": "VKAndroidApp/5.52-4543 (Android 5.1.1; SDK 22; x86_64; "
                      "unknown Android SDK built for x86_64; en; 320x240)",
    },
    {
        "name": "Kate Mobile",
        "client_id": "2685278",
        "client_secret": "lxhD8OD7dMsqtXIm5IUY",
        "user_agent": "KateMobileAndroid/56 lite-460 (Android 4.4.2; SDK 19; x86; "
                      "unknown Android SDK built for x86; en)",
    },
]


def vk_get(url: str, params: dict, user_agent: str) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:  # VK возвращает ошибки авторизации с кодом 401
        return json.loads(e.read().decode())


def get_token(client: dict, login: str, password: str):
    params = {
        "grant_type": "password",
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "username": login,
        "password": password,
        "v": "5.131",
        "2fa_supported": "1",
        "scope": "all",
    }
    for _ in range(5):
        data = vk_get("https://oauth.vk.com/token", params, client["user_agent"])
        if "access_token" in data:
            return data["access_token"]

        error = data.get("error")
        if error == "need_validation":
            print("\nВКонтакте прислал код подтверждения (SMS или в приложении).")
            params["code"] = input("Введите код: ").strip()
            continue
        if error == "need_captcha":
            print("\nВКонтакте просит ввести капчу. Откройте картинку в браузере:")
            print(data.get("captcha_img"))
            params["captcha_sid"] = data.get("captcha_sid")
            params["captcha_key"] = input("Символы с картинки: ").strip()
            continue

        print(f"  ✗ {client['name']}: {data.get('error_description') or error or data}")
        return None
    return None


def check_audio(token: str, user_agent: str) -> bool:
    data = vk_get(
        "https://api.vk.com/method/audio.search",
        {"access_token": token, "v": "5.131", "q": "Макс Корж", "count": 1},
        user_agent,
    )
    if "error" in data:
        print(f"  ✗ Токен получен, но музыка недоступна: {data['error'].get('error_msg')}")
        return False
    return True


def obtain_vk_token():
    """Спрашивает логин/пароль и возвращает (token, user_agent) или None."""
    print("Получение токена ВКонтакте\n")
    login = input("Логин ВК (телефон или почта): ").strip()
    password = getpass.getpass("Пароль ВК (при вводе не отображается): ")

    for client in CLIENTS:
        print(f"\nПробую через «{client['name']}»…")
        token = get_token(client, login, password)
        if token and check_audio(token, client["user_agent"]):
            return token, client["user_agent"]
    return None


def main():
    result = obtain_vk_token()
    if not result:
        print("\n❌ Не получилось получить токен. Скопируйте текст выше и покажите его Claude.")
        return
    token, user_agent = result
    print("\n✅ Готово! Вставьте эти две строки в файл .env (замените старые):\n")
    print(f"VK_TOKEN={token}")
    print(f"VK_USER_AGENT={user_agent}")
    print("\nНикому не показывайте токен — это доступ к вашему аккаунту ВК.")


if __name__ == "__main__":
    main()
