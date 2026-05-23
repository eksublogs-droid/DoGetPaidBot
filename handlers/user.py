import logging
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from models.db import (
    get_user, create_user, get_settings, mark_onboarded,
    update_user_balance, increment_referral_count,
    get_onboarding_tasks, get_user_completions
)
from utils.keyboards import onboarding_keyboard, whatsapp_tasks_keyboard, dashboard_keyboard, back_to_dashboard
from config.settings import settings

logger = logging.getLogger(__name__)
router = Router()


# ─────────────────────────────────────────
# /start
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

    # If already onboarded, show dashboard
    if user.get("onboarded"):
        await show_dashboard(message, db, user_id)
        return

    # Get bot settings
    bot_settings = await get_settings(db)
    pool = bot_settings.get("total_reward_pool", 500000)
    ref_reward = bot_settings.get("referral_reward", 100)

    # Get onboarding tasks
    onboarding_tasks = await get_onboarding_tasks(db)

    # Filter channel tasks (for joining)
    channel_tasks = [t for t in onboarding_tasks if t["task_type"] == "join_channel"]

    text = (
        f"🎁 *{bot_settings.get('bot_name', 'MOREMONEE')} Is Live* 🔄\n\n"
        f"🎁 *Total Reward:* ₦{pool:,.0f}\n"
        f"👤 *Earn per Referral:* ₦{ref_reward:,.0f}\n\n"
        f"📋 *Instructions:*\n"
        f"Join all channels below and tap \"Done\" to proceed."
    )

    if not channel_tasks:
        # No onboarding tasks configured yet, go straight to dashboard
        await mark_onboarded(db, user_id)
        await _credit_referrer(db, user, ref_reward)
        await show_dashboard(message, db, user_id)
        return

    await message.answer(
        text,
        reply_markup=onboarding_keyboard(channel_tasks),
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# ONBOARDING — Done button (channel tasks)
# ─────────────────────────────────────────

@router.callback_query(F.data == "onboarding_done")
async def onboarding_done(callback: CallbackQuery, db, bot: Bot):
    user_id = callback.from_user.id
    await callback.answer()

    # Get onboarding channel tasks
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

    # Check for WhatsApp/manual onboarding tasks
    wa_tasks = [t for t in onboarding_tasks if t["task_type"] in ["whatsapp", "visit_link"]]

    if wa_tasks:
        await callback.message.answer(
            f"✅ Great! Now join the groups below to continue:",
            reply_markup=whatsapp_tasks_keyboard(wa_tasks)
        )
    else:
        # No extra tasks — complete onboarding
        await _complete_onboarding(callback.message, db, user_id)


# ─────────────────────────────────────────
# ONBOARDING — Extra tasks Done
# ─────────────────────────────────────────

@router.callback_query(F.data == "extra_tasks_done")
async def extra_tasks_done(callback: CallbackQuery, db):
    user_id = callback.from_user.id
    await callback.answer()
    await _complete_onboarding(callback.message, db, user_id)


async def _complete_onboarding(message: Message, db, user_id: int):
    """Mark user as onboarded, credit referrer, show dashboard."""
    user = await get_user(db, user_id)
    if user and not user.get("onboarded"):
        await mark_onboarded(db, user_id)
        bot_settings = await get_settings(db)
        ref_reward = bot_settings.get("referral_reward", 100)
        await _credit_referrer(db, user, ref_reward)

    await message.answer("🎉 Welcome aboard! Here's your dashboard:")
    await show_dashboard(message, db, user_id)


async def _credit_referrer(db, user: dict, ref_reward: float):
    """Credit the person who referred this user."""
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

    text = (
        f"👋 Hello *{username}*!\n\n"
        f"💰 *Balance:* ₦{balance:,.0f}\n"
        f"👥 *Referrals:* {referral_count}\n\n"
        f"What would you like to do?"
    )

    await message.answer(
        text,
        reply_markup=dashboard_keyboard(balance, referral_count),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "dashboard")
async def back_to_dash(callback: CallbackQuery, db):
    await callback.answer()
    await show_dashboard(callback.message, db, callback.from_user.id)


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
        f"Share this link and earn *₦{ref_reward:,.0f}* for every person who joins and completes onboarding!\n\n"
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
