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
        "onboarded": False,   # True after completing all onboarding tasks
        "banned": False,
        "joined_at": datetime.utcnow()
    }
    await db.users.insert_one(user)
    return user


async def update_user_balance(db: AsyncIOMotorDatabase, telegram_id: int, amount: float):
    """Add (positive) or deduct (negative) from balance."""
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$inc": {"balance": amount}}
    )


async def set_user_bank(db: AsyncIOMotorDatabase, telegram_id: int, account: str, bank_name: str, bank_code: str):
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"bank_account": account, "bank_name": bank_name, "bank_code": bank_code}}
    )


async def mark_onboarded(db: AsyncIOMotorDatabase, telegram_id: int):
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"onboarded": True}}
    )


async def increment_referral_count(db: AsyncIOMotorDatabase, telegram_id: int):
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$inc": {"referral_count": 1}}
    )


async def get_all_users(db: AsyncIOMotorDatabase):
    return await db.users.find({"banned": False}).to_list(length=None)


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
