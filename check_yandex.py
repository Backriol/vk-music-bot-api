"""
Проверка: отдаёт ли Яндекс Музыка песни с этого компьютера/сервера.

Запуск:  python check_yandex.py
Ищет популярную песню, получает ссылку на MP3 и скачивает первые байты.
Выход 0 — всё работает, 1 — есть проблема (текст объяснит какая).
"""
import asyncio
import sys

QUERY = "Анна Асти"


async def main() -> int:
    from app.core.config import settings

    if (settings.music_source or ("yandex" if settings.yandex_token else "vk")).lower() != "yandex":
        print("Источник музыки не Яндекс — проверка не нужна.")
        return 0
    if not settings.yandex_token:
        print("❌ В .env нет YANDEX_TOKEN.")
        return 1

    import aiohttp
    from yandex_music import ClientAsync

    try:
        client = await ClientAsync(settings.yandex_token).init()
    except Exception as e:
        print(f"❌ Яндекс Музыка не отвечает или токен недействителен: {e}")
        return 1
    me = client.me
    plus = bool(me and me.plus and me.plus.has_plus)
    print(f"✅ Вход в Яндекс Музыку: {(me.account.login if me and me.account else '') or 'ok'}"
          f" · Плюс: {'есть' if plus else 'нет (будут только отрывки)'}")

    try:
        result = await client.search(QUERY, type_="track")
        tracks = [t for t in (result.tracks.results if result and result.tracks else []) if t.available is not False]
    except Exception as e:
        print(f"❌ Поиск не работает: {e}")
        return 1
    if not tracks:
        print("❌ Поиск ничего не нашёл — похоже, Яндекс Музыка недоступна в регионе этого сервера.")
        return 1
    print("✅ Поиск работает")

    track = tracks[0]
    try:
        infos = await track.get_download_info_async(get_direct_links=True)
    except Exception as e:
        print(f"❌ Яндекс не даёт скачать песню: {e}")
        print("   Скорее всего, Яндекс Музыка недоступна в регионе этого сервера.")
        return 1
    mp3 = [i for i in infos or [] if i.codec == "mp3" and i.direct_link]
    if not mp3:
        print("❌ Яндекс не дал ссылку на MP3 — похоже, ограничение по региону.")
        return 1
    best = max(mp3, key=lambda i: (not i.preview, i.bitrate_in_kbps))

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(best.direct_link, headers={"Range": "bytes=0-65535"},
                                   timeout=aiohttp.ClientTimeout(total=30)) as resp:
                ok = resp.status in (200, 206) and len(await resp.read()) > 1000
    except Exception as e:
        print(f"❌ Файл песни не скачивается: {e}")
        return 1
    if not ok:
        print(f"❌ Файл песни не скачивается (ответ сервера {resp.status}) — похоже, ограничение по региону.")
        return 1

    kind = "целиком" if not best.preview else "только 30-секундный отрывок"
    print(f"✅ Песни скачиваются ({kind}, {best.bitrate_in_kbps} кбит/с). Всё готово!")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
