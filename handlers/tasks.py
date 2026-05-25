import logging
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from models.db import (
    get_user, get_extra_tasks, get_completion, create_completion,
    approve_completion, reject_completion, update_user_balance,
    get_user_completions, get_task_by_id, increment_tasks_done,
    update_completion_proof, increment_task_slots_filled,
    increment_user_rejection_count, log_transaction, count_user_rejections
)
from utils.keyboards import tasks_keyboard, back_to_dashboard, admin_task_completion_keyboard
from config.settings import settings

logger = logging.getLogger(__name__)
router = Router()

TASK_COOLDOWN_SECONDS = 60          # minimum seconds between task submissions
MAX_REJECTIONS_BEFORE_FLAG = 3      # flag user after this many rejections


class TaskProofStates(StatesGroup):
    waiting_proof = State()     # waiting for screenshot/proof


# ─────────────────────────────────────────
# SHOW TASKS
# ─────────────────────────────────────────

@router.callback_query(F.data == "show_tasks")
async def show_tasks(callback: CallbackQuery, db):
    await callback.answer()
    user_id = callback.from_user.id

    user = await get_user(db, user_id)
    if not user or not user.get("onboarded"):
        await callback.message.answer("❌ Complete onboarding first.")
        return

    tasks = await get_extra_tasks(db)
    if not tasks:
        await callback.message.answer(
            "📭 No tasks available right now. Check back later!",
            reply_markup=back_to_dashboard()
        )
        return

    completions = await get_user_completions(db, user_id)
    completed_ids = [c["task_id"] for c in completions]

    # Also include pending
    pending_completions = await db.completions.find({"user_id": user_id, "status": "pending"}).to_list(length=None)
    pending_ids = [c["task_id"] for c in pending_completions]
    all_done_ids = list(set(completed_ids + pending_ids))

    await callback.message.answer(
        "✅ *Available Tasks*\n\n"
        "📌 = Pinned  |  ⏸ = Paused  |  🔴 = Full\n\n"
        "Complete tasks below to earn rewards:",
        reply_markup=tasks_keyboard(tasks, all_done_ids),
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# USER MARKS TASK AS DONE
# ─────────────────────────────────────────

@router.callback_query(F.data.startswith("task_done:"))
async def task_done(callback: CallbackQuery, state: FSMContext, db, bot: Bot):
    await callback.answer()
    user_id = callback.from_user.id
    task_id = callback.data.split(":")[1]

    # Anti-spam cooldown check
    last_submission_key = f"last_task_submit:{user_id}"
    fsm_data = await state.get_data()
    last_submit_ts = fsm_data.get(last_submission_key)
    if last_submit_ts:
        elapsed = (datetime.utcnow() - datetime.fromisoformat(last_submit_ts)).total_seconds()
        if elapsed < TASK_COOLDOWN_SECONDS:
            remaining = int(TASK_COOLDOWN_SECONDS - elapsed)
            await callback.message.answer(
                f"⏳ Please wait *{remaining}s* before submitting another task.",
                parse_mode="Markdown"
            )
            return

    # Check not already completed/pending
    existing = await get_completion(db, user_id, task_id)
    if existing:
        if existing["status"] == "approved":
            await callback.message.answer("✅ You already completed this task and were rewarded!")
        elif existing["status"] == "pending":
            await callback.message.answer("⏳ This task is already pending admin review.")
        return

    task = await get_task_by_id(db, task_id)
    if not task or not task.get("active"):
        await callback.message.answer("❌ Task not found or no longer available.")
        return

    if task.get("paused"):
        await callback.message.answer("⏸ This task is paused. Check back later.")
        return

    # Check deadline
    deadline = task.get("deadline")
    if deadline and datetime.utcnow() > deadline:
        await callback.message.answer("❌ This task has expired.")
        return

    # Check slots
    slots = task.get("slots")
    slots_filled = task.get("slots_filled", 0)
    if slots is not None and slots_filled >= slots:
        await callback.message.answer("🔴 This task is full. No more slots available.")
        return

    confirm_type = task.get("confirm_type", "manual")

    if confirm_type == "auto" and task.get("task_type") == "join_channel":
        # Auto verify
        channel_id = task.get("channel_id")
        if channel_id:
            try:
                member = await bot.get_chat_member(channel_id, user_id)
                if member.status in ["left", "kicked", "banned"]:
                    await callback.message.answer(
                        f"❌ You haven't joined *{task['title']}* yet!\n\nJoin and try again.",
                        parse_mode="Markdown"
                    )
                    return
            except Exception as e:
                logger.warning(f"Auto-verify failed: {e}")

        await create_completion(db, user_id, task_id, status="approved")
        await increment_task_slots_filled(db, task_id)
        reward = task.get("reward", 0)
        await update_user_balance(db, user_id, reward)
        await increment_tasks_done(db, user_id)
        await log_transaction(db, user_id, "task_reward", reward,
                              f"Task reward: {task['title']}", ref_id=task_id)

        # Update cooldown
        await state.update_data(**{last_submission_key: datetime.utcnow().isoformat()})

        # Check if task full after this fill
        if slots is not None and slots_filled + 1 >= slots:
            # Notify creator
            creator_id = task.get("creator_id")
            if creator_id:
                try:
                    await bot.send_message(
                        creator_id,
                        f"✅ Your task *{task['title']}* is now full! All {slots} slots have been filled.",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

        await callback.message.answer(
            f"🎉 Task completed! *+₦{reward:,.0f}* added to your balance.",
            reply_markup=back_to_dashboard(),
            parse_mode="Markdown"
        )

    else:
        # Manual — check if proof is required
        proof_instructions = task.get("proof_instructions", "")

        # Show proof reminder
        proof_reminder = (
            f"\n\n📸 *Proof Required:*\n{proof_instructions}" if proof_instructions
            else "\n\n📸 *Tip:* Take a screenshot as proof before submitting."
        )

        await create_completion(db, user_id, task_id, status="pending")
        await state.update_data(pending_task_id=task_id,
                                 **{last_submission_key: datetime.utcnow().isoformat()})

        await callback.message.answer(
            f"📋 *Task: {task['title']}*\n\n"
            f"Reward: ₦{task['reward']:,.0f}{proof_reminder}\n\n"
            f"Send a *screenshot or photo* as proof now, or tap Skip if no proof is needed:",
            reply_markup=_skip_proof_keyboard(task_id),
            parse_mode="Markdown"
        )
        await state.set_state(TaskProofStates.waiting_proof)


def _skip_proof_keyboard(task_id: str):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton  # noqa — needed here as function defined mid-module
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Submit Without Proof", callback_data=f"skip_proof:{task_id}")]
    ])


@router.message(TaskProofStates.waiting_proof, F.photo)
async def receive_task_proof(message: Message, state: FSMContext, db, bot: Bot):
    user_id = message.from_user.id
    data = await state.get_data()
    task_id = data.get("pending_task_id")
    await state.clear()

    if not task_id:
        await message.answer("❌ Something went wrong. Please try again from the task list.")
        return

    # Save proof file_id
    photo_file_id = message.photo[-1].file_id
    await update_completion_proof(db, user_id, task_id, photo_file_id)

    task = await get_task_by_id(db, task_id)
    user = await get_user(db, user_id)
    username = user.get("username", str(user_id)) if user else str(user_id)

    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_photo(
                admin_id,
                photo=photo_file_id,
                caption=(
                    f"📋 *Task Completion (with proof)*\n\n"
                    f"👤 User: @{username} (`{user_id}`)\n"
                    f"✅ Task: *{task['title'] if task else task_id}*\n"
                    f"💰 Reward: ₦{task['reward']:,.0f if task else 0}\n"
                    f"📸 Proof attached above"
                ),
                reply_markup=admin_task_completion_keyboard(task_id, user_id, task_id),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")

    await message.answer(
        f"✅ *Proof submitted!*\n\n"
        f"Task: *{task['title'] if task else 'Unknown'}*\n"
        f"Reward: ₦{task['reward']:,.0f if task else 0}\n\n"
        f"⏳ Admin will review and credit you shortly.",
        reply_markup=back_to_dashboard(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("skip_proof:"))
async def skip_proof(callback: CallbackQuery, state: FSMContext, db, bot: Bot):
    await callback.answer()
    user_id = callback.from_user.id
    task_id = callback.data.split(":")[1]
    await state.clear()

    task = await get_task_by_id(db, task_id)
    user = await get_user(db, user_id)
    username = user.get("username", str(user_id)) if user else str(user_id)

    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📋 *Task Completion (no proof)*\n\n"
                f"👤 User: @{username} (`{user_id}`)\n"
                f"✅ Task: *{task['title'] if task else task_id}*\n"
                f"💰 Reward: ₦{task['reward']:,.0f if task else 0}",
                reply_markup=admin_task_completion_keyboard(task_id, user_id, task_id),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")

    await callback.message.answer(
        f"⏳ *Task submitted for review!*\n\n"
        f"Task: *{task['title'] if task else 'Unknown'}*\n"
        f"Reward: ₦{task['reward']:,.0f if task else 0}\n\n"
        f"An admin will verify and credit your account shortly.",
        reply_markup=back_to_dashboard(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "already_done")
async def already_done(callback: CallbackQuery):
    await callback.answer("You already completed this task!", show_alert=True)


@router.callback_query(F.data == "task_unavailable")
async def task_unavailable(callback: CallbackQuery):
    await callback.answer("This task is paused or full.", show_alert=True)


@router.callback_query(F.data == "task_info")
async def task_info(callback: CallbackQuery):
    await callback.answer()


# ─────────────────────────────────────────
# ADMIN APPROVES TASK
# ─────────────────────────────────────────

@router.callback_query(F.data.startswith("approve_task:"))
async def admin_approve_task(callback: CallbackQuery, db, bot: Bot):
    if callback.from_user.id not in settings.ADMIN_IDS:
        await callback.answer("❌ Unauthorized", show_alert=True)
        return

    await callback.answer("✅ Approved!")
    parts = callback.data.split(":")
    user_id = int(parts[1])
    task_id = parts[2]

    existing = await get_completion(db, user_id, task_id)
    if existing and existing["status"] == "approved":
        await callback.message.reply("Already approved.")
        return

    task = await get_task_by_id(db, task_id)
    if not task:
        await callback.message.reply("Task not found.")
        return

    await approve_completion(db, user_id, task_id)
    await increment_task_slots_filled(db, task_id)
    reward = task.get("reward", 0)
    await update_user_balance(db, user_id, reward)
    await increment_tasks_done(db, user_id)
    await log_transaction(db, user_id, "task_reward", reward,
                          f"Task approved: {task['title']}", ref_id=task_id)

    # Check if task is now full
    slots = task.get("slots")
    slots_filled = task.get("slots_filled", 0) + 1
    if slots is not None and slots_filled >= slots:
        creator_id = task.get("creator_id")
        if creator_id:
            try:
                await bot.send_message(
                    creator_id,
                    f"✅ Your task *{task['title']}* quota is now full! ({slots}/{slots} slots filled)",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    try:
        await callback.message.edit_text(
            callback.message.text + f"\n\n✅ *Approved by @{callback.from_user.username}*",
            parse_mode="Markdown"
        )
    except Exception:
        pass

    try:
        await bot.send_message(
            user_id,
            f"🎉 *Task Approved!*\n\n"
            f"Your task *{task['title']}* has been verified.\n"
            f"*+₦{reward:,.0f}* has been added to your balance!",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning(f"Could not notify user {user_id}: {e}")


# ─────────────────────────────────────────
# ADMIN REJECTS TASK
# ─────────────────────────────────────────

@router.callback_query(F.data.startswith("reject_task:"))
async def admin_reject_task(callback: CallbackQuery, db, bot: Bot):
    if callback.from_user.id not in settings.ADMIN_IDS:
        await callback.answer("❌ Unauthorized", show_alert=True)
        return

    await callback.answer("❌ Rejected")
    parts = callback.data.split(":")
    user_id = int(parts[1])
    task_id = parts[2]

    await reject_completion(db, user_id, task_id)
    await increment_user_rejection_count(db, user_id)

    # Check if user should be flagged
    rejection_count = await count_user_rejections(db, user_id)
    if rejection_count >= MAX_REJECTIONS_BEFORE_FLAG:
        user = await get_user(db, user_id)
        if user and not user.get("flagged"):
            await db.users.update_one({"telegram_id": user_id}, {"$set": {"flagged": True}})
            for admin_id in settings.ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"🚩 *User Auto-Flagged*\n\n"
                        f"User `{user_id}` (@{user.get('username', 'unknown')}) has been flagged "
                        f"after {rejection_count} task rejections.",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

    try:
        await callback.message.edit_text(
            callback.message.text + f"\n\n❌ *Rejected by @{callback.from_user.username}*",
            parse_mode="Markdown"
        )
    except Exception:
        pass

    try:
        task = await get_task_by_id(db, task_id)
        task_name = task["title"] if task else "the task"
        await bot.send_message(
            user_id,
            f"❌ *Task Rejected*\n\n"
            f"Your submission for *{task_name}* was not approved.\n"
            f"Please complete the task properly and try again.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning(f"Could not notify user {user_id}: {e}")
