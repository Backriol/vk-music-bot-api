"""
Мастер настройки: спрашивает токены и создаёт файл .env.
Запускается автоматически из start.bat, если .env ещё нет.
"""
from pathlib import Path

from get_vk_token import CLIENTS, check_audio, obtain_vk_token

ENV = Path(__file__).with_name(".env")


def extract_token(text: str) -> str:
    """Принимает и сам токен, и всю ссылку из адресной строки."""
    if "access_token=" in text:
        text = text.split("access_token=", 1)[1]
    return text.split("&", 1)[0].strip()


def ask(prompt: str, required: bool = True) -> str:
    while True:
        value = input(prompt).strip().strip("'\"")
        if value or not required:
            return value
        print("  Это поле обязательно.")


def main():
    print("=" * 60)
    print(" Настройка музыкального бота")
    print("=" * 60)

    print("\n1) Токен Telegram-бота (его прислал @BotFather).")
    print("   Чтобы вставить в это окно — щёлкните ПРАВОЙ кнопкой мыши.")
    bot_token = ask("Токен бота: ")
    while ":" not in bot_token:
        print("  Похоже, это не токен. Он выглядит так: 1234567890:AAE...")
        bot_token = ask("Токен бота: ")

    print("\n2) Доступ к музыке ВКонтакте.")
    print("   Если у вас уже есть токен ВК (длинная строка vk1.a.... или из ссылки")
    print("   после access_token=) - вставьте его. Если нет - просто нажмите Enter,")
    print("   и программа получит его по логину и паролю.")
    pasted = ask("Готовый токен ВК (или Enter): ", required=False)
    result = None
    if pasted:
        token = extract_token(pasted)
        # Токен из браузера получен через Kate Mobile — подбираем подходящую «подпись»
        for client in reversed(CLIENTS):
            if check_audio(token, client["user_agent"]):
                result = (token, client["user_agent"])
                break
        if not result:
            print("\n[!] С этим токеном музыка не находится. Попробуем по логину и паролю.")
    while not result:
        result = obtain_vk_token()
        if not result:
            print("\n[!] Не получилось. Проверьте логин и пароль.")
            if input("Попробовать ещё раз? (да/нет): ").strip().lower() not in ("да", "д", "y", "yes"):
                print("Покажите текст выше Claude — разберёмся.")
                raise SystemExit(1)
    vk_token, vk_user_agent = result
    print("[OK] Доступ к музыке получен.")

    print("\n3) Ссылка на базу MongoDB (mongodb+srv://...).")
    print("   Если базы нет — просто нажмите Enter: история будет храниться")
    print("   только пока бот запущен.")
    mongo_url = ask("Ссылка на базу (или Enter): ", required=False)

    ENV.write_text(
        "\n".join([
            f"BOT_TOKEN={bot_token}",
            f"VK_TOKEN={vk_token}",
            f"VK_USER_AGENT={vk_user_agent}",
            f"MONGO_URL={mongo_url}",
            "DB_NAME=music_bot_db",
            "WEBAPP_URL=",
            "RUN_BOT=true",
            "DEBUG=false",
            "",
        ]),
        encoding="utf-8",
    )
    print(f"\n[OK] Настройки сохранены в файл {ENV.name}")
    print("   Чтобы настроить заново — удалите файл .env и запустите start.bat.\n")


if __name__ == "__main__":
    main()
