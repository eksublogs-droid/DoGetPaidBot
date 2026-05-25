import logging
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from models.db import (
    add_task, delete_task, get_all_tasks, get_settings,
    update_setting, update_nested_setting, get_all_users, get_pending_withdrawals,
    get_pending_completions, get_user, update_user_balance,
    delete_user, reset_user, ban_user, unban_user,
    get_all_users_paginated, get_banned_users, get_flagged_users,
    update_withdrawal_status, toggle_task_onboarding, toggle_task_pause,
    toggle_task_pin, update_task_reward, get_task_by_id, count_all_users,
    get_referral_leaderboard, get_withdrawal_stats, get_total_paid_out,
    task_title_exists, set_referral_contest_prize, log_transaction,
    get_ads_by_user, get_pending_review_ads, increment_total_withdrawn
)
from handlers.notifications import notify_all_users, send_new_task_alert
from utils.keyboards import (
    admin_withdrawal_keyboard, admin_task_completion_keyboard,
    admin_ad_review_keyboard
)
from config.settings import settings

logger = logging.getLogger(__name__)
router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in settings.ADMIN_IDS


def _is_command(text: str) -> bool:
    return text is not None and text.startswith("/")


# ─────────────────────────────────────────
# FSM STATES
# ─────────────────────────────────────────

class AddTaskStates(StatesGroup):
    title = State()
    description = State()
    link = State()
    task_type = State()
    confirm_type = State()
    reward = State()
    onboarding = State()
    channel_id = State()
    slots = State()
    proof_instructions = State()


class SetBotNameStates(StatesGroup):
    waiting_name = State()


class UserBalanceStates(StatesGroup):
    add_amount = State()
    deduct_amount = State()


class EditTaskRewardStates(StatesGroup):
    new_reward = State()


class BroadcastPhotoStates(StatesGroup):
    photo = State()
    caption = State()


class SetContestPrizeStates(StatesGroup):
    waiting_prize = State()


class EditAdTierStates(StatesGroup):
    waiting_price = State()


class EditCheckinStates(StatesGroup):
    waiting_range = State()


# ─────────────────────────────────────────
# KEYBOARD HELPERS
# ─────────────────────────────────────────

def _users_list_keyboard(users: list, page: int, total: int, page_size: int = 20) -> InlineKeyboardMarkup:
    buttons = []
    for u in users:
        uname = f"@{u['username']}" if u.get("username") else str(u["telegram_id"])
        balance = u.get("balance", 0)
        buttons.append([InlineKeyboardButton(
            text=f"{uname} — ₦{balance:,.0f}",
            callback_data=f"admin_user:{u['telegram_id']}"
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"admin_users_page:{page - 1}"))
    total_pages = (total + page_size - 1) // page_size
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if (page + 1) * page_size < total:
        nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"admin_users_page:{page + 1}"))
    if nav:
        buttons.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _user_detail_keyboard(user_id: int) -> InlineKeyboardMarkup:
    uid = str(user_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Add Balance", callback_data=f"admin_addbal:{uid}"),
            InlineKeyboardButton(text="➖ Deduct Balance", callback_data=f"admin_deductbal:{uid}"),
        ],
        [
            InlineKeyboardButton(text="🚫 Ban", callback_data=f"admin_ban_confirm:{uid}"),
            InlineKeyboardButton(text="✅ Unban", callback_data=f"admin_unban:{uid}"),
        ],
        [
            InlineKeyboardButton(text="🗑️ Remove", callback_data=f"admin_remove_confirm:{uid}"),
            InlineKeyboardButton(text="🔄 Reset", callback_data=f"admin_reset:{uid}"),
        ],
        [InlineKeyboardButton(text="🔙 Back to Users", callback_data="admin_users_page:0")],
    ])


def _confirm_action_keyboard(action: str, user_id: int) -> InlineKeyboardMarkup:
    uid = str(user_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Yes, confirm", callback_data=f"admin_{action}:{uid}"),
            InlineKeyboardButton(text="❌ Cancel", callback_data=f"admin_user:{uid}"),
        ]
    ])


def _tasks_list_keyboard(tasks: list) -> InlineKeyboardMarkup:
    buttons = []
    for t in tasks:
        ob_tag = "🟢" if t.get("onboarding") else "🔵"
        pin_tag = "📌" if t.get("pinned") else ""
        pause_tag = "⏸" if t.get("paused") else ""
        buttons.append([InlineKeyboardButton(
            text=f"{ob_tag}{pin_tag}{pause_tag} {t['title']} — ₦{t['reward']:,.0f}",
            callback_data=f"admin_task:{t['_id']}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _task_detail_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ Delete", callback_data=f"admin_deltask:{task_id}")],
        [InlineKeyboardButton(text="✏️ Edit Reward", callback_data=f"admin_editreward:{task_id}")],
        [InlineKeyboardButton(text="🔄 Toggle Onboarding/Dashboard", callback_data=f"admin_toggleob:{task_id}")],
        [InlineKeyboardButton(text="⏸ Pause/Resume", callback_data=f"admin_togglepause:{task_id}")],
        [InlineKeyboardButton(text="📌 Pin/Unpin", callback_data=f"admin_togglepin:{task_id}")],
        [InlineKeyboardButton(text="🔙 Back to Tasks", callback_data="admin_listtasks")],
    ])


def _cleartasks_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Yes, clear all", callback_data="admin_cleartasks_confirm"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="admin_cleartasks_cancel"),
    ]])


# ─────────────────────────────────────────
# /admin — Menu
# ─────────────────────────────────────────

@router.message(Command("admin"))
async def admin_menu(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🛠 *Admin Panel*\n\n"
        "*Task Management:*\n"
        "/addtask — Add task\n"
        "/listtasks — View all tasks\n"
        "/cleartasks — Delete all tasks\n"
        "/pendingtasks — Pending task approvals\n\n"
        "*Ads:*\n"
        "/adqueue — Review premium ads\n"
        "/setadtier `[basic/standard/premium]` `[price]` — Edit ad tier price\n\n"
        "*Settings:*\n"
        "/setbotname — Change bot name\n"
        "/setminwithdrawbank `[amt]` — Min bank withdrawal\n"
        "/setminwithdrawairtime `[amt]` — Min airtime withdrawal\n"
        "/setreferralreward `[amt]` — Referral reward\n"
        "/setrewardpool `[amt]` — Total reward pool\n"
        "/setcommission `[pct]` — Task commission %\n"
        "/setcontestprize — Set monthly referral prize\n"
        "/setcheckinrange `[min]` `[max]` — Set check-in reward range\n"
        "/settings — View current settings\n\n"
        "*Users:*\n"
        "/users — Paginated user list\n"
        "/stats — Bot statistics\n"
        "/totalusers — Total user count\n"
        "/broadcast `[msg]` — Broadcast text\n"
        "/broadcastphoto — Broadcast photo\n"
        "/ban `[id]` — Ban user\n"
        "/unban `[id]` — Unban user\n"
        "/bannedusers — Banned users list\n"
        "/flaggedusers — Flagged users list\n"
        "/referralleaderboard — Top 10 referrers\n"
        "/addbalance `[id]` `[amt]` — Credit user\n"
        "/deductbalance `[id]` `[amt]` — Deduct from user\n"
        "/removeuser `[id]` — Delete user\n"
        "/resetuser `[id]` — Reset onboarding\n\n"
        "*Withdrawals:*\n"
        "/pendingwithdrawals — Pending list\n"
        "/approveall — Approve all pending\n"
        "/withdrawalstats — Summary",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# STATS
# ─────────────────────────────────────────

@router.message(Command("stats"))
async def admin_stats(message: Message, db):
    if not is_admin(message.from_user.id):
        return
    total_users = await count_all_users(db)
    stats = await get_withdrawal_stats(db)
    total_paid = await get_total_paid_out(db)
    active_tasks = await db.tasks.count_documents({"active": True, "onboarding": False})
    active_ads = await db.ads.count_documents({"status": "active"})
    pending_w = stats["pending"]["count"]
    paid_w = stats["paid"]["count"]
    bot_settings = await get_settings(db)
    commission = bot_settings.get("task_commission_pct", 10)

    await message.answer(
        f"📊 *Bot Statistics*\n\n"
        f"👥 Total users: {total_users}\n"
        f"✅ Active tasks: {active_tasks}\n"
        f"📢 Active ads: {active_ads}\n\n"
        f"💸 Pending withdrawals: {pending_w}\n"
        f"💰 Total paid out: ₦{total_paid:,.0f}\n"
        f"📦 Paid withdrawals: {paid_w}\n\n"
        f"📈 Task commission: {commission}%",
        parse_mode="Markdown"
    )


@router.message(Command("totalusers"))
async def admin_total_users(message: Message, db):
    if not is_admin(message.from_user.id):
        return
    total = await count_all_users(db)
    await message.answer(f"👥 Total registered users: *{total}*", parse_mode="Markdown")


# ─────────────────────────────────────────
# SETTINGS COMMANDS
# ─────────────────────────────────────────

@router.message(Command("settings"))
async def admin_view_settings(message: Message, db):
    if not is_admin(message.from_user.id):
        return
    s = await get_settings(db)
    tiers = s.get("ad_tiers", {})
    tier_lines = "\n".join(
        [f"  {k}: ₦{v.get('price',0):,} / {v.get('description','')}" for k, v in tiers.items()]
    )
    await message.answer(
        f"⚙️ *Current Settings*\n\n"
        f"Bot name: {s.get('bot_name', 'DoGetPaid Bot')}\n"
        f"Min withdraw (bank): ₦{s.get('min_withdraw_bank', 1500):,}\n"
        f"Min withdraw (airtime): ₦{s.get('min_withdraw_airtime', 700):,}\n"
        f"Referral reward: ₦{s.get('referral_reward', 100):,}\n"
        f"Total reward pool: ₦{s.get('total_reward_pool', 500000):,}\n"
        f"Task commission: {s.get('task_commission_pct', 10)}%\n"
        f"Check-in range: ₦{s.get('checkin_min_reward', 20)} – ₦{s.get('checkin_max_reward', 100)}\n"
        f"Contest prize: {s.get('referral_contest_prize', '₦5,000 airtime')}\n\n"
        f"Ad tiers:\n{tier_lines}",
        parse_mode="Markdown"
    )


@router.message(Command("setbotname"))
async def admin_setbotname_cmd(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        await _do_set_botname(message, parts[1].strip(), None)
        return
    await message.answer("Send the new bot name:")
    await state.set_state(SetBotNameStates.waiting_name)


@router.message(SetBotNameStates.waiting_name)
async def admin_setbotname_receive(message: Message, state: FSMContext, db):
    if not is_admin(message.from_user.id):
        await state.clear(); return
    await _do_set_botname(message, message.text.strip(), db)
    await state.clear()


async def _do_set_botname(message, name, db):
    if db:
        await update_setting(db, "bot_name", name)
    await message.answer(f"✅ Bot name set to: *{name}*", parse_mode="Markdown")


@router.message(Command("setminwithdrawbank"))
async def admin_set_min_withdraw_bank(message: Message, db):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /setminwithdrawbank [amount]"); return
    try:
        amount = float(parts[1])
        await update_setting(db, "min_withdraw_bank", amount)
        await update_setting(db, "min_withdraw", amount)  # keep compat
        await message.answer(f"✅ Min bank withdrawal set to ₦{amount:,.0f}", parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ Invalid amount.")


@router.message(Command("setminwithdrawairtime"))
async def admin_set_min_withdraw_airtime(message: Message, db):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /setminwithdrawairtime [amount]"); return
    try:
        amount = float(parts[1])
        await update_setting(db, "min_withdraw_airtime", amount)
        await message.answer(f"✅ Min airtime withdrawal set to ₦{amount:,.0f}", parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ Invalid amount.")


@router.message(Command("setreferralreward"))
async def admin_set_referral_reward(message: Message, db):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /setreferralreward [amount]"); return
    try:
        amount = float(parts[1])
        await update_setting(db, "referral_reward", amount)
        await message.answer(f"✅ Referral reward set to ₦{amount:,.0f}", parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ Invalid amount.")


@router.message(Command("setrewardpool"))
async def admin_set_reward_pool(message: Message, db):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /setrewardpool [amount]"); return
    try:
        amount = float(parts[1])
        await update_setting(db, "total_reward_pool", amount)
        await message.answer(f"✅ Reward pool set to ₦{amount:,.0f}", parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ Invalid amount.")


@router.message(Command("setcommission"))
async def admin_set_commission(message: Message, db):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /setcommission [percentage]"); return
    try:
        pct = float(parts[1])
        if not 0 <= pct <= 100:
            raise ValueError
        await update_setting(db, "task_commission_pct", pct)
        await message.answer(f"✅ Task commission set to {pct}%", parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ Invalid percentage (0–100).")


@router.message(Command("setcontestprize"))
async def admin_set_contest_prize(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        await message.answer(f"✅ Contest prize not saved (no db arg). Use state flow.")
    await message.answer("Send the new contest prize description (e.g. ₦10,000 airtime):")
    await state.set_state(SetContestPrizeStates.waiting_prize)


@router.message(SetContestPrizeStates.waiting_prize)
async def admin_receive_contest_prize(message: Message, state: FSMContext, db):
    if not is_admin(message.from_user.id):
        await state.clear(); return
    prize = message.text.strip()
    await set_referral_contest_prize(db, prize)
    await update_setting(db, "referral_contest_prize", prize)
    await state.clear()
    await message.answer(f"✅ Contest prize set to: *{prize}*", parse_mode="Markdown")


@router.message(Command("setcheckinrange"))
async def admin_set_checkin_range(message: Message, db):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Usage: /setcheckinrange [min] [max]"); return
    try:
        min_r = int(parts[1])
        max_r = int(parts[2])
        if min_r >= max_r:
            raise ValueError
        await update_setting(db, "checkin_min_reward", min_r)
        await update_setting(db, "checkin_max_reward", max_r)
        await message.answer(f"✅ Check-in range set to ₦{min_r} – ₦{max_r}", parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ Invalid values. min must be less than max.")


@router.message(Command("setadtier"))
async def admin_set_ad_tier(message: Message, db):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Usage: /setadtier [basic/standard/premium] [price]"); return
    tier = parts[1].lower()
    if tier not in ("basic", "standard", "premium"):
        await message.answer("❌ Tier must be basic, standard, or premium."); return
    try:
        price = float(parts[2])
        await update_nested_setting(db, f"ad_tiers.{tier}.price", price)
        await message.answer(f"✅ *{tier.capitalize()}* ad tier price set to ₦{price:,.0f}", parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ Invalid price.")


# ─────────────────────────────────────────
# BROADCAST
# ─────────────────────────────────────────

@router.message(Command("broadcast"))
async def admin_broadcast(message: Message, db, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /broadcast [message]")
        return
    text = parts[1].strip()
    await message.answer("📡 Broadcasting...")
    sent, failed = await notify_all_users(bot, db, text, only_notifications_on=False)
    await message.answer(f"✅ Broadcast done: {sent} sent, {failed} failed.")


@router.message(Command("broadcastphoto"))
async def admin_broadcastphoto_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Send the photo to broadcast:")
    await state.set_state(BroadcastPhotoStates.photo)


@router.message(BroadcastPhotoStates.photo, F.photo)
async def admin_broadcastphoto_photo(message: Message, state: FSMContext):
    await state.update_data(photo_id=message.photo[-1].file_id)
    await message.answer("Now send the caption:")
    await state.set_state(BroadcastPhotoStates.caption)


@router.message(BroadcastPhotoStates.caption, F.text)
async def admin_broadcastphoto_caption(message: Message, state: FSMContext, db, bot: Bot):
    if not is_admin(message.from_user.id):
        await state.clear(); return
    data = await state.get_data()
    photo_id = data["photo_id"]
    caption = message.text.strip()
    await state.clear()
    await message.answer("📡 Broadcasting photo...")
    users = await db.users.find({"onboarded": True, "banned": False},
                                 {"telegram_id": 1}).to_list(length=None)
    sent = failed = 0
    for u in users:
        try:
            await bot.send_photo(u["telegram_id"], photo=photo_id, caption=caption, parse_mode="Markdown")
            sent += 1
        except Exception:
            failed += 1
    await message.answer(f"✅ Photo broadcast done: {sent} sent, {failed} failed.")


# ─────────────────────────────────────────
# ADD TASK (extended with slots + proof instructions)
# ─────────────────────────────────────────

@router.message(Command("addtask"))
async def admin_addtask_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("📋 *Add New Task*\n\nStep 1/9 — Enter the task *title*:", parse_mode="Markdown")
    await state.set_state(AddTaskStates.title)


@router.message(AddTaskStates.title)
async def addtask_title(message: Message, state: FSMContext, db):
    if _is_command(message.text):
        await state.clear()
        await message.answer("Task creation cancelled.")
        return
    title = message.text.strip()
    if await task_title_exists(db, title):
        await message.answer(f"❌ A task named *{title}* already exists. Enter a different title:", parse_mode="Markdown")
        return
    await state.update_data(title=title)
    await message.answer("Step 2/9 — Enter a *description* (or type `skip`):", parse_mode="Markdown")
    await state.set_state(AddTaskStates.description)


@router.message(AddTaskStates.description)
async def addtask_description(message: Message, state: FSMContext):
    if _is_command(message.text):
        await state.clear(); await message.answer("Cancelled."); return
    desc = "" if message.text.strip().lower() == "skip" else message.text.strip()
    await state.update_data(description=desc)
    await message.answer("Step 3/9 — Enter the task *link* (URL), or type `skip`:", parse_mode="Markdown")
    await state.set_state(AddTaskStates.link)


@router.message(AddTaskStates.link)
async def addtask_link(message: Message, state: FSMContext):
    if _is_command(message.text):
        await state.clear(); await message.answer("Cancelled."); return
    link = "" if message.text.strip().lower() == "skip" else message.text.strip()
    await state.update_data(link=link)
    await message.answer(
        "Step 4/9 — Task type:\n"
        "`join_channel` | `visit_link` | `whatsapp` | `custom`",
        parse_mode="Markdown"
    )
    await state.set_state(AddTaskStates.task_type)


@router.message(AddTaskStates.task_type)
async def addtask_task_type(message: Message, state: FSMContext):
    if _is_command(message.text):
        await state.clear(); await message.answer("Cancelled."); return
    t = message.text.strip().lower()
    if t not in ("join_channel", "visit_link", "whatsapp", "custom"):
        await message.answer("❌ Invalid type. Choose: join_channel | visit_link | whatsapp | custom"); return
    await state.update_data(task_type=t)
    await message.answer("Step 5/9 — Confirm type:\n`auto` (Telegram API auto-verify) or `manual`:", parse_mode="Markdown")
    await state.set_state(AddTaskStates.confirm_type)


@router.message(AddTaskStates.confirm_type)
async def addtask_confirm_type(message: Message, state: FSMContext):
    if _is_command(message.text):
        await state.clear(); await message.answer("Cancelled."); return
    ct = message.text.strip().lower()
    if ct not in ("auto", "manual"):
        await message.answer("❌ Enter `auto` or `manual`:"); return
    await state.update_data(confirm_type=ct)
    await message.answer("Step 6/9 — Enter the *reward amount* (₦):", parse_mode="Markdown")
    await state.set_state(AddTaskStates.reward)


@router.message(AddTaskStates.reward)
async def addtask_reward(message: Message, state: FSMContext):
    if _is_command(message.text):
        await state.clear(); await message.answer("Cancelled."); return
    try:
        reward = float(message.text.strip().replace(",", "").replace("₦", ""))
        if reward <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Enter a valid positive amount:"); return
    await state.update_data(reward=reward)
    await message.answer("Step 7/9 — Is this an *onboarding* task? (`yes` / `no`):", parse_mode="Markdown")
    await state.set_state(AddTaskStates.onboarding)


@router.message(AddTaskStates.onboarding)
async def addtask_onboarding(message: Message, state: FSMContext):
    if _is_command(message.text):
        await state.clear(); await message.answer("Cancelled."); return
    v = message.text.strip().lower() in ("yes", "y", "true", "1")
    await state.update_data(onboarding=v)
    data = await state.get_data()
    if data.get("task_type") == "join_channel" and data.get("confirm_type") == "auto":
        await message.answer("Step 8/9 — Enter the *channel ID* (e.g. -1001234567890):", parse_mode="Markdown")
        await state.set_state(AddTaskStates.channel_id)
    else:
        await state.update_data(channel_id=None)
        await message.answer("Step 8/9 — How many *slots* (max users)? Type `0` for unlimited:", parse_mode="Markdown")
        await state.set_state(AddTaskStates.slots)


@router.message(AddTaskStates.channel_id)
async def addtask_channel_id(message: Message, state: FSMContext):
    if _is_command(message.text):
        await state.clear(); await message.answer("Cancelled."); return
    ch = message.text.strip()
    try:
        ch = int(ch)
    except ValueError:
        pass
    await state.update_data(channel_id=ch)
    await message.answer("Step 8b/9 — How many *slots*? Type `0` for unlimited:", parse_mode="Markdown")
    await state.set_state(AddTaskStates.slots)


@router.message(AddTaskStates.slots)
async def addtask_slots(message: Message, state: FSMContext):
    if _is_command(message.text):
        await state.clear(); await message.answer("Cancelled."); return
    try:
        slots = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Enter a number (0 = unlimited):"); return
    await state.update_data(slots=None if slots == 0 else slots)
    await message.answer("Step 9/9 — Proof instructions (what screenshot to send), or type `skip`:", parse_mode="Markdown")
    await state.set_state(AddTaskStates.proof_instructions)


@router.message(AddTaskStates.proof_instructions)
async def addtask_proof(message: Message, state: FSMContext, db, bot: Bot):
    if _is_command(message.text):
        await state.clear(); await message.answer("Cancelled."); return
    proof_instr = "" if message.text.strip().lower() == "skip" else message.text.strip()
    data = await state.get_data()
    data["proof_instructions"] = proof_instr
    await state.clear()

    task = await add_task(db, data)
    task_id = str(task["_id"])
    slots_display = str(data.get("slots")) if data.get("slots") else "Unlimited"

    await message.answer(
        f"✅ *Task Added!*\n\n"
        f"Title: {task['title']}\n"
        f"Reward: ₦{task['reward']:,.0f}\n"
        f"Type: {task['task_type']} ({task['confirm_type']})\n"
        f"Slots: {slots_display}\n"
        f"Onboarding: {'Yes' if task['onboarding'] else 'No'}\n"
        f"ID: `{task_id}`",
        parse_mode="Markdown"
    )

    # Notify users of new dashboard task
    if not task.get("onboarding"):
        await message.answer("📡 Sending new task alert to all users...")
        sent, failed = await send_new_task_alert(bot, db, task)
        await message.answer(f"Notified {sent} users ({failed} failed).")


# ─────────────────────────────────────────
# LIST TASKS
# ─────────────────────────────────────────

@router.message(Command("listtasks"))
@router.callback_query(F.data == "admin_listtasks")
async def admin_listtasks(event, db):
    if isinstance(event, CallbackQuery):
        await event.answer()
        send_fn = event.message.answer
        uid = event.from_user.id
    else:
        send_fn = event.answer
        uid = event.from_user.id
    if not is_admin(uid):
        return
    tasks = await get_all_tasks(db)
    if not tasks:
        await send_fn("No active tasks.", reply_markup=None)
        return
    await send_fn("📋 *All Active Tasks:*\n🟢 = onboarding  🔵 = dashboard  📌 = pinned  ⏸ = paused",
                  reply_markup=_tasks_list_keyboard(tasks), parse_mode="Markdown")


@router.callback_query(F.data.startswith("admin_task:"))
async def admin_task_detail(callback: CallbackQuery, db):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True); return
    await callback.answer()
    task_id = callback.data.split(":")[1]
    task = await get_task_by_id(db, task_id)
    if not task:
        await callback.message.reply("❌ Task not found."); return
    slots_display = f"{task.get('slots_filled', 0)}/{task.get('slots', '∞')}"
    await callback.message.answer(
        f"📋 *Task Detail*\n\n"
        f"Title: {task['title']}\n"
        f"Reward: ₦{task['reward']:,.0f}\n"
        f"Type: {task.get('task_type')} ({task.get('confirm_type')})\n"
        f"Slots: {slots_display}\n"
        f"Pinned: {'Yes' if task.get('pinned') else 'No'}\n"
        f"Paused: {'Yes' if task.get('paused') else 'No'}\n"
        f"Onboarding: {'Yes' if task.get('onboarding') else 'No'}\n"
        f"ID: `{task_id}`",
        reply_markup=_task_detail_keyboard(task_id),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("admin_deltask:"))
async def admin_delete_task(callback: CallbackQuery, db):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True); return
    await callback.answer("Deleted")
    task_id = callback.data.split(":")[1]
    await delete_task(db, task_id)
    await callback.message.reply("🗑️ Task deleted.")


@router.callback_query(F.data.startswith("admin_toggleob:"))
async def admin_toggle_onboarding(callback: CallbackQuery, db):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True); return
    task_id = callback.data.split(":")[1]
    new_val = await toggle_task_onboarding(db, task_id)
    await callback.answer(f"{'Onboarding' if new_val else 'Dashboard'} mode set")
    await callback.message.reply(f"✅ Task moved to {'onboarding' if new_val else 'dashboard'} tasks.")


@router.callback_query(F.data.startswith("admin_togglepause:"))
async def admin_toggle_pause(callback: CallbackQuery, db):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True); return
    task_id = callback.data.split(":")[1]
    new_val = await toggle_task_pause(db, task_id)
    await callback.answer(f"{'Paused' if new_val else 'Resumed'}")
    await callback.message.reply(f"{'⏸ Task paused.' if new_val else '▶️ Task resumed.'}")


@router.callback_query(F.data.startswith("admin_togglepin:"))
async def admin_toggle_pin(callback: CallbackQuery, db):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True); return
    task_id = callback.data.split(":")[1]
    new_val = await toggle_task_pin(db, task_id)
    await callback.answer(f"{'Pinned' if new_val else 'Unpinned'}")
    await callback.message.reply(f"{'📌 Task pinned.' if new_val else 'Task unpinned.'}")


@router.callback_query(F.data.startswith("admin_editreward:"))
async def admin_editreward_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True); return
    await callback.answer()
    task_id = callback.data.split(":")[1]
    await state.update_data(edit_task_id=task_id)
    await callback.message.reply("Enter new reward amount:")
    await state.set_state(EditTaskRewardStates.new_reward)


@router.message(EditTaskRewardStates.new_reward)
async def admin_editreward_receive(message: Message, state: FSMContext, db):
    if not is_admin(message.from_user.id):
        await state.clear(); return
    data = await state.get_data()
    task_id = data["edit_task_id"]
    try:
        reward = float(message.text.strip().replace(",", "").replace("₦", ""))
    except ValueError:
        await message.answer("❌ Invalid amount."); return
    await update_task_reward(db, task_id, reward)
    await state.clear()
    await message.answer(f"✅ Reward updated to ₦{reward:,.0f}", parse_mode="Markdown")


@router.message(Command("cleartasks"))
async def admin_cleartasks(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "⚠️ *Delete ALL active tasks?* This cannot be undone.",
        reply_markup=_cleartasks_confirm_keyboard(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin_cleartasks_confirm")
async def admin_cleartasks_confirm(callback: CallbackQuery, db):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True); return
    await callback.answer()
    await db.tasks.update_many({}, {"$set": {"active": False}})
    await callback.message.edit_text("✅ All tasks cleared.")


@router.callback_query(F.data == "admin_cleartasks_cancel")
async def admin_cleartasks_cancel(callback: CallbackQuery):
    await callback.answer("Cancelled")
    await callback.message.edit_text("❌ Cancelled.")


# ─────────────────────────────────────────
# USERS
# ─────────────────────────────────────────

@router.message(Command("users"))
@router.callback_query(F.data.startswith("admin_users_page:"))
async def admin_users(event, db):
    if isinstance(event, CallbackQuery):
        uid = event.from_user.id
        page = int(event.data.split(":")[1])
        send_fn = event.message.answer
        await event.answer()
    else:
        uid = event.from_user.id
        page = 0
        send_fn = event.answer
    if not is_admin(uid):
        return
    users, total = await get_all_users_paginated(db, page=page)
    if not users:
        await send_fn("No users found.")
        return
    await send_fn(
        f"👥 *Users* (page {page + 1}, {total} total):",
        reply_markup=_users_list_keyboard(users, page, total),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("admin_user:"))
async def admin_user_detail(callback: CallbackQuery, db):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True); return
    await callback.answer()
    target_uid = int(callback.data.split(":")[1])
    user = await get_user(db, target_uid)
    if not user:
        await callback.message.reply("❌ User not found."); return
    uname = f"@{user['username']}" if user.get("username") else str(target_uid)
    joined = user.get("joined_at", "")
    joined_str = joined.strftime("%b %d, %Y") if hasattr(joined, "strftime") else str(joined)
    await callback.message.answer(
        f"👤 *User Detail*\n\n"
        f"Name: {uname}\n"
        f"ID: `{target_uid}`\n"
        f"Balance: ₦{user.get('balance', 0):,.0f}\n"
        f"Referrals: {user.get('referral_count', 0)}\n"
        f"Tasks done: {user.get('tasks_done', 0)}\n"
        f"Banned: {'Yes' if user.get('banned') else 'No'}\n"
        f"Flagged: {'Yes' if user.get('flagged') else 'No'}\n"
        f"Joined: {joined_str}",
        reply_markup=_user_detail_keyboard(target_uid),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("admin_ban_confirm:"))
async def admin_ban_confirm(callback: CallbackQuery, db):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True); return
    await callback.answer()
    target_uid = int(callback.data.split(":")[1])
    await callback.message.answer(
        f"⚠️ Ban user `{target_uid}`?",
        reply_markup=_confirm_action_keyboard("ban", target_uid),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("admin_ban:"))
async def admin_ban(callback: CallbackQuery, db, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True); return
    await callback.answer("Banned")
    target_uid = int(callback.data.split(":")[1])
    await ban_user(db, target_uid)
    await callback.message.reply(f"🚫 User `{target_uid}` banned.", parse_mode="Markdown")
    try:
        await bot.send_message(target_uid, "🚫 Your account has been banned.")
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_unban:"))
async def admin_unban(callback: CallbackQuery, db, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True); return
    await callback.answer("Unbanned")
    target_uid = int(callback.data.split(":")[1])
    await unban_user(db, target_uid)
    await callback.message.reply(f"✅ User `{target_uid}` unbanned.", parse_mode="Markdown")
    try:
        await bot.send_message(target_uid, "✅ Your account has been unbanned.")
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_remove_confirm:"))
async def admin_remove_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True); return
    await callback.answer()
    target_uid = int(callback.data.split(":")[1])
    await callback.message.answer(
        f"⚠️ Permanently remove user `{target_uid}` and all their data?",
        reply_markup=_confirm_action_keyboard("remove", target_uid),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("admin_remove:"))
async def admin_remove(callback: CallbackQuery, db, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True); return
    await callback.answer("Removed")
    target_uid = int(callback.data.split(":")[1])
    await delete_user(db, target_uid)
    await callback.message.reply(f"🗑️ User `{target_uid}` removed.", parse_mode="Markdown")
    try:
        await bot.send_message(target_uid, "🗑️ Your account has been removed by an admin.")
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_reset:"))
async def admin_reset(callback: CallbackQuery, db, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True); return
    await callback.answer("Reset")
    target_uid = int(callback.data.split(":")[1])
    await reset_user(db, target_uid)
    await callback.message.reply(f"🔄 User `{target_uid}` onboarding reset.", parse_mode="Markdown")
    try:
        await bot.send_message(target_uid, "🔄 Your onboarding has been reset. Type /start to begin again.")
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_addbal:"))
async def admin_addbal_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True); return
    await callback.answer()
    target_uid = int(callback.data.split(":")[1])
    await state.set_state(UserBalanceStates.add_amount)
    await state.update_data(target_uid=target_uid)
    await callback.message.reply(f"➕ Amount to *add* to user `{target_uid}`:", parse_mode="Markdown")


@router.callback_query(F.data.startswith("admin_deductbal:"))
async def admin_deductbal_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True); return
    await callback.answer()
    target_uid = int(callback.data.split(":")[1])
    await state.set_state(UserBalanceStates.deduct_amount)
    await state.update_data(target_uid=target_uid)
    await callback.message.reply(f"➖ Amount to *deduct* from user `{target_uid}`:", parse_mode="Markdown")


@router.message(UserBalanceStates.add_amount)
async def admin_addbal_receive(message: Message, state: FSMContext, db, bot: Bot):
    if not is_admin(message.from_user.id):
        await state.clear(); return
    data = await state.get_data()
    target_uid = data["target_uid"]
    await state.clear()
    try:
        amount = float(message.text.strip().replace(",", "").replace("₦", ""))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Invalid amount."); return
    await update_user_balance(db, target_uid, amount)
    await log_transaction(db, target_uid, "admin_credit", amount,
                          f"Admin credit ₦{amount:,.0f}")
    user = await get_user(db, target_uid)
    new_bal = user.get("balance", 0) if user else 0
    await message.answer(f"✅ +₦{amount:,.0f} added to `{target_uid}`. New balance: ₦{new_bal:,.0f}", parse_mode="Markdown")
    try:
        await bot.send_message(target_uid, f"💰 *₦{amount:,.0f}* added to your balance by admin!\nNew balance: ₦{new_bal:,.0f}", parse_mode="Markdown")
    except Exception:
        pass


@router.message(UserBalanceStates.deduct_amount)
async def admin_deductbal_receive(message: Message, state: FSMContext, db, bot: Bot):
    if not is_admin(message.from_user.id):
        await state.clear(); return
    data = await state.get_data()
    target_uid = data["target_uid"]
    await state.clear()
    try:
        amount = float(message.text.strip().replace(",", "").replace("₦", ""))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Invalid amount."); return
    await update_user_balance(db, target_uid, -amount)
    await log_transaction(db, target_uid, "admin_deduct", -amount,
                          f"Admin deduction ₦{amount:,.0f}")
    user = await get_user(db, target_uid)
    new_bal = user.get("balance", 0) if user else 0
    await message.answer(f"✅ -₦{amount:,.0f} deducted from `{target_uid}`. New balance: ₦{new_bal:,.0f}", parse_mode="Markdown")
    try:
        await bot.send_message(target_uid, f"💰 *₦{amount:,.0f}* deducted from your balance by admin.\nNew balance: ₦{new_bal:,.0f}", parse_mode="Markdown")
    except Exception:
        pass


@router.message(Command("addbalance"))
async def admin_addbalance_cmd(message: Message, db, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Usage: /addbalance [user_id] [amount]"); return
    try:
        uid = int(parts[1]); amount = float(parts[2])
    except ValueError:
        await message.answer("❌ Invalid params."); return
    await update_user_balance(db, uid, amount)
    await log_transaction(db, uid, "admin_credit", amount, f"Admin credit ₦{amount:,.0f}")
    user = await get_user(db, uid)
    new_bal = user.get("balance", 0) if user else 0
    await message.answer(f"✅ +₦{amount:,.0f} to `{uid}`. Balance: ₦{new_bal:,.0f}", parse_mode="Markdown")
    try:
        await bot.send_message(uid, f"💰 *₦{amount:,.0f}* added to your balance!\nNew balance: ₦{new_bal:,.0f}", parse_mode="Markdown")
    except Exception:
        pass


@router.message(Command("deductbalance"))
async def admin_deductbalance_cmd(message: Message, db, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Usage: /deductbalance [user_id] [amount]"); return
    try:
        uid = int(parts[1]); amount = float(parts[2])
    except ValueError:
        await message.answer("❌ Invalid params."); return
    await update_user_balance(db, uid, -amount)
    await log_transaction(db, uid, "admin_deduct", -amount, f"Admin deduction ₦{amount:,.0f}")
    await message.answer(f"✅ -₦{amount:,.0f} from `{uid}`.", parse_mode="Markdown")


@router.message(Command("removeuser"))
async def admin_removeuser_cmd(message: Message, db, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /removeuser [user_id]"); return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer("❌ Invalid user ID."); return
    await delete_user(db, uid)
    await message.answer(f"🗑️ User `{uid}` removed.", parse_mode="Markdown")


@router.message(Command("resetuser"))
async def admin_resetuser_cmd(message: Message, db, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /resetuser [user_id]"); return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer("❌ Invalid user ID."); return
    await reset_user(db, uid)
    await message.answer(f"🔄 User `{uid}` onboarding reset.", parse_mode="Markdown")
    try:
        await bot.send_message(uid, "🔄 Your account has been reset. Type /start to begin again.")
    except Exception:
        pass


@router.message(Command("ban"))
async def admin_ban_cmd(message: Message, db, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /ban [user_id]"); return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer("❌ Invalid user ID."); return
    await ban_user(db, uid)
    await message.answer(f"🚫 User `{uid}` banned.", parse_mode="Markdown")
    try:
        await bot.send_message(uid, "🚫 Your account has been banned.")
    except Exception:
        pass


@router.message(Command("unban"))
async def admin_unban_cmd(message: Message, db, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /unban [user_id]"); return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer("❌ Invalid user ID."); return
    await unban_user(db, uid)
    await message.answer(f"✅ User `{uid}` unbanned.", parse_mode="Markdown")


@router.message(Command("bannedusers"))
async def admin_bannedusers(message: Message, db):
    if not is_admin(message.from_user.id):
        return
    users = await get_banned_users(db)
    if not users:
        await message.answer("No banned users."); return
    lines = ["🚫 *Banned Users:*\n"]
    for u in users[:30]:
        uname = f"@{u['username']}" if u.get("username") else str(u["telegram_id"])
        lines.append(f"• {uname} (`{u['telegram_id']}`)")
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("flaggedusers"))
async def admin_flaggedusers(message: Message, db):
    if not is_admin(message.from_user.id):
        return
    users = await get_flagged_users(db)
    if not users:
        await message.answer("No flagged users."); return
    lines = ["🚩 *Flagged Users:*\n"]
    for u in users[:30]:
        uname = f"@{u['username']}" if u.get("username") else str(u["telegram_id"])
        lines.append(f"• {uname} (`{u['telegram_id']}`)")
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("referralleaderboard"))
async def admin_referral_leaderboard(message: Message, db):
    if not is_admin(message.from_user.id):
        return
    top = await get_referral_leaderboard(db, limit=10)
    if not top:
        await message.answer("No referrals yet."); return
    lines = ["🏆 *Top 10 Referrers:*\n"]
    for i, u in enumerate(top, 1):
        uname = f"@{u['username']}" if u.get("username") else str(u["telegram_id"])
        lines.append(f"{i}. {uname} (`{u['telegram_id']}`) — {u['referral_count']} referral(s)")
    await message.answer("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────
# WITHDRAWALS
# ─────────────────────────────────────────

@router.message(Command("pendingwithdrawals"))
async def admin_pending_withdrawals(message: Message, db):
    if not is_admin(message.from_user.id):
        return
    withdrawals = await get_pending_withdrawals(db)
    if not withdrawals:
        await message.answer("✅ No pending withdrawals."); return
    for w in withdrawals[:10]:
        wid = str(w["_id"])
        uid = w["user_id"]
        method = w.get("method", "bank")
        if method == "airtime":
            detail = f"📱 {w.get('phone_number')} ({w.get('network')})"
        else:
            detail = f"🏦 {w.get('bank_name')} — {w.get('bank_account')}"
        await message.answer(
            f"💸 *Pending Withdrawal*\n\n"
            f"User: `{uid}`\n"
            f"Amount: ₦{w['amount']:,.0f}\n"
            f"{detail}\n"
            f"ID: `{wid}`",
            reply_markup=admin_withdrawal_keyboard(wid, uid),
            parse_mode="Markdown"
        )


@router.message(Command("approveall"))
async def admin_approveall(message: Message, db, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    withdrawals = await get_pending_withdrawals(db)
    if not withdrawals:
        await message.answer("No pending withdrawals."); return
    count = 0
    for w in withdrawals:
        wid = str(w["_id"])
        uid = w["user_id"]
        await update_withdrawal_status(db, wid, "paid")
        await increment_total_withdrawn(db, uid, w["amount"])
        count += 1
        try:
            await bot.send_message(uid, f"🎉 *Payment Sent!* ₦{w['amount']:,.0f} processed.", parse_mode="Markdown")
        except Exception:
            pass
    await message.answer(f"✅ Approved {count} withdrawal(s).")


@router.message(Command("withdrawalstats"))
async def admin_withdrawal_stats(message: Message, db):
    if not is_admin(message.from_user.id):
        return
    stats = await get_withdrawal_stats(db)
    total_paid = await get_total_paid_out(db)
    await message.answer(
        f"💸 *Withdrawal Stats*\n\n"
        f"⏳ Pending: {stats['pending']['count']} (₦{stats['pending']['total']:,.0f})\n"
        f"✅ Approved: {stats['approved']['count']} (₦{stats['approved']['total']:,.0f})\n"
        f"❌ Rejected: {stats['rejected']['count']} (₦{stats['rejected']['total']:,.0f})\n"
        f"💰 Paid: {stats['paid']['count']} (₦{stats['paid']['total']:,.0f})\n\n"
        f"🏆 Total paid out: ₦{total_paid:,.0f}",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# PENDING TASKS
# ─────────────────────────────────────────

@router.message(Command("pendingtasks"))
async def admin_pending_tasks(message: Message, db, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    pending = await get_pending_completions(db)
    if not pending:
        await message.answer("✅ No pending task completions."); return
    for c in pending[:10]:
        uid = c["user_id"]
        task_id = c["task_id"]
        task = await get_task_by_id(db, task_id)
        task_title = task["title"] if task else task_id
        reward = task["reward"] if task else 0
        proof_id = c.get("proof_file_id")
        text = (
            f"📋 *Pending Task Completion*\n\n"
            f"User: `{uid}`\n"
            f"Task: *{task_title}*\n"
            f"Reward: ₦{reward:,.0f}"
        )
        kb = admin_task_completion_keyboard(task_id, uid, task_id)
        if proof_id:
            await bot.send_photo(message.chat.id, photo=proof_id, caption=text,
                                 reply_markup=kb, parse_mode="Markdown")
        else:
            await message.answer(text, reply_markup=kb, parse_mode="Markdown")


# ─────────────────────────────────────────
# AD QUEUE
# ─────────────────────────────────────────

@router.message(Command("adqueue"))
async def admin_adqueue(message: Message, db, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    ads = await get_pending_review_ads(db)
    if not ads:
        await message.answer("✅ No ads pending review."); return
    for ad in ads[:5]:
        ad_id = str(ad["_id"])
        text = (
            f"⭐ *Premium Ad — Pending Review*\n\n"
            f"User: `{ad['creator_id']}`\n"
            f"Copy:\n{ad['copy']}\n\n"
            f"Ad ID: `{ad_id}`"
        )
        kb = admin_ad_review_keyboard(ad_id)
        if ad.get("image_file_id"):
            await bot.send_photo(message.chat.id, photo=ad["image_file_id"],
                                 caption=text, reply_markup=kb, parse_mode="Markdown")
        else:
            await message.answer(text, reply_markup=kb, parse_mode="Markdown")


# ─────────────────────────────────────────
# NOOP (page label button)
# ─────────────────────────────────────────

@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer()
