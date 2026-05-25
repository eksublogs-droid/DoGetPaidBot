"""
Handles: user profile, transaction history, daily check-in, leaderboard.
"""
import logging
from datetime import datetime, timedelta
import random

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command

from models.db import (
    get_user, get_settings, get_user_transactions, get_user_withdrawals,
    get_last_checkin, record_checkin, update_user_balance, log_transaction,
    get_referral_leaderboard
)
from utils.keyboards import back_to_dashboard, history_keyboard

logger = logging.getLogger(__name__)
router = Router()


# ─────────────────────────────────────────
# PROFILE
# ─────────────────────────────────────────

@router.callback_query(F.data == "show_profile")
@router.message(Command("profile"))
async def show_profile(event, db):
    user_id = event.from_user.id
    answer_fn = event.message.answer if isinstance(event, CallbackQuery) else event.answer
    if isinstance(event, CallbackQuery):
        await event.answer()

    user = await get_user(db, user_id)
    if not user:
        await answer_fn("❌ Profile not found. Type /start to register.")
        return

    joined = user.get("joined_at", datetime.utcnow())
    joined_str = joined.strftime("%b %d, %Y") if hasattr(joined, "strftime") else str(joined)
    username = event.from_user.first_name or user.get("username", "User")

    balance = user.get("balance", 0)
    referral_count = user.get("referral_count", 0)
    tasks_done = user.get("tasks_done", 0)
    total_withdrawn = user.get("total_withdrawn", 0.0)
    bank_info = (
        f"{user['bank_name']} — {user['bank_account']}"
        if user.get("bank_account") else "Not set"
    )
    notif_status = "🔔 On" if user.get("notifications_on", True) else "🔕 Off"

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    toggle_notif_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Toggle Notifications", callback_data="toggle_notifications")],
        [InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="dashboard")],
    ])

    await answer_fn(
        f"👤 *Your Profile*\n\n"
        f"Name: *{username}*\n"
        f"Joined: {joined_str}\n\n"
        f"💰 Balance: ₦{balance:,.0f}\n"
        f"🏆 Tasks Completed: {tasks_done}\n"
        f"👥 Referrals: {referral_count}\n"
        f"💸 Total Withdrawn: ₦{total_withdrawn:,.0f}\n\n"
        f"🏦 Bank Account: {bank_info}\n"
        f"Notifications: {notif_status}",
        reply_markup=toggle_notif_kb,
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "toggle_notifications")
async def toggle_notifications(callback: CallbackQuery, db):
    await callback.answer()
    user_id = callback.from_user.id
    user = await get_user(db, user_id)
    if not user:
        return
    current = user.get("notifications_on", True)
    new_val = not current
    await db.users.update_one({"telegram_id": user_id}, {"$set": {"notifications_on": new_val}})
    status = "🔔 Notifications turned *ON*." if new_val else "🔕 Notifications turned *OFF*."
    await callback.message.answer(status, reply_markup=back_to_dashboard(), parse_mode="Markdown")


# ─────────────────────────────────────────
# TRANSACTION HISTORY
# ─────────────────────────────────────────

@router.callback_query(F.data == "show_history")
async def show_history_menu(callback: CallbackQuery, db):
    await callback.answer()
    await callback.message.answer(
        "📜 *Earnings & History*\n\nWhat would you like to view?",
        reply_markup=history_keyboard(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "history_transactions")
async def show_transactions(callback: CallbackQuery, db):
    await callback.answer()
    user_id = callback.from_user.id
    txns = await get_user_transactions(db, user_id, limit=15)

    if not txns:
        await callback.message.answer(
            "📜 No transaction history yet.",
            reply_markup=back_to_dashboard()
        )
        return

    type_emoji = {
        "task_reward": "✅",
        "referral_bonus": "👥",
        "withdrawal_bank": "🏦",
        "withdrawal_airtime": "📱",
        "admin_credit": "⬆️",
        "admin_deduct": "⬇️",
    }
    lines = ["📜 *Transaction History* (last 15)\n"]
    for tx in txns:
        emoji = type_emoji.get(tx["type"], "•")
        date = tx["created_at"].strftime("%b %d") if hasattr(tx.get("created_at"), "strftime") else "—"
        amount = tx.get("amount", 0)
        sign = "+" if amount >= 0 else ""
        status_label = f" [{tx.get('status', 'confirmed').upper()}]" if tx.get("status") == "pending" else ""
        lines.append(f"{emoji} {sign}₦{abs(amount):,.0f} — {tx.get('description', tx['type'])} ({date}){status_label}")

    await callback.message.answer(
        "\n".join(lines),
        reply_markup=back_to_dashboard(),
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# DAILY CHECK-IN
# ─────────────────────────────────────────

@router.callback_query(F.data == "daily_checkin")
@router.message(Command("checkin"))
async def daily_checkin(event, db, bot: Bot):
    user_id = event.from_user.id
    answer_fn = event.message.answer if isinstance(event, CallbackQuery) else event.answer
    if isinstance(event, CallbackQuery):
        await event.answer()

    user = await get_user(db, user_id)
    if not user or not user.get("onboarded"):
        await answer_fn("❌ Complete onboarding first.")
        return

    checkin = await get_last_checkin(db, user_id)
    now = datetime.utcnow()

    if checkin:
        last = checkin.get("last_checkin")
        if last:
            last_naive = last.replace(tzinfo=None) if hasattr(last, "tzinfo") and last.tzinfo else last
            if (now - last_naive).total_seconds() < 86400:
                next_checkin = last_naive + timedelta(hours=24)
                hours_left = max(0, int((next_checkin - now).total_seconds() // 3600))
                mins_left = max(0, int(((next_checkin - now).total_seconds() % 3600) // 60))
                await answer_fn(
                    f"⏳ *Already checked in today!*\n\n"
                    f"Come back in *{hours_left}h {mins_left}m* for your next reward.",
                    reply_markup=back_to_dashboard(),
                    parse_mode="Markdown"
                )
                return

    bot_settings = await get_settings(db)
    min_reward = bot_settings.get("checkin_min_reward", 20)
    max_reward = bot_settings.get("checkin_max_reward", 100)
    reward = random.randint(int(min_reward), int(max_reward))

    await record_checkin(db, user_id, reward)
    await update_user_balance(db, user_id, reward)
    await log_transaction(db, user_id, "task_reward", reward, "Daily check-in reward")

    streak = (checkin.get("streak", 0) + 1) if checkin else 1
    streak_bonus = ""
    if streak >= 7:
        streak_bonus = "\n🔥 *7-day streak bonus applied!*"

    await answer_fn(
        f"🎁 *Daily Check-In Reward!*\n\n"
        f"You earned *₦{reward:,.0f}*!\n"
        f"Streak: {streak} day(s){streak_bonus}\n\n"
        f"Come back tomorrow for another reward.",
        reply_markup=back_to_dashboard(),
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# LEADERBOARD
# ─────────────────────────────────────────

@router.callback_query(F.data == "show_leaderboard")
@router.message(Command("leaderboard"))
async def show_leaderboard(event, db):
    user_id = event.from_user.id
    answer_fn = event.message.answer if isinstance(event, CallbackQuery) else event.answer
    if isinstance(event, CallbackQuery):
        await event.answer()

    top = await get_referral_leaderboard(db, limit=10)
    if not top:
        await answer_fn(
            "📊 No referrals yet — be the first to share your link!",
            reply_markup=back_to_dashboard()
        )
        return

    bot_settings = await get_settings(db)
    prize = bot_settings.get("referral_contest_prize", "₦5,000 airtime")

    lines = [
        f"🏆 *Referral Leaderboard — Top 10*\n",
        f"🎁 *Monthly Prize:* {prize}\n",
    ]
    for i, u in enumerate(top, 1):
        uname = f"@{u['username']}" if u.get("username") else f"User {u['telegram_id']}"
        marker = " 👈 you" if u["telegram_id"] == user_id else ""
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        lines.append(f"{medal} {uname} — {u['referral_count']} referral(s){marker}")

    await answer_fn(
        "\n".join(lines),
        reply_markup=back_to_dashboard(),
        parse_mode="Markdown"
    )
