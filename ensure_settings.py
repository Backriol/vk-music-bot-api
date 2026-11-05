"""
Дополняет уже существующий файл .env новыми настройками, если их там нет,
задавая вопросы. Запускается из start.bat при каждом старте — вручную
редактировать .env не нужно.
"""
from pathlib import Path

ENV = Path(__file__).with_name(".env")


def read_keys(text: str) -> set[str]:
    keys = set()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.add(line.split("=", 1)[0].strip().upper())
    return keys


def main():
    if not ENV.exists():
        return
    text = ENV.read_text(encoding="utf-8")
    keys = read_keys(text)
    added = []

    if "BOT_USERNAME" not in keys:
        print("\nНовая настройка: какое @имя бота показывать в кнопках под песнями")
        print("и в ссылках для друзей (если у бота несколько имён).")
        name = input("Имя бота, например YanMusicapp (или Enter — оставить основное): ")
        name = name.strip().lstrip("@").strip()
        added.append(f"BOT_USERNAME={name}")

    if added:
        if text and not text.endswith("\n"):
            text += "\n"
        ENV.write_text(text + "\n".join(added) + "\n", encoding="utf-8")
        print("[OK] Настройки сохранены.\n")


if __name__ == "__main__":
    main()
