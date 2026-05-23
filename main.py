import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.mongo import MongoStorage
from motor.motor_asyncio import AsyncIOMotorClient

from config.settings import settings
from handlers import user, admin, tasks, withdrawal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    # MongoDB connection
    mongo_client = AsyncIOMotorClient(settings.MONGO_URI)
    db = mongo_client[settings.DB_NAME]

    # FSM storage using MongoDB
    storage = MongoStorage(mongo_client, db_name=settings.DB_NAME, collection_name="fsm_states")

    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher(storage=storage)

    # Pass db to all handlers via middleware data
    dp["db"] = db

    # Register routers
    dp.include_router(user.router)
    dp.include_router(tasks.router)
    dp.include_router(withdrawal.router)
    dp.include_router(admin.router)

    logger.info("Bot starting...")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
