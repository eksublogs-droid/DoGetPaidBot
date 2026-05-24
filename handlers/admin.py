import logging
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from models.db import (
    add_task, delete_task, get_all_tasks, get_settings,
    update_setting, get_all_users, get_pending_withdrawals,
    get_pending_completions
)
from config.settings import settings

logger = logging.getLogger(__name__)
router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in settings.ADMIN_IDS


def _is_command(text: str) -> bool:
    """Returns True if message is a command — lets FSM steps escape cleanly."""
    return text is not None and text.startswith("/")


class AddTaskStates(StatesGroup):
    title = State()
    description = State()
    link = State()
    task_type = State()
    confirm_type = State()
    reward = State()
    onboarding = State()
    channel_id = State()


# ─────────────────────────────────────────
# /admin — Admin menu
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
        "/deltask `[id]` — Delete a task\n\n"
        "*Settings:*\n"
        "/setminwithdraw `[amount]` — Set min withdrawal\n"
        "/setreferralreward `[amount]` — Set referral reward\n"
        "/setrewardpool `[amount]` — Set total reward pool\n\n"
        "*Users:*\n"
        "/stats — Bot statistics\n"
        "/broadcast `[message]` — Message all users\n"
        "/ban `[user_id]` — Ban a user\n"
        "/unban `[user_id]` — Unban a user\n"
        "/addbalance `[user_id]` `[amount]` — Credit user\n\n"
        "*Withdrawals:*\n"
        "/pendingwithdrawals — View pending\n"
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
async def task_title(message: Message, state: FSMContext):
    if _is_command(message.text):
        await state.clear()
        await message.answer("⚠️ Task creation cancelled. Run /addtask to start again.")
        return
    await state.update_data(title=message.text.strip())
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
# LIST TASKS
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

    lines = ["📋 *All Active Tasks*\n"]
    for t in tasks:
        tag = "🟢 Onboarding" if t.get("onboarding") else "🔵 Dashboard"
        lines.append(
            f"{tag}\n"
            f"ID: `{t['_id']}`\n"
            f"Title: {t['title']}\n"
            f"Type: {t['task_type']} | Confirm: {t['confirm_type']}\n"
            f"Reward: ₦{t['reward']:,.0f}\n"
        )
    await message.answer("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────
# DELETE TASK
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

    await message.answer(
        f"📊 *Bot Statistics*\n\n"
        f"👥 Total users: {len(users)}\n"
        f"✅ Onboarded: {len(onboarded)}\n"
        f"💰 Total balance in system: ₦{total_balance:,.0f}\n"
        f"⏳ Pending withdrawals: {len(pending_w)}\n"
        f"📋 Pending task approvals: {len(pending_t)}\n\n"
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
# BAN / UNBAN
# ─────────────────────────────────────────

@router.message(Command("ban"))
async def ban_user(message: Message, state: FSMContext, db):
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
async def unban_user(message: Message, state: FSMContext, db):
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
# ADD BALANCE MANUALLY
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
        from models.db import update_user_balance
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
        from models.db import get_task_by_id
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
