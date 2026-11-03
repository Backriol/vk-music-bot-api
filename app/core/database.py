from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings

class Database:
    client: AsyncIOMotorClient = None
    music_db = None

db = Database()

async def connect_to_mongo():
    if settings.mongo_url:
        db.client = AsyncIOMotorClient(settings.mongo_url)
        print("✅ Connected to MongoDB")
    else:
        # Без MONGO_URL работаем с базой в памяти — удобно для локального запуска
        from mongomock_motor import AsyncMongoMockClient
        db.client = AsyncMongoMockClient()
        print("⚠️  MONGO_URL не задан: используется временная база в памяти")
    db.music_db = db.client[settings.db_name]

async def close_mongo_connection():
    db.client.close()
    print("❌ Closed MongoDB connection")
