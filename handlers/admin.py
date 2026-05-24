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
    update_setting, get_all_users, get_pending_withdrawals,
    get_pending_completions, get_user, update_user_balance,
    delete_user, reset_user, ban_user, unban_user,
    get_all_users_paginated, get_banned_users, get_flagged_users,
    update_withdrawal_status, toggle_task_onboarding,
    update_task_reward, get_task_by_id, count_all_users,
    get_referral_leaderboard, get_withdrawal_stats, get_total_paid_out,
    task_title_exists
)
from config.settings import settings

logger = logging.getLogger(__name__)
router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in settings.ADMIN_IDS


def _is_command(text: str) -> bool:
    """Returns True if message is a command — lets FSM steps escape cleanly."""
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


# ─────────────────────────────────────────
# KEYBOARD HELPERS (admin-only, defined here to avoid cluttering keyboards.py)
# ─────────────────────────────────────────

def _users_list_keyboard(users: list, page: int, total: int, page_size: int = 20) -> InlineKeyboardMarkup:
    buttons = []
    for u in users:
        uname = f"@{u['username']}" if u.get("username") else str(u["telegram_id"])
        balance = u.get("balance", 0)
        buttons.append([
            InlineKeyboardButton(
                text=f"{uname} — ₦{balance:,.0f}",
                callback_data=f"admin_user:{u['telegram_id']}"
            )
        ])
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
        tag = "🟢" if t.get("onboarding") else "🔵"
        buttons.append([
            InlineKeyboardButton(
                text=f"{tag} {t['title']} — ₦{t['reward']:,.0f}",
                callback_data=f"admin_task:{t['_id']}"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _task_detail_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ Delete", callback_data=f"admin_deltask:{task_id}")],
        [InlineKeyboardButton(text="✏️ Edit Reward", callback_data=f"admin_editreward:{task_id}")],
        [InlineKeyboardButton(text="🔄 Toggle Onboarding/Dashboard", callback_data=f"admin_toggleob:{task_id}")],
        [InlineKeyboardButton(text="🔙 Back to Tasks", callback_data="admin_listtasks")],
    ])


def _cleartasks_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Yes, clear all", callback_data="admin_cleartasks_confirm"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="admin_cleartasks_cancel"),
        ]
    ])


# ─────────────────────────────────────────
# /admin — Admin menu (unchanged, no new commands added)
# ─────────────────────────────────────────

@router.message(Command("admin"))
async def admin_menu(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🛠 *Admin Panel*\n\n"
        "*Task Management:*\n"
        "/addtask — Add a new task\n"
        "/listtasks — View all tasks\n"
        "/deltask `[id]` — Delete a task\n"
        "/cleartasks — Delete all tasks\n\n"
        "*Settings:*\n"
        "/setbotname `[name]` — Change bot name\n"
        "/setminwithdraw `[amount]` — Set min withdrawal\n"
        "/setreferralreward `[amount]` — Set referral reward\n"
        "/setrewardpool `[amount]` — Set total reward pool\n"
        "/settings — View current settings\n\n"
        "*Users:*\n"
        "/users — Paginated user list\n"
        "/stats — Bot statistics\n"
        "/totalusers — Total user count\n"
        "/broadcast `[message]` — Message all users\n"
        "/broadcastphoto — Broadcast a photo\n"
        "/ban `[user_id]` — Ban a user\n"
        "/unban `[user_id]` — Unban a user\n"
        "/bannedusers — View banned users\n"
        "/flaggedusers — View flagged users\n"
        "/referralleaderboard — Top 10 referrers\n"
        "/addbalance `[user_id]` `[amount]` — Credit user\n"
        "/deductbalance `[user_id]` `[amount]` — Deduct from user\n"
        "/removeuser `[user_id]` — Delete a user\n"
        "/resetuser `[user_id]` — Reset user onboarding\n\n"
        "*Withdrawals:*\n"
        "/pendingwithdrawals — View pending\n"
        "/approveall — Approve all pending\n"
        "/rejectwithdrawal `[id]` `[reason]` — Reject one\n"
        "/withdrawalstats — Withdrawal summary\n\n"
        "*Tasks:*\n"
        "/pendingtasks — View pending task approvals",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# ADD TASK (FSM)
# ─────────────────────────────────────────

@router.message(Command("addtask"))
async def add_task_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("📋 *Add New Task*\n\nTask title:", parse_mode="Markdown")
    await state.set_state(AddTaskStates.title)


@router.message(Command("canceladdtask"))
async def cancel_add_task(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Task creation cancelled.")


@router.message(AddTaskStates.title)
async def task_title(message: Message, state: FSMContext, db):
    if _is_command(message.text):
        await state.clear()
        await message.answer("⚠️ Task creation cancelled. Run /addtask to start again.")
        return
    title = message.text.strip()
    if await task_title_exists(db, title):
        await message.answer(
            f"⚠️ A task titled *{title}* already exists.\n\n"
            "Send a different title or /canceladdtask to stop:",
            parse_mode="Markdown"
        )
        return
    await state.update_data(title=title)
    await message.answer("Description (or send `-` to skip):")
    await state.set_state(AddTaskStates.description)


@router.message(AddTaskStates.description)
async def task_description(message: Message, state: FSMContext):
    if _is_command(message.text):
        await state.clear()
        await message.answer("⚠️ Task creation cancelled. Run /addtask to start again.")
        return
    desc = message.text.strip()
    await state.update_data(description="" if desc == "-" else desc)
    await message.answer("Link/URL for the task (or `-` if none):")
    await state.set_state(AddTaskStates.link)


@router.message(AddTaskStates.link)
async def task_link(message: Message, state: FSMContext):
    if _is_command(message.text):
        await state.clear()
        await message.answer("⚠️ Task creation cancelled. Run /addtask to start again.")
        return
    link = message.text.strip()
    await state.update_data(link="" if link == "-" else link)
    await message.answer(
        "Task type:\n"
        "`join_channel` — Join Telegram channel\n"
        "`join_group` — Join Telegram group\n"
        "`whatsapp` — Join WhatsApp group\n"
        "`visit_link` — Visit a website\n"
        "`custom` — Any other task\n\n"
        "Send one of the above:",
        parse_mode="Markdown"
    )
    await state.set_state(AddTaskStates.task_type)


@router.message(AddTaskStates.task_type)
async def task_type_handler(message: Message, state: FSMContext):
    if _is_command(message.text):
        await state.clear()
        await message.answer("⚠️ Task creation cancelled. Run /addtask to start again.")
        return
    valid = ["join_channel", "join_group", "whatsapp", "visit_link", "custom"]
    t = message.text.strip().lower()
    if t not in valid:
        await message.answer(f"❌ Invalid. Choose one: {', '.join(valid)}")
        return
    await state.update_data(task_type=t)
    await message.answer(
        "Confirmation type:\n"
        "`auto` — Bot verifies automatically (Telegram channels only)\n"
        "`manual` — Admin manually approves\n\n"
        "Send `auto` or `manual`:",
        parse_mode="Markdown"
    )
    await state.set_state(AddTaskStates.confirm_type)


@router.message(AddTaskStates.confirm_type)
async def task_confirm_type(message: Message, state: FSMContext):
    if _is_command(message.text):
        await state.clear()
        await message.answer("⚠️ Task creation cancelled. Run /addtask to start again.")
        return
    ct = message.text.strip().lower()
    if ct not in ["auto", "manual"]:
        await message.answer("Send `auto` or `manual`:", parse_mode="Markdown")
        return
    await state.update_data(confirm_type=ct)
    await message.answer("Reward amount in ₦ (numbers only):")
    await state.set_state(AddTaskStates.reward)


@router.message(AddTaskStates.reward)
async def task_reward(message: Message, state: FSMContext):
    if _is_command(message.text):
        await state.clear()
        await message.answer("⚠️ Task creation cancelled. Run /addtask to start again.")
        return
    try:
        reward = float(message.text.strip().replace(",", ""))
    except ValueError:
        await message.answer("❌ Numbers only. Try again:")
        return
    await state.update_data(reward=reward)
    await message.answer(
        "Is this an *onboarding* task? (shown during /start)\n"
        "Send `yes` or `no`:",
        parse_mode="Markdown"
    )
    await state.set_state(AddTaskStates.onboarding)


@router.message(AddTaskStates.onboarding)
async def task_onboarding(message: Message, state: FSMContext, db):
    if _is_command(message.text):
        await state.clear()
        await message.answer("⚠️ Task creation cancelled. Run /addtask to start again.")
        return
    val = message.text.strip().lower()
    if val not in ["yes", "no"]:
        await message.answer("Send `yes` or `no`:", parse_mode="Markdown")
        return

    is_onboarding = val == "yes"
    await state.update_data(onboarding=is_onboarding)

    data = await state.get_data()
    if is_onboarding and data.get("task_type") == "join_channel" and data.get("confirm_type") == "auto":
        await message.answer(
            "For auto-verify, enter the *channel ID* (e.g. `-1001234567890`).\n"
            "Send `-` if you don't know it yet:",
            parse_mode="Markdown"
        )
        await state.set_state(AddTaskStates.channel_id)
    else:
        await _save_task(message, state, db)


@router.message(AddTaskStates.channel_id)
async def task_channel_id(message: Message, state: FSMContext, db):
    if _is_command(message.text):
        await state.clear()
        await message.answer("⚠️ Task creation cancelled. Run /addtask to start again.")
        return
    cid = message.text.strip()
    await state.update_data(channel_id=None if cid == "-" else cid)
    await _save_task(message, state, db)


async def _save_task(message: Message, state: FSMContext, db):
    data = await state.get_data()
    task_data = {
        "title": data["title"],
        "description": data.get("description", ""),
        "link": data.get("link", ""),
        "task_type": data["task_type"],
        "confirm_type": data["confirm_type"],
        "reward": data["reward"],
        "onboarding": data["onboarding"],
        "channel_id": data.get("channel_id")
    }
    task = await add_task(db, task_data)
    await state.clear()
    await message.answer(
        f"✅ Task *{task['title']}* saved!\n"
        f"ID: `{task['_id']}`",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# LIST TASKS — inline buttons
# ─────────────────────────────────────────

@router.message(Command("listtasks"))
async def list_tasks(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    tasks = await get_all_tasks(db)
    if not tasks:
        await message.answer("No active tasks.")
        return
    await message.answer(
        "📋 *All Active Tasks*\nTap a task to manage it:",
        reply_markup=_tasks_list_keyboard(tasks),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin_listtasks")
async def cb_list_tasks(callback: CallbackQuery, db):
    tasks = await get_all_tasks(db)
    if not tasks:
        await callback.message.edit_text("No active tasks.")
        await callback.answer()
        return
    await callback.message.edit_text(
        "📋 *All Active Tasks*\nTap a task to manage it:",
        reply_markup=_tasks_list_keyboard(tasks),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_task:"))
async def cb_task_detail(callback: CallbackQuery, db):
    task_id = callback.data.split(":", 1)[1]
    task = await get_task_by_id(db, task_id)
    if not task:
        await callback.answer("Task not found.", show_alert=True)
        return
    tag = "🟢 Onboarding" if task.get("onboarding") else "🔵 Dashboard"
    text = (
        f"📋 *Task Detail*\n\n"
        f"Title: *{task['title']}*\n"
        f"Type: {task.get('task_type', 'N/A')}\n"
        f"Confirm: {task.get('confirm_type', 'N/A')}\n"
        f"Reward: ₦{task['reward']:,.0f}\n"
        f"Placement: {tag}\n"
        f"Link: {task.get('link') or 'None'}\n"
        f"Description: {task.get('description') or 'None'}\n"
        f"ID: `{task['_id']}`"
    )
    await callback.message.edit_text(
        text,
        reply_markup=_task_detail_keyboard(task_id),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_deltask:"))
async def cb_delete_task(callback: CallbackQuery, db):
    task_id = callback.data.split(":", 1)[1]
    await delete_task(db, task_id)
    tasks = await get_all_tasks(db)
    if not tasks:
        await callback.message.edit_text("✅ Task deleted. No active tasks remaining.")
    else:
        await callback.message.edit_text(
            "✅ Task deleted.\n\n📋 *All Active Tasks*\nTap a task to manage it:",
            reply_markup=_tasks_list_keyboard(tasks),
            parse_mode="Markdown"
        )
    await callback.answer("Task deleted.")


@router.callback_query(F.data.startswith("admin_toggleob:"))
async def cb_toggle_onboarding(callback: CallbackQuery, db):
    task_id = callback.data.split(":", 1)[1]
    new_val = await toggle_task_onboarding(db, task_id)
    label = "Onboarding" if new_val else "Dashboard"
    await callback.answer(f"✅ Task moved to {label}.", show_alert=True)
    # Refresh task detail view
    task = await get_task_by_id(db, task_id)
    if task:
        tag = "🟢 Onboarding" if task.get("onboarding") else "🔵 Dashboard"
        text = (
            f"📋 *Task Detail*\n\n"
            f"Title: *{task['title']}*\n"
            f"Type: {task.get('task_type', 'N/A')}\n"
            f"Confirm: {task.get('confirm_type', 'N/A')}\n"
            f"Reward: ₦{task['reward']:,.0f}\n"
            f"Placement: {tag}\n"
            f"Link: {task.get('link') or 'None'}\n"
            f"Description: {task.get('description') or 'None'}\n"
            f"ID: `{task['_id']}`"
        )
        await callback.message.edit_text(
            text,
            reply_markup=_task_detail_keyboard(task_id),
            parse_mode="Markdown"
        )


@router.callback_query(F.data.startswith("admin_editreward:"))
async def cb_edit_reward_start(callback: CallbackQuery, state: FSMContext):
    task_id = callback.data.split(":", 1)[1]
    await state.update_data(edit_reward_task_id=task_id)
    await state.set_state(EditTaskRewardStates.new_reward)
    await callback.message.answer("✏️ Enter the new reward amount in ₦ (numbers only):")
    await callback.answer()


@router.message(EditTaskRewardStates.new_reward)
async def fsm_edit_reward(message: Message, state: FSMContext, db):
    if _is_command(message.text):
        await state.clear()
        await message.answer("⚠️ Edit cancelled.")
        return
    try:
        new_reward = float(message.text.strip().replace(",", ""))
    except ValueError:
        await message.answer("❌ Numbers only. Try again:")
        return
    data = await state.get_data()
    task_id = data.get("edit_reward_task_id")
    await update_task_reward(db, task_id, new_reward)
    await state.clear()
    await message.answer(f"✅ Reward updated to ₦{new_reward:,.0f}")


# ─────────────────────────────────────────
# DELETE TASK (command)
# ─────────────────────────────────────────

@router.message(Command("deltask"))
async def del_task(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /deltask `[task_id]`", parse_mode="Markdown")
        return
    task_id = parts[1].strip()
    await delete_task(db, task_id)
    await message.answer(f"✅ Task `{task_id}` deleted.", parse_mode="Markdown")


# ─────────────────────────────────────────
# CLEAR ALL TASKS
# ─────────────────────────────────────────

@router.message(Command("cleartasks"))
async def cleartasks_prompt(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "⚠️ Are you sure you want to delete *all* active tasks? This cannot be undone.",
        reply_markup=_cleartasks_confirm_keyboard(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin_cleartasks_confirm")
async def cb_cleartasks_confirm(callback: CallbackQuery, db):
    await db.tasks.update_many({"active": True}, {"$set": {"active": False}})
    await callback.message.edit_text("✅ All tasks have been cleared.")
    await callback.answer()


@router.callback_query(F.data == "admin_cleartasks_cancel")
async def cb_cleartasks_cancel(callback: CallbackQuery):
    await callback.message.edit_text("❌ Cancelled. No tasks were deleted.")
    await callback.answer()


# ─────────────────────────────────────────
# USERS — paginated list
# ─────────────────────────────────────────

@router.message(Command("users"))
async def cmd_users(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    users, total = await get_all_users_paginated(db, page=0)
    if not users:
        await message.answer("No users found.")
        return
    await message.answer(
        f"👥 *Users* (total: {total})\nTap a user to manage them:",
        reply_markup=_users_list_keyboard(users, 0, total),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("admin_users_page:"))
async def cb_users_page(callback: CallbackQuery, db):
    page = int(callback.data.split(":", 1)[1])
    users, total = await get_all_users_paginated(db, page=page)
    if not users:
        await callback.answer("No users on this page.", show_alert=True)
        return
    await callback.message.edit_text(
        f"👥 *Users* (total: {total})\nTap a user to manage them:",
        reply_markup=_users_list_keyboard(users, page, total),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user:"))
async def cb_user_detail(callback: CallbackQuery, db):
    uid = int(callback.data.split(":", 1)[1])
    user = await get_user(db, uid)
    if not user:
        await callback.answer("User not found.", show_alert=True)
        return

    joined = user.get("joined_at")
    joined_str = joined.strftime("%b %d, %Y") if joined else "N/A"
    referred_by = user.get("referred_by") or "None"
    onboarded_at = user.get("onboarded_at")
    onboarded_str = onboarded_at.strftime("%b %d, %Y %H:%M") if onboarded_at else "No"

    text = (
        f"👤 *User Detail*\n\n"
        f"ID: `{user['telegram_id']}`\n"
        f"Username: @{user.get('username', 'N/A')}\n"
        f"Balance: ₦{user.get('balance', 0):,.0f}\n"
        f"Referrals: {user.get('referral_count', 0)}\n"
        f"Referred by: {referred_by}\n"
        f"Joined: {joined_str}\n"
        f"Onboarded: {onboarded_str}\n"
        f"Banned: {'Yes' if user.get('banned') else 'No'}\n"
        f"Flagged: {'Yes' if user.get('flagged') else 'No'}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=_user_detail_keyboard(uid),
        parse_mode="Markdown"
    )
    await callback.answer()


# ─── User action: Add Balance ───

@router.callback_query(F.data.startswith("admin_addbal:"))
async def cb_addbal_start(callback: CallbackQuery, state: FSMContext):
    uid = int(callback.data.split(":", 1)[1])
    await state.update_data(action_user_id=uid)
    await state.set_state(UserBalanceStates.add_amount)
    await callback.message.answer(f"➕ Enter amount to *add* to user `{uid}`'s balance (₦):", parse_mode="Markdown")
    await callback.answer()


@router.message(UserBalanceStates.add_amount)
async def fsm_addbal(message: Message, state: FSMContext, db, bot: Bot):
    if _is_command(message.text):
        await state.clear()
        await message.answer("⚠️ Action cancelled.")
        return
    try:
        amount = float(message.text.strip().replace(",", ""))
    except ValueError:
        await message.answer("❌ Numbers only. Try again:")
        return
    data = await state.get_data()
    uid = data["action_user_id"]
    await update_user_balance(db, uid, amount)
    await state.clear()
    await message.answer(f"✅ Added ₦{amount:,.0f} to user `{uid}`.", parse_mode="Markdown")
    try:
        await bot.send_message(uid, f"💰 ₦{amount:,.0f} has been added to your balance by admin!")
    except Exception:
        pass


# ─── User action: Deduct Balance ───

@router.callback_query(F.data.startswith("admin_deductbal:"))
async def cb_deductbal_start(callback: CallbackQuery, state: FSMContext):
    uid = int(callback.data.split(":", 1)[1])
    await state.update_data(action_user_id=uid)
    await state.set_state(UserBalanceStates.deduct_amount)
    await callback.message.answer(f"➖ Enter amount to *deduct* from user `{uid}`'s balance (₦):", parse_mode="Markdown")
    await callback.answer()


@router.message(UserBalanceStates.deduct_amount)
async def fsm_deductbal(message: Message, state: FSMContext, db, bot: Bot):
    if _is_command(message.text):
        await state.clear()
        await message.answer("⚠️ Action cancelled.")
        return
    try:
        amount = float(message.text.strip().replace(",", ""))
    except ValueError:
        await message.answer("❌ Numbers only. Try again:")
        return
    data = await state.get_data()
    uid = data["action_user_id"]
    await update_user_balance(db, uid, -amount)
    await state.clear()
    await message.answer(f"✅ Deducted ₦{amount:,.0f} from user `{uid}`.", parse_mode="Markdown")
    try:
        await bot.send_message(uid, f"⚠️ ₦{amount:,.0f} has been deducted from your balance by admin.")
    except Exception:
        pass


# ─── User action: Ban (confirm step) ───

@router.callback_query(F.data.startswith("admin_ban_confirm:"))
async def cb_ban_confirm(callback: CallbackQuery):
    uid = int(callback.data.split(":", 1)[1])
    await callback.message.edit_text(
        f"⚠️ Are you sure you want to *ban* user `{uid}`?",
        reply_markup=_confirm_action_keyboard("ban", uid),
        parse_mode="Markdown"
    )
    await callback.answer()


# ─── User action: Remove (confirm step) ───

@router.callback_query(F.data.startswith("admin_remove_confirm:"))
async def cb_remove_confirm(callback: CallbackQuery):
    uid = int(callback.data.split(":", 1)[1])
    await callback.message.edit_text(
        f"⚠️ Are you sure you want to *permanently remove* user `{uid}` and all their data?",
        reply_markup=_confirm_action_keyboard("remove", uid),
        parse_mode="Markdown"
    )
    await callback.answer()


# ─── User action: Ban ───

@router.callback_query(F.data.startswith("admin_ban:"))
async def cb_ban_user(callback: CallbackQuery, db, bot: Bot):
    uid = int(callback.data.split(":", 1)[1])
    await ban_user(db, uid)
    await callback.answer(f"✅ User {uid} banned.", show_alert=True)
    try:
        await bot.send_message(uid, "🚫 Your account has been banned.")
    except Exception:
        pass
    # Refresh user detail
    user = await get_user(db, uid)
    if user:
        await _refresh_user_detail(callback, user)


# ─── User action: Unban ───

@router.callback_query(F.data.startswith("admin_unban:"))
async def cb_unban_user(callback: CallbackQuery, db, bot: Bot):
    uid = int(callback.data.split(":", 1)[1])
    await unban_user(db, uid)
    await callback.answer(f"✅ User {uid} unbanned.", show_alert=True)
    try:
        await bot.send_message(uid, "✅ Your account has been unbanned.")
    except Exception:
        pass
    user = await get_user(db, uid)
    if user:
        await _refresh_user_detail(callback, user)


# ─── User action: Remove ───

@router.callback_query(F.data.startswith("admin_remove:"))
async def cb_remove_user(callback: CallbackQuery, db, bot: Bot):
    uid = int(callback.data.split(":", 1)[1])
    try:
        await bot.send_message(uid, "🗑️ Your account and all associated data have been removed by admin.")
    except Exception:
        pass
    await delete_user(db, uid)
    await callback.message.edit_text(f"✅ User `{uid}` and all their data have been removed.", parse_mode="Markdown")
    await callback.answer()


# ─── User action: Reset ───

@router.callback_query(F.data.startswith("admin_reset:"))
async def cb_reset_user(callback: CallbackQuery, db, bot: Bot):
    uid = int(callback.data.split(":", 1)[1])
    await reset_user(db, uid)
    await callback.answer(f"✅ User {uid} reset. They will redo onboarding.", show_alert=True)
    try:
        await bot.send_message(uid, "🔄 Your onboarding has been reset. Please use /start to begin again.")
    except Exception:
        pass
    user = await get_user(db, uid)
    if user:
        await _refresh_user_detail(callback, user)


async def _refresh_user_detail(callback: CallbackQuery, user: dict):
    uid = user["telegram_id"]
    joined = user.get("joined_at")
    joined_str = joined.strftime("%b %d, %Y") if joined else "N/A"
    referred_by = user.get("referred_by") or "None"
    onboarded_at = user.get("onboarded_at")
    onboarded_str = onboarded_at.strftime("%b %d, %Y %H:%M") if onboarded_at else "No"
    text = (
        f"👤 *User Detail*\n\n"
        f"ID: `{uid}`\n"
        f"Username: @{user.get('username', 'N/A')}\n"
        f"Balance: ₦{user.get('balance', 0):,.0f}\n"
        f"Referrals: {user.get('referral_count', 0)}\n"
        f"Referred by: {referred_by}\n"
        f"Joined: {joined_str}\n"
        f"Onboarded: {onboarded_str}\n"
        f"Banned: {'Yes' if user.get('banned') else 'No'}\n"
        f"Flagged: {'Yes' if user.get('flagged') else 'No'}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=_user_detail_keyboard(uid),
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# /deductbalance [user_id] [amount]
# ─────────────────────────────────────────

@router.message(Command("deductbalance"))
async def cmd_deduct_balance(message: Message, state: FSMContext, db, bot: Bot):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Usage: /deductbalance `[user_id]` `[amount]`", parse_mode="Markdown")
        return
    try:
        uid = int(parts[1])
        amount = float(parts[2])
        await update_user_balance(db, uid, -amount)
        await message.answer(f"✅ Deducted ₦{amount:,.0f} from user `{uid}`.", parse_mode="Markdown")
        try:
            await bot.send_message(uid, f"⚠️ ₦{amount:,.0f} has been deducted from your balance by admin.")
        except Exception:
            pass
    except ValueError:
        await message.answer("❌ Invalid inputs.")


# ─────────────────────────────────────────
# /removeuser [user_id]
# ─────────────────────────────────────────

@router.message(Command("removeuser"))
async def cmd_remove_user(message: Message, state: FSMContext, db, bot: Bot):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /removeuser `[user_id]`", parse_mode="Markdown")
        return
    try:
        uid = int(parts[1])
        try:
            await bot.send_message(uid, "🗑️ Your account and all associated data have been removed by admin.")
        except Exception:
            pass
        await delete_user(db, uid)
        await message.answer(f"✅ User `{uid}` and all their data have been removed.", parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ Invalid user ID.")


# ─────────────────────────────────────────
# /totalusers
# ─────────────────────────────────────────

@router.message(Command("totalusers"))
async def cmd_total_users(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    total = await count_all_users(db)
    await message.answer(f"👥 Total registered users: *{total}*", parse_mode="Markdown")


# ─────────────────────────────────────────
# /bannedusers
# ─────────────────────────────────────────

@router.message(Command("bannedusers"))
async def cmd_banned_users(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    banned = await get_banned_users(db)
    if not banned:
        await message.answer("✅ No banned users.")
        return
    lines = ["🚫 *Banned Users*\n"]
    for u in banned:
        uname = f"@{u['username']}" if u.get("username") else "N/A"
        lines.append(f"ID: `{u['telegram_id']}` — {uname}")
    await message.answer("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────
# /resetuser [user_id]
# ─────────────────────────────────────────

@router.message(Command("resetuser"))
async def cmd_reset_user(message: Message, state: FSMContext, db, bot: Bot):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /resetuser `[user_id]`", parse_mode="Markdown")
        return
    try:
        uid = int(parts[1])
        await reset_user(db, uid)
        await message.answer(f"✅ User `{uid}` has been reset. They will redo onboarding.", parse_mode="Markdown")
        try:
            await bot.send_message(uid, "🔄 Your onboarding has been reset. Please use /start to begin again.")
        except Exception:
            pass
    except ValueError:
        await message.answer("❌ Invalid user ID.")


# ─────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────

@router.message(Command("setminwithdraw"))
async def set_min_withdraw(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /setminwithdraw `500`", parse_mode="Markdown")
        return
    try:
        amount = float(parts[1])
        await update_setting(db, "min_withdraw", amount)
        await message.answer(f"✅ Minimum withdrawal set to ₦{amount:,.0f}")
    except ValueError:
        await message.answer("❌ Invalid amount.")


@router.message(Command("setreferralreward"))
async def set_referral_reward(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /setreferralreward `100`", parse_mode="Markdown")
        return
    try:
        amount = float(parts[1])
        await update_setting(db, "referral_reward", amount)
        await message.answer(f"✅ Referral reward set to ₦{amount:,.0f}")
    except ValueError:
        await message.answer("❌ Invalid amount.")


@router.message(Command("setrewardpool"))
async def set_reward_pool(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /setrewardpool `500000`", parse_mode="Markdown")
        return
    try:
        amount = float(parts[1])
        await update_setting(db, "total_reward_pool", amount)
        await message.answer(f"✅ Total reward pool set to ₦{amount:,.0f}")
    except ValueError:
        await message.answer("❌ Invalid amount.")


# ─────────────────────────────────────────
# /settings — show all current settings
# ─────────────────────────────────────────

@router.message(Command("settings"))
async def cmd_settings(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    s = await get_settings(db)
    await message.answer(
        f"⚙️ *Current Settings*\n\n"
        f"Bot Name: *{s.get('bot_name', 'N/A')}*\n"
        f"Min Withdrawal: ₦{s.get('min_withdraw', 0):,.0f}\n"
        f"Referral Reward: ₦{s.get('referral_reward', 0):,.0f}\n"
        f"Total Reward Pool: ₦{s.get('total_reward_pool', 0):,.0f}",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# STATS
# ─────────────────────────────────────────

@router.message(Command("stats"))
async def stats(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return

    users = await get_all_users(db)
    onboarded = [u for u in users if u.get("onboarded")]
    total_balance = sum(u.get("balance", 0) for u in users)
    pending_w = await get_pending_withdrawals(db)
    pending_t = await get_pending_completions(db)
    bot_settings = await get_settings(db)
    total_paid = await get_total_paid_out(db)

    await message.answer(
        f"📊 *Bot Statistics*\n\n"
        f"👥 Total users: {len(users)}\n"
        f"✅ Onboarded: {len(onboarded)}\n"
        f"💰 Total balance in system: ₦{total_balance:,.0f}\n"
        f"⏳ Pending withdrawals: {len(pending_w)}\n"
        f"📋 Pending task approvals: {len(pending_t)}\n"
        f"💸 Total paid out: ₦{total_paid:,.0f}\n\n"
        f"⚙️ *Settings*\n"
        f"Min withdrawal: ₦{bot_settings.get('min_withdraw', 500):,.0f}\n"
        f"Referral reward: ₦{bot_settings.get('referral_reward', 100):,.0f}\n"
        f"Reward pool: ₦{bot_settings.get('total_reward_pool', 500000):,.0f}",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# BROADCAST
# ─────────────────────────────────────────

@router.message(Command("broadcast"))
async def broadcast(message: Message, state: FSMContext, db, bot: Bot):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    text = message.text.replace("/broadcast", "", 1).strip()
    if not text:
        await message.answer("Usage: /broadcast Your message here")
        return

    users = await get_all_users(db)
    sent, failed = 0, 0
    await message.answer(f"📡 Broadcasting to {len(users)} users...")

    for user in users:
        try:
            await bot.send_message(user["telegram_id"], text)
            sent += 1
        except Exception:
            failed += 1

    await message.answer(f"✅ Broadcast done.\nSent: {sent} | Failed: {failed}")


# ─────────────────────────────────────────
# /broadcastphoto — FSM: photo → caption → send
# ─────────────────────────────────────────

@router.message(Command("broadcastphoto"))
async def broadcastphoto_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("📸 Send the photo you want to broadcast:")
    await state.set_state(BroadcastPhotoStates.photo)


@router.message(BroadcastPhotoStates.photo)
async def broadcastphoto_get_photo(message: Message, state: FSMContext):
    if _is_command(message.text):
        await state.clear()
        await message.answer("⚠️ Broadcast photo cancelled.")
        return
    if not message.photo:
        await message.answer("❌ Please send a photo (image file).")
        return
    photo_id = message.photo[-1].file_id
    await state.update_data(broadcast_photo_id=photo_id)
    await message.answer("✏️ Now send the caption for this photo (or `-` for no caption):")
    await state.set_state(BroadcastPhotoStates.caption)


@router.message(BroadcastPhotoStates.caption)
async def broadcastphoto_send(message: Message, state: FSMContext, db, bot: Bot):
    if _is_command(message.text):
        await state.clear()
        await message.answer("⚠️ Broadcast photo cancelled.")
        return
    caption = message.text.strip()
    if caption == "-":
        caption = None
    data = await state.get_data()
    photo_id = data.get("broadcast_photo_id")
    await state.clear()

    users = await get_all_users(db)
    sent, failed = 0, 0
    await message.answer(f"📡 Broadcasting photo to {len(users)} users...")

    for user in users:
        try:
            await bot.send_photo(user["telegram_id"], photo=photo_id, caption=caption)
            sent += 1
        except Exception:
            failed += 1

    await message.answer(f"✅ Photo broadcast done.\nSent: {sent} | Failed: {failed}")


# ─────────────────────────────────────────
# BAN / UNBAN (command)
# ─────────────────────────────────────────

@router.message(Command("ban"))
async def ban_user_cmd(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /ban `[user_id]`", parse_mode="Markdown")
        return
    uid = int(parts[1])
    await db.users.update_one({"telegram_id": uid}, {"$set": {"banned": True}})
    await message.answer(f"✅ User `{uid}` banned.", parse_mode="Markdown")


@router.message(Command("unban"))
async def unban_user_cmd(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /unban `[user_id]`", parse_mode="Markdown")
        return
    uid = int(parts[1])
    await db.users.update_one({"telegram_id": uid}, {"$set": {"banned": False}})
    await message.answer(f"✅ User `{uid}` unbanned.", parse_mode="Markdown")


# ─────────────────────────────────────────
# ADD BALANCE MANUALLY (command)
# ─────────────────────────────────────────

@router.message(Command("addbalance"))
async def add_balance(message: Message, state: FSMContext, db, bot: Bot):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Usage: /addbalance `[user_id]` `[amount]`", parse_mode="Markdown")
        return
    try:
        uid = int(parts[1])
        amount = float(parts[2])
        await update_user_balance(db, uid, amount)
        await message.answer(f"✅ Added ₦{amount:,.0f} to user `{uid}`", parse_mode="Markdown")
        try:
            await bot.send_message(uid, f"💰 ₦{amount:,.0f} has been added to your balance by admin!")
        except Exception:
            pass
    except ValueError:
        await message.answer("❌ Invalid inputs.")


# ─────────────────────────────────────────
# PENDING WITHDRAWALS LIST
# ─────────────────────────────────────────

@router.message(Command("pendingwithdrawals"))
async def pending_withdrawals(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    pending = await get_pending_withdrawals(db)
    if not pending:
        await message.answer("✅ No pending withdrawals.")
        return
    for w in pending:
        from utils.keyboards import admin_withdrawal_keyboard
        await message.answer(
            f"💸 *Withdrawal Request*\n\n"
            f"User ID: `{w['user_id']}`\n"
            f"Amount: ₦{w['amount']:,.0f}\n"
            f"Bank: {w['bank_name']}\n"
            f"Account: {w['bank_account']}\n"
            f"Date: {w['requested_at'].strftime('%b %d, %Y %H:%M')}",
            reply_markup=admin_withdrawal_keyboard(str(w["_id"]), w["user_id"]),
            parse_mode="Markdown"
        )


# ─────────────────────────────────────────
# /approveall — approve all pending withdrawals
# ─────────────────────────────────────────

@router.message(Command("approveall"))
async def cmd_approveall(message: Message, state: FSMContext, db, bot: Bot):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    pending = await get_pending_withdrawals(db)
    if not pending:
        await message.answer("✅ No pending withdrawals to approve.")
        return
    approved_count = 0
    for w in pending:
        wid = str(w["_id"])
        uid = w["user_id"]
        amount = w["amount"]
        await update_withdrawal_status(db, wid, "approved")
        await update_user_balance(db, uid, amount)
        approved_count += 1
        try:
            await bot.send_message(
                uid,
                f"✅ Your withdrawal of ₦{amount:,.0f} has been approved and credited to your account!"
            )
        except Exception:
            pass
    await message.answer(f"✅ Approved and credited {approved_count} withdrawal(s).")


# ─────────────────────────────────────────
# /rejectwithdrawal [id] [reason]
# ─────────────────────────────────────────

@router.message(Command("rejectwithdrawal"))
async def cmd_reject_withdrawal(message: Message, state: FSMContext, db, bot: Bot):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(None, 2)
    if len(parts) < 3:
        await message.answer("Usage: /rejectwithdrawal `[withdrawal_id]` `[reason]`", parse_mode="Markdown")
        return
    wid = parts[1].strip()
    reason = parts[2].strip()
    withdrawal = await db.withdrawals.find_one({"_id": __import__("bson").ObjectId(wid)})
    if not withdrawal:
        await message.answer("❌ Withdrawal not found.")
        return
    await update_withdrawal_status(db, wid, "rejected", reason=reason)
    uid = withdrawal["user_id"]
    amount = withdrawal["amount"]
    try:
        await bot.send_message(
            uid,
            f"❌ Your withdrawal of ₦{amount:,.0f} was rejected.\nReason: {reason}"
        )
    except Exception:
        pass
    await message.answer(f"✅ Withdrawal `{wid}` rejected. User notified.", parse_mode="Markdown")


# ─────────────────────────────────────────
# PENDING TASK APPROVALS
# ─────────────────────────────────────────

@router.message(Command("pendingtasks"))
async def pending_tasks(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    pending = await get_pending_completions(db)
    if not pending:
        await message.answer("✅ No pending task approvals.")
        return
    for c in pending:
        from utils.keyboards import admin_task_completion_keyboard
        task = await get_task_by_id(db, c["task_id"])
        task_title = task["title"] if task else c["task_id"]
        reward = task["reward"] if task else 0
        await message.answer(
            f"📋 *Pending Task Approval*\n\n"
            f"User ID: `{c['user_id']}`\n"
            f"Task: *{task_title}*\n"
            f"Reward: ₦{reward:,.0f}\n"
            f"Submitted: {c['completed_at'].strftime('%b %d, %Y %H:%M')}",
            reply_markup=admin_task_completion_keyboard(c["task_id"], c["user_id"], c["task_id"]),
            parse_mode="Markdown"
        )


@router.message(Command("setbotname"))
async def set_bot_name_start(message: Message, state: FSMContext, db):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    # Support inline: /setbotname MyBotName
    parts = message.text.split(None, 1)
    if len(parts) >= 2 and parts[1].strip():
        name = parts[1].strip()
        await update_setting(db, "bot_name", name)
        await message.answer(f"✅ Bot name updated to *{name}*", parse_mode="Markdown")
        return
    await message.answer("✏️ Send the new bot name:")
    await state.set_state(SetBotNameStates.waiting_name)


@router.message(SetBotNameStates.waiting_name)
async def fsm_set_bot_name(message: Message, state: FSMContext, db):
    if _is_command(message.text):
        await state.clear()
        await message.answer("⚠️ Cancelled.")
        return
    name = message.text.strip()
    if not name:
        await message.answer("❌ Name cannot be empty. Try again:")
        return
    await update_setting(db, "bot_name", name)
    await state.clear()
    await message.answer(f"✅ Bot name updated to *{name}*", parse_mode="Markdown")


# ─────────────────────────────────────────
# /flaggedusers
# ─────────────────────────────────────────

@router.message(Command("flaggedusers"))
async def cmd_flagged_users(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    flagged = await get_flagged_users(db)
    if not flagged:
        await message.answer("✅ No flagged users.")
        return
    lines = ["🚩 *Flagged Users*\n"]
    for u in flagged:
        uname = f"@{u['username']}" if u.get("username") else "N/A"
        onboarded_at = u.get("onboarded_at")
        ts = onboarded_at.strftime("%b %d, %Y %H:%M") if onboarded_at else "N/A"
        lines.append(f"ID: `{u['telegram_id']}` — {uname} (onboarded: {ts})")
    await message.answer("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────
# /referralleaderboard
# ─────────────────────────────────────────

@router.message(Command("referralleaderboard"))
async def cmd_referral_leaderboard(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    top = await get_referral_leaderboard(db, limit=10)
    if not top:
        await message.answer("📊 No referral data yet.")
        return
    lines = ["🏆 *Top 10 Referrers*\n"]
    for i, u in enumerate(top, 1):
        uname = f"@{u['username']}" if u.get("username") else str(u["telegram_id"])
        lines.append(f"{i}. {uname} — {u['referral_count']} referral(s)")
    await message.answer("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────
# /withdrawalstats
# ─────────────────────────────────────────

@router.message(Command("withdrawalstats"))
async def cmd_withdrawal_stats(message: Message, state: FSMContext, db):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    s = await get_withdrawal_stats(db)
    total_paid = await get_total_paid_out(db)
    await message.answer(
        f"📊 *Withdrawal Statistics*\n\n"
        f"⏳ Pending: {s['pending']['count']} (₦{s['pending']['total']:,.0f})\n"
        f"✅ Approved: {s['approved']['count']} (₦{s['approved']['total']:,.0f})\n"
        f"💰 Paid Out: {s['paid']['count']} (₦{s['paid']['total']:,.0f})\n"
        f"❌ Rejected: {s['rejected']['count']} (₦{s['rejected']['total']:,.0f})\n\n"
        f"*Total Ever Paid Out: ₦{total_paid:,.0f}*",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# noop callback (pagination label button)
# ─────────────────────────────────────────

@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()
