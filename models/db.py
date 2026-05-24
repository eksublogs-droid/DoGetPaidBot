from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId


# ─────────────────────────────────────────
# USER HELPERS
# ─────────────────────────────────────────

async def get_user(db: AsyncIOMotorDatabase, telegram_id: int):
    return await db.users.find_one({"telegram_id": telegram_id})


async def create_user(db: AsyncIOMotorDatabase, telegram_id: int, username: str, referred_by: int = None):
    existing = await get_user(db, telegram_id)
    if existing:
        return existing

    user = {
        "telegram_id": telegram_id,
        "username": username or f"user_{telegram_id}",
        "balance": 0,
        "referred_by": referred_by,
        "referral_count": 0,
        "bank_account": None,
        "bank_name": None,
        "bank_code": None,
        "onboarded": False,       # True after completing all onboarding tasks
        "onboarded_at": None,     # Timestamp when onboarding was completed
        "flagged": False,         # True if onboarding completed in under 5 seconds
        "captcha_answer": None,   # Temporarily stores correct captcha answer during onboarding
        "banned": False,
        "joined_at": datetime.utcnow()
    }
    await db.users.insert_one(user)
    return user


async def count_all_users(db: AsyncIOMotorDatabase) -> int:
    """Return total number of registered users."""
    return await db.users.count_documents({})


async def update_user_balance(db: AsyncIOMotorDatabase, telegram_id: int, amount: float):
    """Add (positive) or deduct (negative) from balance."""
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$inc": {"balance": amount}}
    )


async def set_user_balance(db: AsyncIOMotorDatabase, telegram_id: int, amount: float):
    """Overwrite balance with an exact value."""
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"balance": amount}}
    )


async def set_user_bank(db: AsyncIOMotorDatabase, telegram_id: int, account: str, bank_name: str, bank_code: str):
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"bank_account": account, "bank_name": bank_name, "bank_code": bank_code}}
    )


async def mark_onboarded(db: AsyncIOMotorDatabase, telegram_id: int, flagged: bool = False):
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {
            "onboarded": True,
            "onboarded_at": datetime.utcnow(),
            "flagged": flagged,
            "captcha_answer": None   # Clear captcha once onboarding is done
        }}
    )


async def set_captcha_answer(db: AsyncIOMotorDatabase, telegram_id: int, answer: str):
    """Store the correct captcha answer temporarily."""
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"captcha_answer": answer}}
    )


async def clear_captcha_answer(db: AsyncIOMotorDatabase, telegram_id: int):
    """Clear captcha answer from DB."""
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"captcha_answer": None}}
    )


async def ban_user(db: AsyncIOMotorDatabase, telegram_id: int):
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"banned": True}}
    )


async def unban_user(db: AsyncIOMotorDatabase, telegram_id: int):
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"banned": False}}
    )


async def increment_referral_count(db: AsyncIOMotorDatabase, telegram_id: int):
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$inc": {"referral_count": 1}}
    )


async def get_all_users(db: AsyncIOMotorDatabase):
    return await db.users.find({"banned": False}).to_list(length=None)


async def get_all_users_paginated(db: AsyncIOMotorDatabase, page: int = 0, page_size: int = 20):
    """Return users in chunks of page_size by page number (0-indexed)."""
    skip = page * page_size
    users = await db.users.find({}).skip(skip).limit(page_size).to_list(length=None)
    total = await db.users.count_documents({})
    return users, total


async def get_banned_users(db: AsyncIOMotorDatabase):
    """Return all users where banned=True."""
    return await db.users.find({"banned": True}).to_list(length=None)


async def get_flagged_users(db: AsyncIOMotorDatabase):
    """Return all users where flagged=True."""
    return await db.users.find({"flagged": True}).to_list(length=None)


async def get_referral_leaderboard(db: AsyncIOMotorDatabase, limit: int = 10):
    """Return top users by referral_count, descending."""
    return await db.users.find(
        {"referral_count": {"$gt": 0}},
        {"telegram_id": 1, "username": 1, "referral_count": 1}
    ).sort("referral_count", -1).limit(limit).to_list(length=None)


async def get_withdrawal_stats(db: AsyncIOMotorDatabase) -> dict:
    """Return counts and total amounts for each withdrawal status."""
    pipeline = [
        {"$group": {
            "_id": "$status",
            "count": {"$sum": 1},
            "total": {"$sum": "$amount"}
        }}
    ]
    rows = await db.withdrawals.aggregate(pipeline).to_list(length=None)
    stats = {"pending": {"count": 0, "total": 0.0},
             "approved": {"count": 0, "total": 0.0},
             "rejected": {"count": 0, "total": 0.0},
             "paid": {"count": 0, "total": 0.0}}
    for r in rows:
        key = r["_id"]
        if key in stats:
            stats[key] = {"count": r["count"], "total": r["total"]}
    return stats


async def get_total_paid_out(db: AsyncIOMotorDatabase) -> float:
    """Return total amount from withdrawals with status 'paid'."""
    pipeline = [
        {"$match": {"status": "paid"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]
    result = await db.withdrawals.aggregate(pipeline).to_list(length=None)
    return result[0]["total"] if result else 0.0


async def task_title_exists(db: AsyncIOMotorDatabase, title: str) -> bool:
    """Check if an active task with the same title already exists (case-insensitive)."""
    import re
    pattern = re.compile(f"^{re.escape(title.strip())}$", re.IGNORECASE)
    existing = await db.tasks.find_one({"active": True, "title": {"$regex": pattern}})
    return existing is not None


async def reset_user(db: AsyncIOMotorDatabase, telegram_id: int):
    """Reset onboarding state so user goes through onboarding again."""
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {
            "onboarded": False,
            "onboarded_at": None,
            "flagged": False,
            "captcha_answer": None
        }}
    )


# ─────────────────────────────────────────
# TASK HELPERS
# ─────────────────────────────────────────

async def get_all_tasks(db: AsyncIOMotorDatabase):
    return await db.tasks.find({"active": True}).to_list(length=None)


async def get_onboarding_tasks(db: AsyncIOMotorDatabase):
    """Tasks shown during onboarding (join channels/groups)."""
    return await db.tasks.find({"active": True, "onboarding": True}).to_list(length=None)


async def get_extra_tasks(db: AsyncIOMotorDatabase):
    """Ongoing tasks shown in dashboard."""
    return await db.tasks.find({"active": True, "onboarding": False}).to_list(length=None)


async def add_task(db: AsyncIOMotorDatabase, task_data: dict):
    task = {
        "title": task_data["title"],
        "description": task_data.get("description", ""),
        "link": task_data.get("link", ""),
        "channel_id": task_data.get("channel_id"),        # for auto-verify
        "task_type": task_data["task_type"],               # join_channel | visit_link | whatsapp | custom
        "confirm_type": task_data.get("confirm_type", "manual"),  # auto | manual
        "reward": float(task_data["reward"]),
        "onboarding": task_data.get("onboarding", False),  # show during onboarding or dashboard
        "active": True,
        "created_at": datetime.utcnow()
    }
    result = await db.tasks.insert_one(task)
    task["_id"] = result.inserted_id
    return task


async def delete_task(db: AsyncIOMotorDatabase, task_id: str):
    await db.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {"active": False}})


async def get_task_by_id(db: AsyncIOMotorDatabase, task_id: str):
    return await db.tasks.find_one({"_id": ObjectId(task_id)})


async def toggle_task_onboarding(db: AsyncIOMotorDatabase, task_id: str):
    """Flip the onboarding field between True and False."""
    task = await get_task_by_id(db, task_id)
    if not task:
        return None
    new_val = not task.get("onboarding", False)
    await db.tasks.update_one(
        {"_id": ObjectId(task_id)},
        {"$set": {"onboarding": new_val}}
    )
    return new_val


async def update_task_reward(db: AsyncIOMotorDatabase, task_id: str, reward: float):
    """Update the reward amount for a task."""
    await db.tasks.update_one(
        {"_id": ObjectId(task_id)},
        {"$set": {"reward": reward}}
    )


# ─────────────────────────────────────────
# TASK COMPLETION HELPERS
# ─────────────────────────────────────────

async def get_completion(db: AsyncIOMotorDatabase, user_id: int, task_id: str):
    return await db.completions.find_one({"user_id": user_id, "task_id": task_id})


async def create_completion(db: AsyncIOMotorDatabase, user_id: int, task_id: str, status: str = "pending"):
    existing = await get_completion(db, user_id, task_id)
    if existing:
        return existing
    completion = {
        "user_id": user_id,
        "task_id": task_id,
        "status": status,   # pending | approved | rejected
        "completed_at": datetime.utcnow()
    }
    await db.completions.insert_one(completion)
    return completion


async def approve_completion(db: AsyncIOMotorDatabase, user_id: int, task_id: str):
    await db.completions.update_one(
        {"user_id": user_id, "task_id": task_id},
        {"$set": {"status": "approved", "approved_at": datetime.utcnow()}}
    )


async def get_user_completions(db: AsyncIOMotorDatabase, user_id: int):
    return await db.completions.find({"user_id": user_id, "status": "approved"}).to_list(length=None)


async def get_pending_completions(db: AsyncIOMotorDatabase):
    """All pending manual task completions for admin review."""
    return await db.completions.find({"status": "pending"}).to_list(length=None)


# ─────────────────────────────────────────
# WITHDRAWAL HELPERS
# ─────────────────────────────────────────

async def create_withdrawal(db: AsyncIOMotorDatabase, user_id: int, amount: float, bank_account: str, bank_name: str, bank_code: str):
    withdrawal = {
        "user_id": user_id,
        "amount": amount,
        "bank_account": bank_account,
        "bank_name": bank_name,
        "bank_code": bank_code,
        "status": "pending",   # pending | approved | rejected | paid
        "requested_at": datetime.utcnow()
    }
    result = await db.withdrawals.insert_one(withdrawal)
    withdrawal["_id"] = result.inserted_id
    return withdrawal


async def get_pending_withdrawals(db: AsyncIOMotorDatabase):
    return await db.withdrawals.find({"status": "pending"}).to_list(length=None)


async def update_withdrawal_status(db: AsyncIOMotorDatabase, withdrawal_id: str, status: str, reason: str = None):
    update = {"status": status, "processed_at": datetime.utcnow()}
    if reason:
        update["reason"] = reason
    await db.withdrawals.update_one({"_id": ObjectId(withdrawal_id)}, {"$set": update})


async def get_user_withdrawals(db: AsyncIOMotorDatabase, user_id: int):
    return await db.withdrawals.find({"user_id": user_id}).sort("requested_at", -1).to_list(length=10)


# ─────────────────────────────────────────
# SETTINGS HELPERS
# ─────────────────────────────────────────

async def get_settings(db: AsyncIOMotorDatabase):
    s = await db.settings.find_one({"_id": "global"})
    if not s:
        # Default settings
        s = {
            "_id": "global",
            "min_withdraw": 500,
            "referral_reward": 100,
            "total_reward_pool": 500000,
            "bot_name": "MOREMONEE"
        }
        await db.settings.insert_one(s)
    return s


async def update_setting(db: AsyncIOMotorDatabase, key: str, value):
    await db.settings.update_one(
        {"_id": "global"},
        {"$set": {key: value}},
        upsert=True
    )


# ─────────────────────────────────────────
# DELETE USER
# ─────────────────────────────────────────

async def delete_user(db: AsyncIOMotorDatabase, telegram_id: int):
    """Wipe all user data from every collection."""
    await db.users.delete_one({"telegram_id": telegram_id})
    await db.completions.delete_many({"user_id": telegram_id})
    await db.withdrawals.delete_many({"user_id": telegram_id})
