import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from models.db import (
    get_user, create_user, get_settings, mark_onboarded,
    update_user_balance, increment_referral_count,
    get_onboarding_tasks, get_user_completions, delete_user
)
from utils.keyboards import (
    welcome_keyboard, onboarding_keyboard,
    whatsapp_tasks_keyboard, dashboard_keyboard, back_to_dashboard,
    colourful_dashboard_keyboard, main_menu_keyboard, confirm_delete_keyboard
)
from config.settings import settings

logger = logging.getLogger(__name__)
router = Router()


# ─────────────────────────────────────────
# MEMBERSHIP RE-CHECK
# ─────────────────────────────────────────

async def verify_membership(user_id: int, bot: Bot, db) -> list:
    """
    Check if user is still in all onboarding channels.
    Returns list of channel titles they've left. Empty list = all good.
    """
    onboarding_tasks = await get_onboarding_tasks(db)
    channel_tasks = [t for t in onboarding_tasks if t["task_type"] == "join_channel"]
    not_in = []
    for task in channel_tasks:
        channel_id = task.get("channel_id")
        if channel_id:
            try:
                member = await bot.get_chat_member(channel_id, user_id)
                if member.status in ["left", "kicked", "banned"]:
                    not_in.append(task["title"])
            except Exception as e:
                logger.warning(f"Could not check channel {channel_id}: {e}")
    return not_in


async def gate_check(message: Message, db, bot: Bot) -> bool:
    """
    Silently gate every action:
    - If user not found or not onboarded → redirect to /start
    - If user left a channel → show rejoin screen
    Returns True if user can proceed, False if blocked.
    """
    user_id = message.from_user.id if hasattr(message, "from_user") and message.from_user else None
    if not user_id:
        return False

    user = await get_user(db, user_id)
    if not user or not user.get("onboarded"):
        await message.answer(
            "⚠️ You haven't completed onboarding yet.\n\nType /start to begin."
        )
        return False

    not_in = await verify_membership(user_id, bot, db)
    if not_in:
        channels_text = "\n".join([f"• {t}" for t in not_in])
        onboarding_tasks = await get_onboarding_tasks(db)
        channel_tasks = [t for t in onboarding_tasks if t["task_type"] == "join_channel"]
        await message.answer(
            f"⚠️ *You've left some required channels!*\n\n"
            f"Please rejoin:\n{channels_text}\n\n"
            f"Tap *Done* after rejoining to continue.",
            reply_markup=onboarding_keyboard(channel_tasks),
            parse_mode="Markdown"
        )
        return False

    return True


async def show_colourful_menu(message: Message, db, user_id: int, bot: Bot):
    """Run gate check then show colourful panel with live data."""
    allowed = await gate_check(message, db, bot)
    if not allowed:
        return

    user = await get_user(db, user_id)
    balance = user.get("balance", 0) if user else 0
    referral_count = user.get("referral_count", 0) if user else 0
    username = message.from_user.first_name if hasattr(message, "from_user") and message.from_user else user.get("username", "User")

    await message.answer(
        f"👋 Hello *{username}*!\n\n"
        f"💰 *Balance:* ₦{balance:,.0f}\n"
        f"👥 *Referrals:* {referral_count}\n\n"
        f"What would you like to do?",
        reply_markup=colourful_dashboard_keyboard(balance, referral_count),
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# /start — Welcome Screen
# ─────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, db, bot: Bot):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    # Check referral param
    referred_by = None
    args = message.text.split()
    if len(args) > 1:
        try:
            ref_id = int(args[1])
            if ref_id != user_id:
                referred_by = ref_id
        except ValueError:
            pass

    # Create or get user
    user = await create_user(db, user_id, username, referred_by)

    # If already onboarded, run membership re-check first
    if user.get("onboarded"):
        await show_colourful_menu(message, db, user_id, bot)
        return

    # Show welcome screen
    bot_settings = await get_settings(db)
    pool = bot_settings.get("total_reward_pool", 500000)
    ref_reward = bot_settings.get("referral_reward", 100)
    bot_name = bot_settings.get("bot_name", "DoGetPaid Bot")

    await message.answer(
        f"💸 *Welcome to {bot_name}!*\n\n"
        f"🏆 Complete tasks, invite friends and earn real *Naira* daily!\n\n"
        f"🎁 *Total Reward Pool:* ₦{pool:,.0f}\n"
        f"👤 *Earn per Referral:* ₦{ref_reward:,.0f}\n\n"
        f"⚡ Fast • Easy • Reliable\n\n"
        f"Tap *Proceed* to get started!",
        reply_markup=welcome_keyboard(),
        parse_mode="Markdown"
    )
    # Send persistent menu keyboard so it's always visible
    await message.answer("👇 Use the menu button below anytime:", reply_markup=main_menu_keyboard())


# ─────────────────────────────────────────
# Proceed button — Show Telegram Tasks
# ─────────────────────────────────────────

@router.callback_query(F.data == "proceed_onboarding")
async def proceed_onboarding(callback: CallbackQuery, db, bot: Bot):
    await callback.answer()
    user_id = callback.from_user.id

    onboarding_tasks = await get_onboarding_tasks(db)
    channel_tasks = [t for t in onboarding_tasks if t["task_type"] == "join_channel"]

    if not channel_tasks:
        # No channel tasks — check WhatsApp tasks
        wa_tasks = [t for t in onboarding_tasks if t["task_type"] == "whatsapp"]
        if wa_tasks:
            await callback.message.answer(
                "📱 *Join the WhatsApp groups below to continue:*",
                reply_markup=whatsapp_tasks_keyboard(wa_tasks),
                parse_mode="Markdown"
            )
        else:
            await _complete_onboarding(callback.message, db, user_id)
        return

    bot_settings = await get_settings(db)
    pool = bot_settings.get("total_reward_pool", 500000)
    ref_reward = bot_settings.get("referral_reward", 100)

    await callback.message.answer(
        f"📋 *Step 1: Join All Channels*\n\n"
        f"🎁 Total Reward: ₦{pool:,.0f}\n"
        f"👤 Earn per Referral: ₦{ref_reward:,.0f}\n\n"
        f"📌 Join all channels below and tap *Done* to proceed.",
        reply_markup=onboarding_keyboard(channel_tasks),
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# Done button — Verify Telegram Channels
# ─────────────────────────────────────────

@router.callback_query(F.data == "onboarding_done")
async def onboarding_done(callback: CallbackQuery, db, bot: Bot):
    await callback.answer()
    user_id = callback.from_user.id

    onboarding_tasks = await get_onboarding_tasks(db)
    channel_tasks = [t for t in onboarding_tasks if t["task_type"] == "join_channel"]

    # Verify all channel joins
    not_joined = []
    for task in channel_tasks:
        channel_id = task.get("channel_id")
        if channel_id:
            try:
                member = await bot.get_chat_member(channel_id, user_id)
                if member.status in ["left", "kicked", "banned"]:
                    not_joined.append(task["title"])
            except Exception as e:
                logger.warning(f"Could not check channel {channel_id}: {e}")

    if not_joined:
        channels_text = "\n".join([f"• {t}" for t in not_joined])
        await callback.message.answer(
            f"❌ You haven't joined all channels yet!\n\n"
            f"Still need to join:\n{channels_text}\n\n"
            f"Please join and tap *Done* again.",
            parse_mode="Markdown"
        )
        return

    # Check for WhatsApp tasks
    wa_tasks = [t for t in onboarding_tasks if t["task_type"] == "whatsapp"]

    if wa_tasks:
        await callback.message.answer(
            "✅ *Channels verified!*\n\n"
            "📱 *Step 2: Join WhatsApp Groups*\n\n"
            "Join all WhatsApp groups below, then tap *I've Joined*.",
            reply_markup=whatsapp_tasks_keyboard(wa_tasks),
            parse_mode="Markdown"
        )
    else:
        await _complete_onboarding(callback.message, db, user_id)


# ─────────────────────────────────────────
# WhatsApp "I've Joined" — 15sec countdown
# ─────────────────────────────────────────

@router.callback_query(F.data == "whatsapp_joined")
async def whatsapp_joined(callback: CallbackQuery, db, bot: Bot):
    await callback.answer()
    user_id = callback.from_user.id

    # Send countdown message
    msg = await callback.message.answer("⏳ *Verifying... 15*", parse_mode="Markdown")

    # Countdown from 15 to 1
    for i in range(14, 0, -1):
        await asyncio.sleep(1)
        try:
            await msg.edit_text(f"⏳ *Verifying... {i}*", parse_mode="Markdown")
        except Exception:
            pass

    await asyncio.sleep(1)
    await msg.edit_text("✅ *Verified!*", parse_mode="Markdown")

    # Complete onboarding
    await _complete_onboarding(callback.message, db, user_id)


# ─────────────────────────────────────────
# Complete Onboarding
# ─────────────────────────────────────────

async def _complete_onboarding(message: Message, db, user_id: int):
    user = await get_user(db, user_id)
    if user and not user.get("onboarded"):
        await mark_onboarded(db, user_id)
        bot_settings = await get_settings(db)
        ref_reward = bot_settings.get("referral_reward", 100)
        await _credit_referrer(db, user, ref_reward)

    await message.answer("🎉 *Welcome aboard!* Here's your dashboard:", parse_mode="Markdown")
    await show_dashboard(message, db, user_id)


async def _credit_referrer(db, user: dict, ref_reward: float):
    referred_by = user.get("referred_by")
    if referred_by:
        referrer = await get_user(db, referred_by)
        if referrer:
            await update_user_balance(db, referred_by, ref_reward)
            await increment_referral_count(db, referred_by)


# ─────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────

async def show_dashboard(message: Message, db, user_id: int):
    user = await get_user(db, user_id)
    if not user:
        return

    balance = user.get("balance", 0)
    referral_count = user.get("referral_count", 0)
    username = message.from_user.first_name if hasattr(message, 'from_user') else user.get("username", "User")

    await message.answer(
        f"👋 Hello *{username}*!\n\n"
        f"💰 *Balance:* ₦{balance:,.0f}\n"
        f"👥 *Referrals:* {referral_count}\n\n"
        f"What would you like to do?",
        reply_markup=dashboard_keyboard(balance, referral_count),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "dashboard")
async def back_to_dash(callback: CallbackQuery, db, bot: Bot):
    await callback.answer()
    await show_colourful_menu(callback.message, db, callback.from_user.id, bot)


# ─────────────────────────────────────────
# /menu COMMAND + PERSISTENT BUTTON
# ─────────────────────────────────────────

@router.message(Command("menu"))
async def cmd_menu(message: Message, db, bot: Bot):
    await show_colourful_menu(message, db, message.from_user.id, bot)


@router.message(F.text == "📋 Menu")
async def persistent_menu_button(message: Message, db, bot: Bot):
    await show_colourful_menu(message, db, message.from_user.id, bot)


@router.message(Command("balance"))
async def cmd_balance(message: Message, db, bot: Bot):
    allowed = await gate_check(message, db, bot)
    if not allowed:
        return
    user = await get_user(db, message.from_user.id)
    balance = user.get("balance", 0) if user else 0
    bot_settings = await get_settings(db)
    min_w = bot_settings.get("min_withdraw", 500)
    await message.answer(
        f"💰 *Your Balance*\n\n"
        f"Available: ₦{balance:,.0f}\n"
        f"Minimum withdrawal: ₦{min_w:,.0f}",
        reply_markup=back_to_dashboard(),
        parse_mode="Markdown"
    )


@router.message(Command("referral"))
async def cmd_referral(message: Message, db, bot: Bot):
    allowed = await gate_check(message, db, bot)
    if not allowed:
        return
    user_id = message.from_user.id
    user = await get_user(db, user_id)
    bot_settings = await get_settings(db)
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    ref_reward = bot_settings.get("referral_reward", 100)
    await message.answer(
        f"🔗 *Your Referral Link*\n\n"
        f"`{ref_link}`\n\n"
        f"Share this link and earn *₦{ref_reward:,.0f}* for every person who joins!\n\n"
        f"👥 *Your referrals so far:* {user.get('referral_count', 0)}",
        reply_markup=back_to_dashboard(),
        parse_mode="Markdown"
    )


@router.message(Command("tasks"))
async def cmd_tasks(message: Message, db, bot: Bot):
    allowed = await gate_check(message, db, bot)
    if not allowed:
        return
    # Trigger show_tasks callback flow by redirecting
    await message.answer(
        "✅ Tap below to view your tasks:",
        reply_markup=back_to_dashboard()
    )


@router.message(Command("withdraw"))
async def cmd_withdraw(message: Message, db, bot: Bot):
    allowed = await gate_check(message, db, bot)
    if not allowed:
        return
    await message.answer(
        "💸 Use the Withdraw button in your dashboard menu to make a withdrawal.",
        reply_markup=back_to_dashboard()
    )


@router.message(Command("history"))
async def cmd_history(message: Message, db, bot: Bot):
    allowed = await gate_check(message, db, bot)
    if not allowed:
        return
    await message.answer(
        "📜 Tap below to go to your dashboard and check withdrawal history:",
        reply_markup=back_to_dashboard()
    )


# ─────────────────────────────────────────
# DELETE ACCOUNT
# ─────────────────────────────────────────

@router.callback_query(F.data == "delete_account")
async def delete_account_prompt(callback: CallbackQuery, db, bot: Bot):
    await callback.answer()
    allowed = await gate_check(callback.message, db, bot)
    if not allowed:
        return
    await callback.message.answer(
        "🗑️ *Delete My Account*\n\n"
        "⚠️ This will permanently delete:\n"
        "• Your balance\n"
        "• Your referral history\n"
        "• Your task completions\n"
        "• Your withdrawal records\n"
        "• All your data\n\n"
        "You will start fresh as a new user.\n\n"
        "*Are you sure?*",
        reply_markup=confirm_delete_keyboard(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "confirm_delete")
async def confirm_delete_account(callback: CallbackQuery, db, bot: Bot):
    await callback.answer()
    user_id = callback.from_user.id
    await delete_user(db, user_id)
    await callback.message.answer(
        "✅ *Account deleted successfully.*\n\n"
        "All your data has been wiped.\n"
        "Type /start to begin again as a new user.",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# BALANCE
# ─────────────────────────────────────────

@router.callback_query(F.data == "show_balance")
async def show_balance(callback: CallbackQuery, db):
    await callback.answer()
    user = await get_user(db, callback.from_user.id)
    balance = user.get("balance", 0) if user else 0
    bot_settings = await get_settings(db)
    min_w = bot_settings.get("min_withdraw", 500)

    await callback.message.answer(
        f"💰 *Your Balance*\n\n"
        f"Available: ₦{balance:,.0f}\n"
        f"Minimum withdrawal: ₦{min_w:,.0f}",
        reply_markup=back_to_dashboard(),
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# REFERRAL LINK
# ─────────────────────────────────────────

@router.callback_query(F.data == "get_referral_link")
async def get_referral_link(callback: CallbackQuery, db, bot: Bot):
    await callback.answer()
    user_id = callback.from_user.id
    user = await get_user(db, user_id)
    bot_settings = await get_settings(db)

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    ref_reward = bot_settings.get("referral_reward", 100)

    await callback.message.answer(
        f"🔗 *Your Referral Link*\n\n"
        f"`{ref_link}`\n\n"
        f"Share this link and earn *₦{ref_reward:,.0f}* for every person who joins!\n\n"
        f"👥 *Your referrals so far:* {user.get('referral_count', 0)}",
        reply_markup=back_to_dashboard(),
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# REFERRALS COUNT
# ─────────────────────────────────────────

@router.callback_query(F.data == "show_referrals")
async def show_referrals(callback: CallbackQuery, db, bot: Bot):
    await callback.answer()
    user = await get_user(db, callback.from_user.id)
    bot_settings = await get_settings(db)
    ref_reward = bot_settings.get("referral_reward", 100)
    ref_count = user.get("referral_count", 0)
    total_earned = ref_count * ref_reward

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={callback.from_user.id}"

    await callback.message.answer(
        f"👥 *Your Referrals*\n\n"
        f"Total referrals: *{ref_count}*\n"
        f"Earned from referrals: *₦{total_earned:,.0f}*\n\n"
        f"🔗 Your link:\n`{ref_link}`",
        reply_markup=back_to_dashboard(),
        parse_mode="Markdown"
    )
