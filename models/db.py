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
        "onboarded": False,
        "onboarded_at": None,
        "flagged": False,
        "captcha_answer": None,
        "banned": False,
        "notifications_on": True,
        "default_withdraw_method": "bank",
        "tasks_done": 0,
        "total_withdrawn": 0.0,
        "joined_at": datetime.utcnow()
    }
    await db.users.insert_one(user)
    return user


async def count_all_users(db: AsyncIOMotorDatabase) -> int:
    return await db.users.count_documents({})


async def update_user_balance(db: AsyncIOMotorDatabase, telegram_id: int, amount: float):
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$inc": {"balance": amount}}
    )


async def set_user_balance(db: AsyncIOMotorDatabase, telegram_id: int, amount: float):
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
            "captcha_answer": None
        }}
    )


async def set_captcha_answer(db: AsyncIOMotorDatabase, telegram_id: int, answer: str):
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"captcha_answer": answer}}
    )


async def clear_captcha_answer(db: AsyncIOMotorDatabase, telegram_id: int):
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"captcha_answer": None}}
    )


async def ban_user(db: AsyncIOMotorDatabase, telegram_id: int):
    await db.users.update_one({"telegram_id": telegram_id}, {"$set": {"banned": True}})


async def unban_user(db: AsyncIOMotorDatabase, telegram_id: int):
    await db.users.update_one({"telegram_id": telegram_id}, {"$set": {"banned": False}})


async def increment_referral_count(db: AsyncIOMotorDatabase, telegram_id: int):
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$inc": {"referral_count": 1}}
    )


async def get_all_users(db: AsyncIOMotorDatabase):
    return await db.users.find({"banned": False}).to_list(length=None)


async def get_all_users_paginated(db: AsyncIOMotorDatabase, page: int = 0, page_size: int = 20):
    skip = page * page_size
    users = await db.users.find({}).skip(skip).limit(page_size).to_list(length=None)
    total = await db.users.count_documents({})
    return users, total


async def get_banned_users(db: AsyncIOMotorDatabase):
    return await db.users.find({"banned": True}).to_list(length=None)


async def get_flagged_users(db: AsyncIOMotorDatabase):
    return await db.users.find({"flagged": True}).to_list(length=None)


async def get_referral_leaderboard(db: AsyncIOMotorDatabase, limit: int = 10):
    return await db.users.find(
        {"referral_count": {"$gt": 0}},
        {"telegram_id": 1, "username": 1, "referral_count": 1}
    ).sort("referral_count", -1).limit(limit).to_list(length=None)


async def get_withdrawal_stats(db: AsyncIOMotorDatabase) -> dict:
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
    pipeline = [
        {"$match": {"status": "paid"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]
    result = await db.withdrawals.aggregate(pipeline).to_list(length=None)
    return result[0]["total"] if result else 0.0


async def task_title_exists(db: AsyncIOMotorDatabase, title: str) -> bool:
    import re
    pattern = re.compile(f"^{re.escape(title.strip())}$", re.IGNORECASE)
    existing = await db.tasks.find_one({"active": True, "title": {"$regex": pattern}})
    return existing is not None


async def reset_user(db: AsyncIOMotorDatabase, telegram_id: int):
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {
            "onboarded": False,
            "onboarded_at": None,
            "flagged": False,
            "captcha_answer": None
        }}
    )


async def update_user_profile(db: AsyncIOMotorDatabase, telegram_id: int, updates: dict):
    """Generic update for user profile fields."""
    await db.users.update_one({"telegram_id": telegram_id}, {"$set": updates})


async def increment_tasks_done(db: AsyncIOMotorDatabase, telegram_id: int):
    await db.users.update_one({"telegram_id": telegram_id}, {"$inc": {"tasks_done": 1}})


async def increment_total_withdrawn(db: AsyncIOMotorDatabase, telegram_id: int, amount: float):
    await db.users.update_one({"telegram_id": telegram_id}, {"$inc": {"total_withdrawn": amount}})


# ─────────────────────────────────────────
# TASK HELPERS
# ─────────────────────────────────────────

async def get_all_tasks(db: AsyncIOMotorDatabase):
    return await db.tasks.find({"active": True}).to_list(length=None)


async def get_onboarding_tasks(db: AsyncIOMotorDatabase):
    return await db.tasks.find({"active": True, "onboarding": True}).to_list(length=None)


async def get_extra_tasks(db: AsyncIOMotorDatabase):
    """Pinned tasks first, then by creation date descending."""
    return await db.tasks.find(
        {"active": True, "onboarding": False}
    ).sort([("pinned", -1), ("created_at", -1)]).to_list(length=None)


async def add_task(db: AsyncIOMotorDatabase, task_data: dict):
    task = {
        "title": task_data["title"],
        "description": task_data.get("description", ""),
        "link": task_data.get("link", ""),
        "channel_id": task_data.get("channel_id"),
        "task_type": task_data["task_type"],
        "confirm_type": task_data.get("confirm_type", "manual"),
        "reward": float(task_data["reward"]),
        "slots": task_data.get("slots"),           # None = unlimited
        "slots_filled": 0,
        "proof_instructions": task_data.get("proof_instructions", ""),
        "deadline": task_data.get("deadline"),      # datetime or None
        "pinned": task_data.get("pinned", False),
        "creator_id": task_data.get("creator_id"),  # telegram_id of task creator (if user-created)
        "creator_paid": task_data.get("creator_paid", False),
        "onboarding": task_data.get("onboarding", False),
        "active": True,
        "paused": False,
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
    task = await get_task_by_id(db, task_id)
    if not task:
        return None
    new_val = not task.get("onboarding", False)
    await db.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {"onboarding": new_val}})
    return new_val


async def toggle_task_pause(db: AsyncIOMotorDatabase, task_id: str):
    task = await get_task_by_id(db, task_id)
    if not task:
        return None
    new_val = not task.get("paused", False)
    await db.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {"paused": new_val}})
    return new_val


async def toggle_task_pin(db: AsyncIOMotorDatabase, task_id: str):
    task = await get_task_by_id(db, task_id)
    if not task:
        return None
    new_val = not task.get("pinned", False)
    await db.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {"pinned": new_val}})
    return new_val


async def update_task_reward(db: AsyncIOMotorDatabase, task_id: str, reward: float):
    await db.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {"reward": reward}})


async def increment_task_slots_filled(db: AsyncIOMotorDatabase, task_id: str):
    await db.tasks.update_one({"_id": ObjectId(task_id)}, {"$inc": {"slots_filled": 1}})


async def get_tasks_by_creator(db: AsyncIOMotorDatabase, creator_id: int):
    return await db.tasks.find({"creator_id": creator_id}).sort("created_at", -1).to_list(length=None)


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
        "status": status,
        "proof_file_id": None,
        "submitted_at": datetime.utcnow(),
        "completed_at": datetime.utcnow() if status == "approved" else None,
        "rejection_count": 0,
    }
    await db.completions.insert_one(completion)
    return completion


async def update_completion_proof(db: AsyncIOMotorDatabase, user_id: int, task_id: str, proof_file_id: str):
    await db.completions.update_one(
        {"user_id": user_id, "task_id": task_id},
        {"$set": {"proof_file_id": proof_file_id, "submitted_at": datetime.utcnow()}}
    )


async def approve_completion(db: AsyncIOMotorDatabase, user_id: int, task_id: str):
    await db.completions.update_one(
        {"user_id": user_id, "task_id": task_id},
        {"$set": {"status": "approved", "completed_at": datetime.utcnow()}}
    )


async def reject_completion(db: AsyncIOMotorDatabase, user_id: int, task_id: str):
    """Reject and allow resubmission by deleting the record."""
    await db.completions.delete_one({"user_id": user_id, "task_id": task_id})


async def get_user_completions(db: AsyncIOMotorDatabase, user_id: int):
    return await db.completions.find({"user_id": user_id, "status": "approved"}).to_list(length=None)


async def get_pending_completions(db: AsyncIOMotorDatabase):
    return await db.completions.find({"status": "pending"}).to_list(length=None)


async def count_user_rejections(db: AsyncIOMotorDatabase, user_id: int) -> int:
    """Count completions for this user that have been rejected (deleted and resubmitted track not here)
       Instead we track a rejection_count in user doc — see update_user_rejection_count."""
    user = await db.users.find_one({"telegram_id": user_id})
    return user.get("rejection_count", 0) if user else 0


async def increment_user_rejection_count(db: AsyncIOMotorDatabase, user_id: int):
    await db.users.update_one({"telegram_id": user_id}, {"$inc": {"rejection_count": 1}})


# ─────────────────────────────────────────
# WITHDRAWAL HELPERS
# ─────────────────────────────────────────

async def create_withdrawal(db: AsyncIOMotorDatabase, user_id: int, amount: float,
                             bank_account: str, bank_name: str, bank_code: str,
                             method: str = "bank", phone_number: str = None, network: str = None):
    withdrawal = {
        "user_id": user_id,
        "amount": amount,
        "method": method,           # "bank" or "airtime"
        "bank_account": bank_account,
        "bank_name": bank_name,
        "bank_code": bank_code,
        "phone_number": phone_number,   # for airtime
        "network": network,             # for airtime: MTN/Airtel/Glo/9mobile
        "status": "pending",
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
    return await db.withdrawals.find({"user_id": user_id}).sort("requested_at", -1).to_list(length=20)


# ─────────────────────────────────────────
# ADS HELPERS
# ─────────────────────────────────────────

async def create_ad(db: AsyncIOMotorDatabase, ad_data: dict):
    ad = {
        "creator_id": ad_data["creator_id"],
        "tier": ad_data["tier"],                    # basic | standard | premium
        "copy": ad_data["copy"],
        "image_file_id": ad_data.get("image_file_id"),
        "price": float(ad_data["price"]),
        "duration_hours": int(ad_data["duration_hours"]),
        "status": ad_data.get("status", "pending_payment"),  # pending_payment | pending_review | active | expired | rejected
        "payment_ref": ad_data.get("payment_ref"),
        "published_at": None,
        "expires_at": None,
        "created_at": datetime.utcnow()
    }
    result = await db.ads.insert_one(ad)
    ad["_id"] = result.inserted_id
    return ad


async def get_ad_by_id(db: AsyncIOMotorDatabase, ad_id: str):
    return await db.ads.find_one({"_id": ObjectId(ad_id)})


async def get_ads_by_user(db: AsyncIOMotorDatabase, creator_id: int):
    return await db.ads.find({"creator_id": creator_id}).sort("created_at", -1).to_list(length=None)


async def get_active_ads(db: AsyncIOMotorDatabase):
    return await db.ads.find({"status": "active"}).to_list(length=None)


async def get_pending_review_ads(db: AsyncIOMotorDatabase):
    return await db.ads.find({"status": "pending_review"}).to_list(length=None)


async def update_ad_status(db: AsyncIOMotorDatabase, ad_id: str, status: str, **kwargs):
    update = {"status": status}
    update.update(kwargs)
    await db.ads.update_one({"_id": ObjectId(ad_id)}, {"$set": update})


async def publish_ad(db: AsyncIOMotorDatabase, ad_id: str):
    ad = await get_ad_by_id(db, ad_id)
    if not ad:
        return None
    now = datetime.utcnow()
    from datetime import timedelta
    expires = now + timedelta(hours=ad["duration_hours"])
    await db.ads.update_one(
        {"_id": ObjectId(ad_id)},
        {"$set": {"status": "active", "published_at": now, "expires_at": expires}}
    )
    return expires


async def expire_overdue_ads(db: AsyncIOMotorDatabase):
    """Mark all active ads whose expires_at is in the past as expired."""
    now = datetime.utcnow()
    result = await db.ads.update_many(
        {"status": "active", "expires_at": {"$lt": now}},
        {"$set": {"status": "expired"}}
    )
    return result.modified_count


# ─────────────────────────────────────────
# TRANSACTION / HISTORY HELPERS
# ─────────────────────────────────────────

async def log_transaction(db: AsyncIOMotorDatabase, user_id: int, tx_type: str, amount: float,
                           description: str, status: str = "confirmed", ref_id: str = None):
    """
    tx_type: task_reward | referral_bonus | withdrawal_bank | withdrawal_airtime | admin_credit | admin_deduct
    status: confirmed | pending
    """
    tx = {
        "user_id": user_id,
        "type": tx_type,
        "amount": amount,
        "description": description,
        "status": status,
        "ref_id": ref_id,
        "created_at": datetime.utcnow()
    }
    await db.transactions.insert_one(tx)
    return tx


async def get_user_transactions(db: AsyncIOMotorDatabase, user_id: int, limit: int = 20):
    return await db.transactions.find({"user_id": user_id}).sort("created_at", -1).limit(limit).to_list(length=None)


# ─────────────────────────────────────────
# REFERRAL CONTEST HELPERS
# ─────────────────────────────────────────

async def get_referral_contest(db: AsyncIOMotorDatabase):
    return await db.contests.find_one({"_id": "referral_contest"})


async def set_referral_contest_prize(db: AsyncIOMotorDatabase, prize_text: str):
    await db.contests.update_one(
        {"_id": "referral_contest"},
        {"$set": {"prize": prize_text, "updated_at": datetime.utcnow()}},
        upsert=True
    )


async def get_contest_winner(db: AsyncIOMotorDatabase):
    """Return the user with the highest referral count."""
    top = await db.users.find(
        {"referral_count": {"$gt": 0}},
        {"telegram_id": 1, "username": 1, "referral_count": 1}
    ).sort("referral_count", -1).limit(1).to_list(length=None)
    return top[0] if top else None


# ─────────────────────────────────────────
# DAILY CHECK-IN HELPERS
# ─────────────────────────────────────────

async def get_last_checkin(db: AsyncIOMotorDatabase, user_id: int):
    return await db.checkins.find_one({"user_id": user_id})


async def record_checkin(db: AsyncIOMotorDatabase, user_id: int, reward: float):
    now = datetime.utcnow()
    await db.checkins.update_one(
        {"user_id": user_id},
        {"$set": {"last_checkin": now, "last_reward": reward}, "$inc": {"streak": 1, "total_earned": reward}},
        upsert=True
    )


async def reset_checkin_streak(db: AsyncIOMotorDatabase, user_id: int):
    await db.checkins.update_one({"user_id": user_id}, {"$set": {"streak": 0}})


# ─────────────────────────────────────────
# SETTINGS HELPERS
# ─────────────────────────────────────────

async def get_settings(db: AsyncIOMotorDatabase):
    s = await db.settings.find_one({"_id": "global"})
    if not s:
        s = {
            "_id": "global",
            "min_withdraw_bank": 1500,
            "min_withdraw_airtime": 700,
            "min_withdraw": 1500,          # kept for compatibility
            "referral_reward": 100,
            "total_reward_pool": 500000,
            "bot_name": "DoGetPaid Bot",
            "task_commission_pct": 10,
            "ad_tiers": {
                "basic":    {"price": 1500, "duration_hours": 24,  "description": "24-hour basic slot"},
                "standard": {"price": 3000, "duration_hours": 72,  "description": "3-day standard slot"},
                "premium":  {"price": 5000, "duration_hours": 168, "description": "7-day premium (manual review)"},
            },
            "checkin_min_reward": 20,
            "checkin_max_reward": 100,
            "referral_contest_prize": "₦5,000 airtime",
        }
        await db.settings.insert_one(s)
    return s


async def update_setting(db: AsyncIOMotorDatabase, key: str, value):
    await db.settings.update_one(
        {"_id": "global"},
        {"$set": {key: value}},
        upsert=True
    )


async def update_nested_setting(db: AsyncIOMotorDatabase, path: str, value):
    """Update a dot-path nested setting e.g. 'ad_tiers.basic.price'."""
    await db.settings.update_one(
        {"_id": "global"},
        {"$set": {path: value}},
        upsert=True
    )


# ─────────────────────────────────────────
# DELETE USER
# ─────────────────────────────────────────

async def delete_user(db: AsyncIOMotorDatabase, telegram_id: int):
    await db.users.delete_one({"telegram_id": telegram_id})
    await db.completions.delete_many({"user_id": telegram_id})
    await db.withdrawals.delete_many({"user_id": telegram_id})
    await db.transactions.delete_many({"user_id": telegram_id})
    await db.checkins.delete_one({"user_id": telegram_id})
    await db.ads.delete_many({"creator_id": telegram_id})
