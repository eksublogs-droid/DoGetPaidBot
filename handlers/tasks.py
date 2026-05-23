import logging
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command

from models.db import (
    get_user, get_extra_tasks, get_completion, create_completion,
    approve_completion, update_user_balance, get_user_completions,
    get_task_by_id
)
from utils.keyboards import tasks_keyboard, back_to_dashboard, admin_task_completion_keyboard
from config.settings import settings

logger = logging.getLogger(__name__)
router = Router()


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

    # Get user's completed task IDs
    completions = await get_user_completions(db, user_id)
    completed_ids = [c["task_id"] for c in completions]

    await callback.message.answer(
        "✅ *Available Tasks*\n\nComplete tasks below to earn rewards:",
        reply_markup=tasks_keyboard(tasks, completed_ids),
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# USER MARKS TASK AS DONE
# ─────────────────────────────────────────

@router.callback_query(F.data.startswith("task_done:"))
async def task_done(callback: CallbackQuery, db, bot: Bot):
    await callback.answer()
    user_id = callback.from_user.id
    task_id = callback.data.split(":")[1]

    # Check not already completed
    existing = await get_completion(db, user_id, task_id)
    if existing:
        if existing["status"] == "approved":
            await callback.message.answer("✅ You already completed this task and were rewarded!")
        elif existing["status"] == "pending":
            await callback.message.answer("⏳ This task is pending admin review.")
        return

    task = await get_task_by_id(db, task_id)
    if not task:
        await callback.message.answer("❌ Task not found.")
        return

    confirm_type = task.get("confirm_type", "manual")

    if confirm_type == "auto" and task.get("task_type") == "join_channel":
        # Auto verify via Telegram API
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

        # Auto approve and credit
        await create_completion(db, user_id, task_id, status="approved")
        reward = task.get("reward", 0)
        await update_user_balance(db, user_id, reward)
        await callback.message.answer(
            f"🎉 Task completed! *+₦{reward:,.0f}* added to your balance.",
            reply_markup=back_to_dashboard(),
            parse_mode="Markdown"
        )

    else:
        # Manual confirm — create pending completion, notify admin
        await create_completion(db, user_id, task_id, status="pending")

        user = await get_user(db, user_id)
        username = user.get("username", str(user_id))

        # Notify all admins
        for admin_id in settings.ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"📋 *New Task Completion Request*\n\n"
                    f"👤 User: @{username} (`{user_id}`)\n"
                    f"✅ Task: *{task['title']}*\n"
                    f"💰 Reward: ₦{task['reward']:,.0f}\n"
                    f"🔖 Type: {task.get('task_type', 'custom')}",
                    reply_markup=admin_task_completion_keyboard(task_id, user_id, task_id),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Could not notify admin {admin_id}: {e}")

        await callback.message.answer(
            f"⏳ *Task submitted for review!*\n\n"
            f"Task: *{task['title']}*\n"
            f"Reward: ₦{task['reward']:,.0f}\n\n"
            f"An admin will verify and credit your account shortly.",
            reply_markup=back_to_dashboard(),
            parse_mode="Markdown"
        )


@router.callback_query(F.data == "already_done")
async def already_done(callback: CallbackQuery):
    await callback.answer("You already completed this task!", show_alert=True)


@router.callback_query(F.data == "task_info")
async def task_info(callback: CallbackQuery):
    await callback.answer()


# ─────────────────────────────────────────
# ADMIN APPROVES TASK COMPLETION
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

    # Check not already processed
    existing = await get_completion(db, user_id, task_id)
    if existing and existing["status"] == "approved":
        await callback.message.edit_text("Already approved.")
        return

    task = await get_task_by_id(db, task_id)
    if not task:
        await callback.message.edit_text("Task not found.")
        return

    await approve_completion(db, user_id, task_id)
    reward = task.get("reward", 0)
    await update_user_balance(db, user_id, reward)

    # Edit admin message
    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ *Approved by @{callback.from_user.username}*",
        parse_mode="Markdown"
    )

    # Notify user
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


@router.callback_query(F.data.startswith("reject_task:"))
async def admin_reject_task(callback: CallbackQuery, db, bot: Bot):
    if callback.from_user.id not in settings.ADMIN_IDS:
        await callback.answer("❌ Unauthorized", show_alert=True)
        return

    await callback.answer("❌ Rejected")
    parts = callback.data.split(":")
    user_id = int(parts[1])
    task_id = parts[2]

    await db.completions.delete_one({"user_id": user_id, "task_id": task_id})

    await callback.message.edit_text(
        callback.message.text + f"\n\n❌ *Rejected by @{callback.from_user.username}*",
        parse_mode="Markdown"
    )

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
