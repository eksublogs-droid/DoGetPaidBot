"""
Ads system — /runad flow
Tiers: Basic ₦1,500 / Standard ₦3,000 / Premium ₦5,000
Premium ads go to admin queue for manual review before publishing.
Basic/Standard auto-publish after payment confirmation.
"""
import logging
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from models.db import (
    get_user, get_settings, create_ad, get_ad_by_id,
    get_ads_by_user, update_ad_status, publish_ad,
    get_pending_review_ads
)
from utils.keyboards import (
    ad_tier_keyboard, ad_confirm_keyboard,
    admin_ad_review_keyboard, back_to_dashboard
)
from config.settings import settings

logger = logging.getLogger(__name__)
router = Router()


class AdStates(StatesGroup):
    choosing_tier = State()
    typing_copy = State()
    uploading_image = State()
    confirming = State()


def _skip_image_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Skip Image", callback_data="ad_skip_image")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="dashboard")],
    ])


# ─────────────────────────────────────────
# /runad — Entry point
# ─────────────────────────────────────────

@router.callback_query(F.data == "run_ad")
@router.message(Command("runad"))
async def run_ad_start(event, db, state: FSMContext):
    user_id = event.from_user.id
    user = await get_user(db, user_id)
    if not user or not user.get("onboarded"):
        answer = event.message.answer if isinstance(event, CallbackQuery) else event.answer
        await answer("❌ Complete onboarding first.")
        if isinstance(event, CallbackQuery):
            await event.answer()
        return

    if isinstance(event, CallbackQuery):
        await event.answer()

    bot_settings = await get_settings(db)
    tiers = bot_settings.get("ad_tiers", {
        "basic":    {"price": 1500, "duration_hours": 24,  "description": "24-hour basic slot"},
        "standard": {"price": 3000, "duration_hours": 72,  "description": "3-day standard slot"},
        "premium":  {"price": 5000, "duration_hours": 168, "description": "7-day premium (manual review)"},
    })

    answer_fn = event.message.answer if isinstance(event, CallbackQuery) else event.answer
    await answer_fn(
        "📢 *Run an Ad*\n\n"
        "Reach all users on this platform by placing an ad.\n\n"
        "Choose a tier:",
        reply_markup=ad_tier_keyboard(tiers),
        parse_mode="Markdown"
    )
    await state.set_state(AdStates.choosing_tier)


@router.callback_query(F.data.startswith("ad_tier:"), AdStates.choosing_tier)
async def ad_choose_tier(callback: CallbackQuery, state: FSMContext, db):
    await callback.answer()
    tier_key = callback.data.split(":")[1]
    bot_settings = await get_settings(db)
    tiers = bot_settings.get("ad_tiers", {})
    tier_info = tiers.get(tier_key)
    if not tier_info:
        await callback.message.answer("❌ Invalid tier. Try again.")
        return

    await state.update_data(tier=tier_key, tier_info=tier_info)
    await callback.message.answer(
        f"✅ Tier selected: *{tier_key.capitalize()}* — ₦{tier_info['price']:,}\n\n"
        f"Now type your *ad copy* (text of your ad, max 500 characters):",
        parse_mode="Markdown"
    )
    await state.set_state(AdStates.typing_copy)


@router.message(AdStates.typing_copy, F.text)
async def ad_receive_copy(message: Message, state: FSMContext):
    copy = message.text.strip()
    if len(copy) > 500:
        await message.answer(f"❌ Ad copy too long ({len(copy)} chars). Max 500 characters. Try again:")
        return

    await state.update_data(copy=copy)
    await message.answer(
        "📸 *Upload an image for your ad* (optional).\n\n"
        "Send a photo, or tap *Skip Image* to proceed without one:",
        reply_markup=_skip_image_keyboard(),
        parse_mode="Markdown"
    )
    await state.set_state(AdStates.uploading_image)


@router.message(AdStates.uploading_image, F.photo)
async def ad_receive_image(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(image_file_id=file_id)
    await _show_ad_preview(message, state, message.from_user.id)


@router.callback_query(F.data == "ad_skip_image", AdStates.uploading_image)
async def ad_skip_image(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(image_file_id=None)
    await _show_ad_preview(callback.message, state, callback.from_user.id)


async def _show_ad_preview(msg: Message, state: FSMContext, user_id: int):
    data = await state.get_data()
    tier = data["tier"]
    tier_info = data["tier_info"]
    copy = data["copy"]
    image_file_id = data.get("image_file_id")

    preview_text = (
        f"👁 *Ad Preview*\n\n"
        f"Tier: *{tier.capitalize()}*\n"
        f"Price: ₦{tier_info['price']:,}\n"
        f"Duration: {tier_info['description']}\n\n"
        f"*Ad Text:*\n{copy}\n\n"
        f"{'📸 Image attached' if image_file_id else '🚫 No image'}\n\n"
        f"{'⚠️ Premium ads require admin review before going live.' if tier == 'premium' else '✅ Will auto-publish after payment.'}\n\n"
        f"Pay ₦{tier_info['price']:,} to submit this ad?"
    )

    # Ad record created at payment confirmation step — store preview in state for now
    await state.update_data(preview_text=preview_text)
    await state.set_state(AdStates.confirming)

    if image_file_id:
        await msg.answer_photo(
            photo=image_file_id,
            caption=preview_text,
            reply_markup=_pre_payment_keyboard(),
            parse_mode="Markdown"
        )
    else:
        await msg.answer(
            preview_text,
            reply_markup=_pre_payment_keyboard(),
            parse_mode="Markdown"
        )


def _pre_payment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Pay & Submit", callback_data="ad_confirm_pay")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="dashboard")],
    ])


@router.callback_query(F.data == "ad_confirm_pay", AdStates.confirming)
async def ad_confirm_pay(callback: CallbackQuery, state: FSMContext, db, bot: Bot):
    await callback.answer()
    user_id = callback.from_user.id
    data = await state.get_data()
    tier = data["tier"]
    tier_info = data["tier_info"]
    copy = data["copy"]
    image_file_id = data.get("image_file_id")
    await state.clear()

    # Create ad record — status pending_payment (manual payment confirmation for now)
    # In production wire Paystack here. For now: admin manually marks as paid.
    ad = await create_ad(db, {
        "creator_id": user_id,
        "tier": tier,
        "copy": copy,
        "image_file_id": image_file_id,
        "price": tier_info["price"],
        "duration_hours": tier_info["duration_hours"],
        "status": "pending_payment",
    })
    ad_id = str(ad["_id"])

    await callback.message.answer(
        f"✅ *Ad Submission Received!*\n\n"
        f"Ad ID: `{ad_id}`\n"
        f"Amount to pay: *₦{tier_info['price']:,}*\n\n"
        f"💳 Please make payment to the admin and share your payment proof.\n"
        f"Once payment is confirmed, your ad will go live.\n\n"
        f"{'⚠️ *Premium ads* require manual admin review.' if tier == 'premium' else ''}",
        reply_markup=back_to_dashboard(),
        parse_mode="Markdown"
    )

    # Notify admins
    for admin_id in settings.ADMIN_IDS:
        try:
            notif_text = (
                f"📢 *New Ad Submission*\n\n"
                f"👤 User: @{callback.from_user.username or user_id} (`{user_id}`)\n"
                f"📦 Tier: *{tier.capitalize()}*\n"
                f"💰 Price: ₦{tier_info['price']:,}\n"
                f"🕐 Duration: {tier_info['description']}\n"
                f"📝 Copy:\n{copy}\n\n"
                f"Ad ID: `{ad_id}`"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Confirm Payment & Publish", callback_data=f"admin_ad_paid:{ad_id}")],
                [InlineKeyboardButton(text="❌ Reject Ad", callback_data=f"admin_ad_reject:{ad_id}:{user_id}")],
            ])
            if image_file_id:
                await bot.send_photo(admin_id, photo=image_file_id,
                                     caption=notif_text, reply_markup=kb, parse_mode="Markdown")
            else:
                await bot.send_message(admin_id, notif_text, reply_markup=kb, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")


# ─────────────────────────────────────────
# ADMIN — CONFIRM PAYMENT & PUBLISH
# ─────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_ad_paid:"))
async def admin_ad_confirm_payment(callback: CallbackQuery, db, bot: Bot):
    if callback.from_user.id not in settings.ADMIN_IDS:
        await callback.answer("❌ Unauthorized", show_alert=True)
        return
    await callback.answer("Processing...")

    ad_id = callback.data.split(":")[1]
    ad = await get_ad_by_id(db, ad_id)
    if not ad:
        await callback.message.reply("❌ Ad not found.")
        return

    if ad["tier"] == "premium":
        # Move to pending_review, don't auto-publish
        await update_ad_status(db, ad_id, "pending_review")
        await callback.message.edit_text(
            callback.message.text + f"\n\n⭐ *Moved to Premium Review Queue.*",
            parse_mode="Markdown"
        )
        # Notify creator
        try:
            await bot.send_message(
                ad["creator_id"],
                f"⭐ *Payment confirmed!*\n\n"
                f"Your Premium ad is now under review. We'll publish it once approved.",
                parse_mode="Markdown"
            )
        except Exception:
            pass
        # Alert all admins to review
        for admin_id in settings.ADMIN_IDS:
            try:
                review_text = (
                    f"⭐ *Premium Ad Ready for Review*\n\n"
                    f"Ad ID: `{ad_id}`\n"
                    f"📝 Copy:\n{ad['copy']}"
                )
                if ad.get("image_file_id"):
                    await bot.send_photo(admin_id, photo=ad["image_file_id"],
                                         caption=review_text,
                                         reply_markup=admin_ad_review_keyboard(ad_id),
                                         parse_mode="Markdown")
                else:
                    await bot.send_message(admin_id, review_text,
                                           reply_markup=admin_ad_review_keyboard(ad_id),
                                           parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Notify admin {admin_id}: {e}")
    else:
        # Auto-publish
        expires_at = await publish_ad(db, ad_id)
        await callback.message.edit_text(
            callback.message.text + f"\n\n✅ *Published! Expires: {expires_at.strftime('%b %d, %Y %H:%M UTC')}*",
            parse_mode="Markdown"
        )
        await _broadcast_ad(bot, db, ad)
        try:
            await bot.send_message(
                ad["creator_id"],
                f"🎉 *Your ad is now live!*\n\n"
                f"Tier: {ad['tier'].capitalize()}\n"
                f"Expires: {expires_at.strftime('%b %d, %Y %H:%M UTC')}",
                parse_mode="Markdown"
            )
        except Exception:
            pass


# ─────────────────────────────────────────
# ADMIN — REVIEW PREMIUM AD
# ─────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_ad_approve:"))
async def admin_ad_approve(callback: CallbackQuery, db, bot: Bot):
    if callback.from_user.id not in settings.ADMIN_IDS:
        await callback.answer("❌ Unauthorized", show_alert=True)
        return
    await callback.answer("Approving...")
    ad_id = callback.data.split(":")[1]
    ad = await get_ad_by_id(db, ad_id)
    if not ad:
        await callback.message.reply("❌ Ad not found.")
        return

    expires_at = await publish_ad(db, ad_id)
    try:
        await callback.message.edit_caption(
            callback.message.caption + f"\n\n✅ *APPROVED & PUBLISHED by @{callback.from_user.username}*\n"
                                        f"Expires: {expires_at.strftime('%b %d, %Y %H:%M UTC')}",
            parse_mode="Markdown"
        )
    except Exception:
        await callback.message.edit_text(
            (callback.message.text or "") + f"\n\n✅ *APPROVED & PUBLISHED*",
            parse_mode="Markdown"
        )

    await _broadcast_ad(bot, db, ad)
    try:
        await bot.send_message(
            ad["creator_id"],
            f"🎉 *Your Premium ad is now live!*\n\n"
            f"Expires: {expires_at.strftime('%b %d, %Y %H:%M UTC')}",
            parse_mode="Markdown"
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_ad_reject:"))
async def admin_ad_reject(callback: CallbackQuery, db, bot: Bot):
    if callback.from_user.id not in settings.ADMIN_IDS:
        await callback.answer("❌ Unauthorized", show_alert=True)
        return
    await callback.answer("Rejected")
    parts = callback.data.split(":")
    ad_id = parts[1]
    ad = await get_ad_by_id(db, ad_id)
    if not ad:
        return

    await update_ad_status(db, ad_id, "rejected")
    try:
        await callback.message.edit_text(
            (callback.message.text or callback.message.caption or "") + f"\n\n❌ *REJECTED by @{callback.from_user.username}*",
            parse_mode="Markdown"
        )
    except Exception:
        pass

    try:
        await bot.send_message(
            ad["creator_id"],
            f"❌ *Your ad was rejected.*\n\n"
            f"Please contact support for more information.",
            parse_mode="Markdown"
        )
    except Exception:
        pass


# ─────────────────────────────────────────
# BROADCAST AD TO ADS CHANNEL
# ─────────────────────────────────────────

async def _broadcast_ad(bot: Bot, db, ad: dict):
    """Post ad to the configured ADS_CHANNEL_ID."""
    channel = settings.ADS_CHANNEL_ID
    if not channel:
        return
    copy = ad.get("copy", "")
    image_file_id = ad.get("image_file_id")
    try:
        if image_file_id:
            await bot.send_photo(channel, photo=image_file_id, caption=f"📢 *Ad*\n\n{copy}", parse_mode="Markdown")
        else:
            await bot.send_message(channel, f"📢 *Ad*\n\n{copy}", parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Could not post ad to channel {channel}: {e}")


# ─────────────────────────────────────────
# USER — MY ADS STATUS (/myadsstatus)
# ─────────────────────────────────────────

@router.callback_query(F.data == "my_ads_status")
@router.message(Command("myadsstatus"))
async def my_ads_status(event, db):
    user_id = event.from_user.id
    answer_fn = event.message.answer if isinstance(event, CallbackQuery) else event.answer
    if isinstance(event, CallbackQuery):
        await event.answer()

    ads = await get_ads_by_user(db, user_id)
    if not ads:
        await answer_fn("📢 You haven't placed any ads yet.", reply_markup=back_to_dashboard())
        return

    lines = ["📢 *My Ads*\n"]
    status_emoji = {
        "pending_payment": "💳",
        "pending_review": "⏳",
        "active": "🟢",
        "expired": "🔴",
        "rejected": "❌"
    }
    now = datetime.utcnow()
    for ad in ads[:10]:
        emoji = status_emoji.get(ad["status"], "•")
        tier = ad.get("tier", "").capitalize()
        if ad["status"] == "active" and ad.get("expires_at"):
            remaining = ad["expires_at"] - now
            hours_left = max(0, int(remaining.total_seconds() // 3600))
            time_info = f" — {hours_left}h remaining"
        else:
            time_info = ""
        lines.append(f"{emoji} [{tier}] {ad.get('copy', '')[:40]}...{time_info}")

    await answer_fn("\n".join(lines), reply_markup=back_to_dashboard(), parse_mode="Markdown")
