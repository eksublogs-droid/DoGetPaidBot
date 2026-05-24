import asyncio
import logging
import random
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from models.db import (
    get_user, create_user, get_settings, mark_onboarded,
    update_user_balance, increment_referral_count,
    get_onboarding_tasks, get_user_completions, delete_user,
    set_captcha_answer, clear_captcha_answer,
    ban_user, unban_user, set_user_balance, count_all_users
)
from utils.keyboards import (
    welcome_keyboard, onboarding_keyboard,
    whatsapp_tasks_keyboard, dashboard_keyboard, back_to_dashboard,
    colourful_dashboard_keyboard, main_menu_keyboard, confirm_delete_keyboard
)
from config.settings import settings

logger = logging.getLogger(__name__)
router = Router()

# Tracks the active countdown task per user so old tasks can be cancelled
# before a new one starts (prevents stacked countdowns on wrong answers).
_captcha_tasks: dict[int, asyncio.Task] = {}

CAPTCHA_TIMEOUT = 20        # seconds user has to answer captcha
SPEED_FLAG_THRESHOLD = 5    # flag if onboarding completed in under this many seconds

# Only these 2 Telegram user IDs may delete their own account.
SELF_DELETE_ALLOWED_IDS: set[int] = {
    1794483261,   # +2348137890167 (@EksuBlog)
    6511973707,   # +2348104797770 (@PDANWEALTH)
}


# ─────────────────────────────────────────
# FSM STATES
# ─────────────────────────────────────────

class OnboardingStates(StatesGroup):
    WaitingCaptcha = State()


class AdminUserActionStates(StatesGroup):
    WaitingBalanceAmount = State()   # admin typed adjust_bal:<uid>, now waiting for amount


# ─────────────────────────────────────────
# INLINE KEYBOARDS — admin action panel
# ─────────────────────────────────────────

def admin_user_action_keyboard(target_uid: int) -> InlineKeyboardMarkup:
    """Inline buttons attached to every new-user admin notification."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚫 Ban",        callback_data=f"adm_ban:{target_uid}"),
            InlineKeyboardButton(text="🗑️ Remove",     callback_data=f"adm_remove:{target_uid}"),
        ],
        [
            InlineKeyboardButton(text="➕ Add Balance", callback_data=f"adm_addbal:{target_uid}"),
            InlineKeyboardButton(text="➖ Deduct Bal",  callback_data=f"adm_deductbal:{target_uid}"),
        ],
    ])


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
    """Gate for Message handlers."""
    user_id = message.from_user.id
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


async def gate_check_callback(callback: CallbackQuery, db, bot: Bot) -> bool:
    """Gate for CallbackQuery handlers."""
    user_id = callback.from_user.id
    user = await get_user(db, user_id)
    if not user or not user.get("onboarded"):
        await callback.message.answer(
            "⚠️ You haven't completed onboarding yet.\n\nType /start to begin."
        )
        return False
    not_in = await verify_membership(user_id, bot, db)
    if not_in:
        channels_text = "\n".join([f"• {t}" for t in not_in])
        onboarding_tasks = await get_onboarding_tasks(db)
        channel_tasks = [t for t in onboarding_tasks if t["task_type"] == "join_channel"]
        await callback.message.answer(
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
# ADMIN NOTIFICATION HELPERS
# ─────────────────────────────────────────

def _is_admin(user_id: int) -> bool:
    return user_id in settings.ADMIN_IDS


async def _notify_admin_new_user(bot: Bot, db, user: dict, from_user):
    """Send admin a rich message when a brand-new user registers."""
    if not settings.ADMIN_IDS:
        return

    # Referred-by validity check
    referred_by = user.get("referred_by")
    if referred_by:
        referrer = await get_user(db, referred_by)
        if referrer:
            ref_text = f"[{referred_by}](tg://user?id={referred_by}) ✅ Valid referrer"
        else:
            ref_text = f"`{referred_by}` ❌ Invalid ref ID (user not found)"
    else:
        ref_text = "⚠️ No referrer"

    # Total user count
    total_users = await count_all_users(db)

    joined_at = user.get("joined_at", datetime.utcnow())
    joined_str = joined_at.strftime("%Y-%m-%d %H:%M:%S UTC") if hasattr(joined_at, "strftime") else str(joined_at)

    name = from_user.full_name if hasattr(from_user, "full_name") else (from_user.first_name or "Unknown")
    username_display = f"@{from_user.username}" if from_user.username else "No username"
    is_premium = "⭐ Yes" if getattr(from_user, "is_premium", False) else "No"

    text = (
        f"🆕 *New User Registered!*\n\n"
        f"👤 *Name:* {name} ({username_display})\n"
        f"🆔 *Telegram ID:* `{from_user.id}`\n"
        f"⭐ *Telegram Premium:* {is_premium}\n"
        f"🔗 *Referred by:* {ref_text}\n"
        f"🌐 *Language:* {from_user.language_code or 'Unknown'}\n"
        f"📅 *Joined:* {joined_str}\n"
        f"🔢 *Total users now:* {total_users}"
    )

    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                text,
                parse_mode="Markdown",
                reply_markup=admin_user_action_keyboard(from_user.id)
            )
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")



async def _notify_admin_flagged(bot: Bot, user: dict, from_user, elapsed: float):
    """Notify admin of suspiciously fast onboarding."""
    if not settings.ADMIN_IDS:
        return

    referred_by = user.get("referred_by")
    ref_text = f"[{referred_by}](tg://user?id={referred_by})" if referred_by else "None"

    name = from_user.full_name if hasattr(from_user, "full_name") else (from_user.first_name or "Unknown")
    username_display = f"@{from_user.username}" if from_user.username else "No username"
    is_premium = "⭐ Yes" if getattr(from_user, "is_premium", False) else "No"

    text = (
        f"⚠️ *Suspicious User Detected!*\n\n"
        f"Completed onboarding in *{elapsed:.1f} seconds* (threshold: {SPEED_FLAG_THRESHOLD}s)\n\n"
        f"👤 *Name:* {name} ({username_display})\n"
        f"🆔 *Telegram ID:* `{from_user.id}`\n"
        f"⭐ *Telegram Premium:* {is_premium}\n"
        f"🔗 *Referred by:* {ref_text}\n"
        f"🌐 *Language:* {from_user.language_code or 'Unknown'}"
    )

    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                text,
                parse_mode="Markdown",
                reply_markup=admin_user_action_keyboard(from_user.id)
            )
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")


# ─────────────────────────────────────────
# ADMIN INLINE ACTION HANDLERS
# ─────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_ban:"))
async def admin_action_ban(callback: CallbackQuery, db, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await callback.answer("Not authorised.", show_alert=True)
        return
    await callback.answer()
    target_uid = int(callback.data.split(":")[1])
    target = await get_user(db, target_uid)
    if not target:
        await callback.message.reply("❌ User not found.")
        return
    await ban_user(db, target_uid)
    await callback.message.reply(
        f"🚫 User `{target_uid}` has been *banned*.",
        parse_mode="Markdown"
    )
    try:
        await bot.send_message(target_uid, "🚫 Your account has been banned. Contact support if you believe this is a mistake.")
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm_remove:"))
async def admin_action_remove(callback: CallbackQuery, db, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await callback.answer("Not authorised.", show_alert=True)
        return
    await callback.answer()
    target_uid = int(callback.data.split(":")[1])
    target = await get_user(db, target_uid)
    if not target:
        await callback.message.reply("❌ User not found.")
        return
    await delete_user(db, target_uid)
    await callback.message.reply(
        f"🗑️ User `{target_uid}` and all their data have been *removed*.",
        parse_mode="Markdown"
    )
    try:
        await bot.send_message(target_uid, "🗑️ Your account has been removed by an admin.")
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm_addbal:"))
async def admin_action_addbal(callback: CallbackQuery, db, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("Not authorised.", show_alert=True)
        return
    await callback.answer()
    target_uid = int(callback.data.split(":")[1])
    await state.set_state(AdminUserActionStates.WaitingBalanceAmount)
    await state.update_data(target_uid=target_uid, action="add")
    await callback.message.reply(
        f"➕ How much do you want to *add* to user `{target_uid}`'s balance?\n\n"
        f"Send the amount (numbers only, e.g. `500`):",
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("adm_deductbal:"))
async def admin_action_deductbal(callback: CallbackQuery, db, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("Not authorised.", show_alert=True)
        return
    await callback.answer()
    target_uid = int(callback.data.split(":")[1])
    await state.set_state(AdminUserActionStates.WaitingBalanceAmount)
    await state.update_data(target_uid=target_uid, action="deduct")
    await callback.message.reply(
        f"➖ How much do you want to *deduct* from user `{target_uid}`'s balance?\n\n"
        f"Send the amount (numbers only, e.g. `500`):",
        parse_mode="Markdown"
    )


@router.message(AdminUserActionStates.WaitingBalanceAmount)
async def admin_balance_amount_input(message: Message, db, bot: Bot, state: FSMContext):
    """Receive the amount from admin after tapping Add/Deduct Balance."""
    if not _is_admin(message.from_user.id):
        # Should never happen — but if state is stuck, clear it and tell user to retry
        await state.clear()
        if message.text and message.text.startswith("/"):
            await message.answer("⚠️ Something went wrong. Please try your command again.")
        return

    raw = message.text.strip().replace(",", "").replace("₦", "")
    try:
        amount = float(raw)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Invalid amount. Send a positive number only, e.g. `500`.", parse_mode="Markdown")
        return

    data = await state.get_data()
    target_uid = data.get("target_uid")
    action = data.get("action")
    await state.clear()

    target = await get_user(db, target_uid)
    if not target:
        await message.answer("❌ User not found.")
        return

    if action == "add":
        await update_user_balance(db, target_uid, amount)
        direction = f"+₦{amount:,.0f}"
        verb = "added to"
    else:
        await update_user_balance(db, target_uid, -amount)
        direction = f"-₦{amount:,.0f}"
        verb = "deducted from"

    updated = await get_user(db, target_uid)
    new_balance = updated.get("balance", 0) if updated else 0

    await message.answer(
        f"✅ *{direction}* {verb} user `{target_uid}`'s balance.\n"
        f"💰 New balance: ₦{new_balance:,.0f}",
        parse_mode="Markdown"
    )

    # Notify the user
    try:
        if action == "add":
            await bot.send_message(
                target_uid,
                f"💰 *₦{amount:,.0f} has been added to your balance by admin!*\n"
                f"New balance: ₦{new_balance:,.0f}",
                parse_mode="Markdown"
            )
        else:
            await bot.send_message(
                target_uid,
                f"💰 *₦{amount:,.0f} has been deducted from your balance by admin.*\n"
                f"New balance: ₦{new_balance:,.0f}",
                parse_mode="Markdown"
            )
    except Exception:
        pass


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

    existing = await get_user(db, user_id)
    user = await create_user(db, user_id, username, referred_by)
    is_brand_new = existing is None  # True = never existed before (or was deleted)

    if is_brand_new:
        try:
            await _notify_admin_new_user(bot, db, user, message.from_user)
        except Exception as e:
            logger.warning(f"Admin notification failed for new user {user_id}: {e}")

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
        wa_tasks = [t for t in onboarding_tasks if t["task_type"] == "whatsapp"]
        if wa_tasks:
            await callback.message.answer(
                "📱 *Join the WhatsApp groups below to continue:*",
                reply_markup=whatsapp_tasks_keyboard(wa_tasks),
                parse_mode="Markdown"
            )
        else:
            await _send_captcha(callback, db, user_id)
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
        await _send_captcha(callback, db, user_id)


# ─────────────────────────────────────────
# WhatsApp "I've Joined" — 15sec countdown
# ─────────────────────────────────────────

@router.callback_query(F.data == "whatsapp_joined")
async def whatsapp_joined(callback: CallbackQuery, db, bot: Bot):
    await callback.answer()
    user_id = callback.from_user.id

    msg = await callback.message.answer("⏳ *Verifying... 15*", parse_mode="Markdown")

    for i in range(14, 0, -1):
        await asyncio.sleep(1)
        try:
            await msg.edit_text(f"⏳ *Verifying... {i}*", parse_mode="Markdown")
        except Exception:
            pass

    await asyncio.sleep(1)
    await msg.edit_text("✅ *Verified!*", parse_mode="Markdown")

    await _send_captcha(callback, db, user_id)


# ─────────────────────────────────────────
# CAPTCHA STEP
# ─────────────────────────────────────────

def _start_captcha_task(user_id: int, msg: Message, db, number: int) -> None:
    """Cancel any existing countdown for this user, then start a fresh one."""
    existing = _captcha_tasks.get(user_id)
    if existing and not existing.done():
        existing.cancel()
    task = asyncio.create_task(_captcha_countdown(msg, db, user_id, number))
    _captcha_tasks[user_id] = task


async def _send_captcha(callback_or_msg, db, user_id: int):
    """Generate and send a captcha challenge, then start the live countdown."""
    number = random.randint(10000, 99999)
    correct_answer = str(number)[-3:]
    await set_captcha_answer(db, user_id, correct_answer)

    # Works whether called from a CallbackQuery or a Message context
    answer_fn = callback_or_msg.message.answer if isinstance(callback_or_msg, CallbackQuery) else callback_or_msg.answer

    msg = await answer_fn(
        f"🔐 *Human Verification*\n\n"
        f"Type the *last 3 digits* of this number:\n\n"
        f"*{number}*\n\n"
        f"⏱ Time remaining: *{CAPTCHA_TIMEOUT}s*",
        parse_mode="Markdown"
    )

    _start_captcha_task(user_id, msg, db, number)


async def _captcha_countdown(msg: Message, db, user_id: int, number: int):
    """Tick the captcha timer down. Stop early on correct answer. Resend on expiry."""
    for remaining in range(CAPTCHA_TIMEOUT - 1, 0, -1):
        await asyncio.sleep(1)
        user = await get_user(db, user_id)
        if user and user.get("captcha_answer") is None:
            # Cleared by successful answer — stop
            _captcha_tasks.pop(user_id, None)
            return
        try:
            await msg.edit_text(
                f"🔐 *Human Verification*\n\n"
                f"Type the *last 3 digits* of this number:\n\n"
                f"*{number}*\n\n"
                f"⏱ Time remaining: *{remaining}s*",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    await asyncio.sleep(1)
    user = await get_user(db, user_id)
    if user and user.get("captcha_answer") is not None:
        await clear_captcha_answer(db, user_id)
        try:
            await msg.edit_text("⏰ *Captcha expired!*\n\nSending a new one...", parse_mode="Markdown")
        except Exception:
            pass
        await _resend_captcha_after_expire(msg, db, user_id)
    _captcha_tasks.pop(user_id, None)


async def _resend_captcha_after_expire(prev_msg: Message, db, user_id: int):
    """Resend captcha when the previous one timed out."""
    number = random.randint(10000, 99999)
    correct_answer = str(number)[-3:]
    await set_captcha_answer(db, user_id, correct_answer)

    msg = await prev_msg.answer(
        f"🔐 *Human Verification*\n\n"
        f"Type the *last 3 digits* of this number:\n\n"
        f"*{number}*\n\n"
        f"⏱ Time remaining: *{CAPTCHA_TIMEOUT}s*",
        parse_mode="Markdown"
    )
    _start_captcha_task(user_id, msg, db, number)


# ─────────────────────────────────────────
# CAPTCHA ANSWER HANDLER
# ─────────────────────────────────────────

@router.message(F.text.regexp(r"^\d{3}$"))
async def handle_captcha_answer(message: Message, db, bot: Bot, state: FSMContext):
    """Intercept 3-digit replies during onboarding captcha."""
    user_id = message.from_user.id
    user = await get_user(db, user_id)

    # Only process if user is not yet onboarded and has a pending captcha
    if not user or user.get("onboarded") or not user.get("captcha_answer"):
        return

    submitted = message.text.strip()
    correct = user.get("captcha_answer")

    if submitted != correct:
        await message.answer("❌ *Wrong answer, try again.*", parse_mode="Markdown")
        number = random.randint(10000, 99999)
        new_answer = str(number)[-3:]
        await set_captcha_answer(db, user_id, new_answer)
        msg = await message.answer(
            f"🔐 *Human Verification*\n\n"
            f"Type the *last 3 digits* of this number:\n\n"
            f"*{number}*\n\n"
            f"⏱ Time remaining: *{CAPTCHA_TIMEOUT}s*",
            parse_mode="Markdown"
        )
        _start_captcha_task(user_id, msg, db, number)
        return

    # Correct — clear captcha to stop the countdown task
    await clear_captcha_answer(db, user_id)
    await message.answer("✅ *Verified! You're human.*", parse_mode="Markdown")

    await _complete_onboarding(message, db, user_id, bot)


# ─────────────────────────────────────────
# Complete Onboarding
# ─────────────────────────────────────────

async def _complete_onboarding(event, db, user_id: int, bot: Bot):
    """
    event: Message or CallbackQuery.
    Runs time-gap check, marks onboarded, credits referrer, shows dashboard.
    """
    user = await get_user(db, user_id)
    if user and not user.get("onboarded"):
        joined_at = user.get("joined_at")
        now_utc = datetime.utcnow()
        flagged = False

        if joined_at:
            joined_at_naive = joined_at.replace(tzinfo=None) if (hasattr(joined_at, "tzinfo") and joined_at.tzinfo) else joined_at
            elapsed = (now_utc - joined_at_naive).total_seconds()

            if elapsed < SPEED_FLAG_THRESHOLD:
                flagged = True
                await _notify_admin_flagged(bot, user, event.from_user, elapsed)

        await mark_onboarded(db, user_id, flagged=flagged)

        bot_settings = await get_settings(db)
        ref_reward = bot_settings.get("referral_reward", 100)
        await _credit_referrer(db, bot, user, ref_reward)

    user = await get_user(db, user_id)
    balance = user.get("balance", 0) if user else 0
    referral_count = user.get("referral_count", 0) if user else 0
    username = event.from_user.first_name

    answer_fn = event.answer if isinstance(event, Message) else event.message.answer

    await answer_fn("🎉 *Welcome aboard!* Here's your dashboard:", parse_mode="Markdown")
    await answer_fn(
        f"👋 Hello *{username}*!\n\n"
        f"💰 *Balance:* ₦{balance:,.0f}\n"
        f"👥 *Referrals:* {referral_count}\n\n"
        f"What would you like to do?",
        reply_markup=colourful_dashboard_keyboard(balance, referral_count),
        parse_mode="Markdown"
    )


async def _credit_referrer(db, bot: Bot, user: dict, ref_reward: float):
    referred_by = user.get("referred_by")
    if referred_by:
        referrer = await get_user(db, referred_by)
        if referrer:
            await update_user_balance(db, referred_by, ref_reward)
            await increment_referral_count(db, referred_by)

            new_total = referrer.get("referral_count", 0) + 1
            try:
                await bot.send_message(
                    referred_by,
                    f"🎉 Someone just joined using your referral link! "
                    f"+₦{ref_reward:,.0f} added. Total referrals: {new_total}",
                )
            except Exception as e:
                logger.warning(f"Could not notify referrer {referred_by}: {e}")


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
    allowed = await gate_check_callback(callback, db, bot)
    if not allowed:
        return
    user = await get_user(db, callback.from_user.id)
    balance = user.get("balance", 0) if user else 0
    referral_count = user.get("referral_count", 0) if user else 0
    username = callback.from_user.first_name
    await callback.message.answer(
        f"👋 Hello *{username}*!\n\n"
        f"💰 *Balance:* ₦{balance:,.0f}\n"
        f"👥 *Referrals:* {referral_count}\n\n"
        f"What would you like to do?",
        reply_markup=colourful_dashboard_keyboard(balance, referral_count),
        parse_mode="Markdown"
    )


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
    if callback.from_user.id not in SELF_DELETE_ALLOWED_IDS:
        await callback.message.answer("⛔ Account deletion is not available for your account.")
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


@router.message(Command("removemyaccount"))
async def cmd_remove_account(message: Message, db, bot: Bot):
    if message.from_user.id not in SELF_DELETE_ALLOWED_IDS:
        await message.answer("⛔ Account deletion is not available for your account.")
        return
    await message.answer(
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
    if callback.from_user.id not in SELF_DELETE_ALLOWED_IDS:
        await callback.message.answer("⛔ Account deletion is not available for your account.")
        return
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
async def show_balance(callback: CallbackQuery, db, bot: Bot):
    await callback.answer()
    allowed = await gate_check_callback(callback, db, bot)
    if not allowed:
        return
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
    allowed = await gate_check_callback(callback, db, bot)
    if not allowed:
        return
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
    allowed = await gate_check_callback(callback, db, bot)
    if not allowed:
        return
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
