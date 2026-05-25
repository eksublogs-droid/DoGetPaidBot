import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.mongo import MongoStorage
from motor.motor_asyncio import AsyncIOMotorClient

from config.settings import settings
from handlers import user, admin, tasks, withdrawal
from handlers import ads, profile
from utils.keyboards import main_menu_keyboard
from scheduler import start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    mongo_client = AsyncIOMotorClient(settings.MONGO_URI)
    db = mongo_client[settings.DB_NAME]

    storage = MongoStorage(mongo_client, db_name=settings.DB_NAME, collection_name="fsm_states")

    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher(storage=storage)

    dp["db"] = db

    # Register routers — ORDER MATTERS
    # user first (handles /start, dashboard, captcha)
    dp.include_router(user.router)
    # profile (show_profile, show_history, daily_checkin, leaderboard)
    dp.include_router(profile.router)
    # tasks (show_tasks, task_done, proof handling)
    dp.include_router(tasks.router)
    # withdrawal (set_bank, withdraw, airtime)
    dp.include_router(withdrawal.router)
    # ads (/runad, ad flow, admin ad review)
    dp.include_router(ads.router)
    # admin last (all /admin commands)
    dp.include_router(admin.router)

    # Bot commands
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="menu",             description="Open dashboard menu"),
        BotCommand(command="balance",          description="Check your balance"),
        BotCommand(command="referral",         description="Get your referral link"),
        BotCommand(command="leaderboard",      description="Top referrers"),
        BotCommand(command="checkin",          description="Daily check-in reward"),
        BotCommand(command="profile",          description="View your profile"),
        BotCommand(command="runad",            description="Place an ad"),
        BotCommand(command="myadsstatus",      description="View your active ads"),
        BotCommand(command="myid",             description="Get your Telegram ID"),
        BotCommand(command="removemyaccount",  description="Delete your account"),
    ])

    # Start background scheduler
    start_scheduler(bot, db)

    logger.info("Bot starting...")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
