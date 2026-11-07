import asyncio
import os
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings

# Без MONGO_URL база живёт в памяти и сохраняется в этот файл (рядом с ботом)
DATA_FILE = Path(settings.data_file)
SAVE_EVERY = 30  # секунд

# «Оглавление» базы: без индексов поиск по истории и кешу тормозит при тысячах записей
INDEXES = {
    "users": [[("id", 1)], [("username", 1)]],
    "history": [[("user_id", 1), ("listened_at", -1)], [("user_id", 1), ("source", 1), ("listened_at", -1)]],
    "tg_audio": [[("track_id", 1), ("bot_id", 1)]],
    "sent_audio": [[("chat_id", 1), ("kind", 1)]],
    "waves": [[("chat_id", 1)]],
    "friends": [[("a", 1), ("b", 1)], [("b", 1)]],
    "friend_requests": [[("to", 1)], [("from", 1), ("to", 1)]],
    "reactions": [[("owner", 1), ("track_id", 1)]],
    "dedications": [[("id", 1)], [("from", 1), ("created", -1)]],
    "game_questions": [[("id", 1)]],
    "battles": [[("id", 1)]],
    "scores": [[("user_id", 1)]],
}


class Database:
    client: AsyncIOMotorClient = None
    music_db = None
    file_mode: bool = False
    saver: asyncio.Task = None


db = Database()


async def _dump() -> dict:
    data = {}
    for name in await db.music_db.list_collection_names():
        docs = await db.music_db[name].find({}).to_list(length=None)
        for d in docs:
            d.pop("_id", None)
        data[name] = docs
    return data


async def save_to_file() -> None:
    if not db.file_mode or db.music_db is None:
        return
    from bson import json_util  # входит в pymongo, понимает даты

    text = json_util.dumps(await _dump(), ensure_ascii=False)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, DATA_FILE)  # атомарно: файл не испортится, если бот закроют посреди записи


async def _load_from_file() -> int:
    if not DATA_FILE.exists():
        return 0
    from bson import json_util

    try:
        data = json_util.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        broken = DATA_FILE.with_suffix(".broken")
        os.replace(DATA_FILE, broken)
        print(f"⚠️  Файл данных повреждён ({e}), сохранён как {broken.name}. Начинаю с чистой базы.")
        return 0
    total = 0
    for name, docs in data.items():
        for d in docs:
            d.pop("_id", None)
        if docs:
            await db.music_db[name].insert_many(docs)
            total += len(docs)
    return total


async def _autosave() -> None:
    while True:
        await asyncio.sleep(SAVE_EVERY)
        try:
            await save_to_file()
        except Exception as e:
            print(f"⚠️  Не удалось сохранить данные: {e}")


async def _ensure_indexes() -> None:
    for collection, indexes in INDEXES.items():
        for keys in indexes:
            try:
                await db.music_db[collection].create_index(keys)
            except Exception as e:  # индекс — ускорение, а не необходимость
                print(f"⚠️  Индекс {collection} {keys}: {e}")


async def connect_to_mongo():
    if settings.mongo_url:
        db.client = AsyncIOMotorClient(settings.mongo_url, serverSelectionTimeoutMS=10000)
        db.music_db = db.client[settings.db_name]
        try:
            await db.client.admin.command("ping")
        except Exception as e:
            raise SystemExit(f"❌ Не удалось подключиться к MongoDB ({settings.mongo_url}): {e}")
        print("✅ Connected to MongoDB")
        # Переезд с файла: если база пустая, а рядом лежит bot_data.json — переносим всё в MongoDB
        if DATA_FILE.exists() and not await db.music_db.users.find_one({}):
            moved = await _load_from_file()
            if moved:
                os.replace(DATA_FILE, DATA_FILE.with_suffix(".imported"))
                print(f"📦 Данные из {DATA_FILE.name} перенесены в MongoDB (записей: {moved})")
        await _ensure_indexes()
        return

    from mongomock_motor import AsyncMongoMockClient
    db.client = AsyncMongoMockClient()
    db.music_db = db.client[settings.db_name]
    db.file_mode = True
    loaded = await _load_from_file()
    await _ensure_indexes()
    db.saver = asyncio.create_task(_autosave())
    print(f"💾 База без MongoDB: данные сохраняются в файл {DATA_FILE.name}"
          + (f" (загружено записей: {loaded})" if loaded else ""))


async def close_mongo_connection():
    if db.file_mode:
        if db.saver:
            db.saver.cancel()
        try:
            await save_to_file()
            print(f"💾 Данные сохранены в {DATA_FILE.name}")
        except Exception as e:
            print(f"⚠️  Не удалось сохранить данные: {e}")
    db.client.close()
    print("❌ Closed MongoDB connection")
