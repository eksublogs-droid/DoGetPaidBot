import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.mongo import MongoStorage
from motor.motor_asyncio import AsyncIOMotorClient

from config.settings import settings
from handlers import user, admin, tasks, withdrawal
from utils.keyboards import main_menu_keyboard

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

    # Set bot commands
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="menu", description="Open dashboard menu"),
        BotCommand(command="balance", description="Check your balance"),
        BotCommand(command="referral", description="Get your referral link"),
        BotCommand(command="tasks", description="View available tasks"),
        BotCommand(command="withdraw", description="Withdraw your funds"),
        BotCommand(command="history", description="View withdrawal history"),
        BotCommand(command="removemyaccount", description="Delete your account"),
    ])

    logger.info("Bot starting...")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
