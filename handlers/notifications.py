"""
Notification utilities — called by scheduler and other handlers.
NOT a router file (no aiogram handlers). Import functions directly.
"""
import logging
from datetime import datetime, timedelta
from aiogram import Bot

logger = logging.getLogger(__name__)


async def notify_all_users(bot: Bot, db, text: str, parse_mode: str = "Markdown",
                            only_notifications_on: bool = True):
    """Send a message to all onboarded, non-banned users."""
    query = {"onboarded": True, "banned": False}
    if only_notifications_on:
        query["notifications_on"] = True

    users = await db.users.find(query, {"telegram_id": 1}).to_list(length=None)
    sent = 0
    failed = 0
    for u in users:
        try:
            await bot.send_message(u["telegram_id"], text, parse_mode=parse_mode)
            sent += 1
        except Exception as e:
            failed += 1
            logger.debug(f"Could not send to {u['telegram_id']}: {e}")
    logger.info(f"Broadcast done: {sent} sent, {failed} failed")
    return sent, failed


async def send_new_task_alert(bot: Bot, db, task: dict):
    """Notify all users of a new task going live."""
    reward = task.get("reward", 0)
    title = task.get("title", "New Task")
    slots = task.get("slots")
    slot_info = f" (Only {slots} slots!)" if slots else ""
    text = (
        f"🆕 *New Task Available!*\n\n"
        f"📋 {title}\n"
        f"💰 Reward: ₦{reward:,.0f}{slot_info}\n\n"
        f"Open the bot to complete it → /menu"
    )
    sent, failed = await notify_all_users(bot, db, text)
    return sent, failed


async def send_earnings_summary(bot: Bot, db, period: str = "weekly"):
    """Send each user their earnings summary for the period."""
    from datetime import timezone
    cutoff_days = 7 if period == "weekly" else 1
    since = datetime.utcnow() - timedelta(days=cutoff_days)
    period_label = "This Week" if period == "weekly" else "Today"

    users = await db.users.find({"onboarded": True, "banned": False, "notifications_on": True},
                                 {"telegram_id": 1}).to_list(length=None)

    for u in users:
        uid = u["telegram_id"]
        try:
            txns = await db.transactions.find(
                {"user_id": uid, "created_at": {"$gte": since}, "amount": {"$gt": 0}}
            ).to_list(length=None)
            if not txns:
                continue
            total_earned = sum(t.get("amount", 0) for t in txns)
            user = await db.users.find_one({"telegram_id": uid})
            balance = user.get("balance", 0) if user else 0
            await bot.send_message(
                uid,
                f"📊 *{period_label}'s Summary*\n\n"
                f"💰 Earned: ₦{total_earned:,.0f}\n"
                f"🏦 Current Balance: ₦{balance:,.0f}\n\n"
                f"Keep going! 🚀 /menu",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.debug(f"Summary failed for {uid}: {e}")


async def send_task_reminder(bot: Bot, db):
    """Remind users who have a pending task submission but haven't completed in 1 hour."""
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    pending = await db.completions.find(
        {"status": "pending", "submitted_at": {"$lt": one_hour_ago}}
    ).to_list(length=None)

    notified = set()
    for c in pending:
        uid = c["user_id"]
        if uid in notified:
            continue
        notified.add(uid)
        user = await db.users.find_one({"telegram_id": uid, "notifications_on": True})
        if not user:
            continue
        try:
            await bot.send_message(
                uid,
                f"⏰ *Reminder!*\n\n"
                f"You have a pending task submission under review.\n"
                f"Open the bot to check your status: /menu",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.debug(f"Reminder failed for {uid}: {e}")
