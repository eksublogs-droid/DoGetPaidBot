"""
Background scheduler — runs periodic jobs.
Call start_scheduler(bot, db) once from main.py after bot starts.
Uses asyncio tasks (no external library needed).
"""
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


async def _job_expire_ads(bot, db):
    """Mark expired ads as 'expired' every 30 minutes."""
    from models.db import expire_overdue_ads
    while True:
        try:
            count = await expire_overdue_ads(db)
            if count:
                logger.info(f"Expired {count} ad(s)")
        except Exception as e:
            logger.error(f"expire_ads error: {e}")
        await asyncio.sleep(1800)   # 30 minutes


async def _job_expire_tasks(bot, db):
    """Deactivate tasks whose deadline has passed every 15 minutes."""
    while True:
        try:
            now = datetime.utcnow()
            result = await db.tasks.update_many(
                {"active": True, "deadline": {"$lt": now, "$ne": None}},
                {"$set": {"active": False}}
            )
            if result.modified_count:
                logger.info(f"Expired {result.modified_count} task(s)")
        except Exception as e:
            logger.error(f"expire_tasks error: {e}")
        await asyncio.sleep(900)    # 15 minutes


async def _job_weekly_summary(bot, db):
    """Send weekly earnings summary every Sunday at 08:00 UTC."""
    from handlers.notifications import send_earnings_summary
    while True:
        try:
            now = datetime.utcnow()
            # Sunday = weekday 6
            days_until_sunday = (6 - now.weekday()) % 7
            next_sunday = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if days_until_sunday == 0 and now.hour >= 8:
                days_until_sunday = 7
            next_sunday = next_sunday.replace(day=now.day) if days_until_sunday == 0 else now
            # Simpler: just run every 7 days from start
            await asyncio.sleep(7 * 86400)
            await send_earnings_summary(bot, db, period="weekly")
            logger.info("Weekly summary sent")
        except Exception as e:
            logger.error(f"weekly_summary error: {e}")


async def _job_task_reminders(bot, db):
    """Send task reminders every 2 hours."""
    from handlers.notifications import send_task_reminder
    while True:
        try:
            await send_task_reminder(bot, db)
        except Exception as e:
            logger.error(f"task_reminder error: {e}")
        await asyncio.sleep(7200)   # 2 hours


async def _job_monthly_contest(bot, db):
    """Reset referral contest and notify winner on the 1st of each month at 09:00 UTC."""
    while True:
        try:
            now = datetime.utcnow()
            # Wait until next 1st of month 09:00
            if now.month == 12:
                next_month = now.replace(year=now.year + 1, month=1, day=1, hour=9, minute=0, second=0)
            else:
                next_month = now.replace(month=now.month + 1, day=1, hour=9, minute=0, second=0)
            wait_seconds = (next_month - now).total_seconds()
            await asyncio.sleep(max(wait_seconds, 60))

            # Find winner
            from models.db import get_contest_winner, get_settings
            winner = await get_contest_winner(db)
            contest = await db.contests.find_one({"_id": "referral_contest"})
            prize = contest.get("prize", "₦5,000 airtime") if contest else "₦5,000 airtime"

            if winner:
                winner_id = winner["telegram_id"]
                winner_name = f"@{winner['username']}" if winner.get("username") else f"User {winner_id}"
                try:
                    await bot.send_message(
                        winner_id,
                        f"🏆 *Congratulations!*\n\n"
                        f"You won the *Monthly Referral Contest!*\n"
                        f"Prize: *{prize}*\n\n"
                        f"An admin will contact you to claim your prize.",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

                # Notify admins
                from config.settings import settings
                for admin_id in settings.ADMIN_IDS:
                    try:
                        await bot.send_message(
                            admin_id,
                            f"🏆 *Monthly Contest Winner*\n\n"
                            f"Winner: {winner_name} (`{winner_id}`)\n"
                            f"Referrals: {winner['referral_count']}\n"
                            f"Prize: {prize}\n\n"
                            f"Please credit the prize manually.",
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass

                # Announce to all users
                from handlers.notifications import notify_all_users
                await notify_all_users(
                    bot, db,
                    f"🏆 *Monthly Referral Contest Results!*\n\n"
                    f"Winner: {winner_name} with {winner['referral_count']} referrals!\n"
                    f"Prize: {prize}\n\n"
                    f"New month, new contest! Share your referral link to win next month. /menu",
                    only_notifications_on=False
                )

            logger.info("Monthly contest processed")
        except Exception as e:
            logger.error(f"monthly_contest error: {e}")


def start_scheduler(bot, db):
    """Launch all background jobs. Call once from main.py."""
    loop = asyncio.get_event_loop()
    loop.create_task(_job_expire_ads(bot, db))
    loop.create_task(_job_expire_tasks(bot, db))
    loop.create_task(_job_task_reminders(bot, db))
    loop.create_task(_job_monthly_contest(bot, db))
    # Weekly summary disabled by default to avoid spamming on first boot;
    # uncomment to enable:
    # loop.create_task(_job_weekly_summary(bot, db))
    logger.info("Scheduler started (expire_ads, expire_tasks, task_reminders, monthly_contest)")
