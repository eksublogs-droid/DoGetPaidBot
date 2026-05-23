import logging
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from models.db import (
    get_user, get_settings, set_user_bank,
    create_withdrawal, get_pending_withdrawals,
    update_withdrawal_status, update_user_balance,
    get_user_withdrawals
)
from utils.keyboards import back_to_dashboard, confirm_withdraw_keyboard, admin_withdrawal_keyboard
from utils.flutterwave import verify_bank_account, send_payout, NIGERIAN_BANKS, banks_list_text
from config.settings import settings

logger = logging.getLogger(__name__)
router = Router()


class BankStates(StatesGroup):
    waiting_bank_code = State()
    waiting_account_number = State()
    confirming_bank = State()


class WithdrawStates(StatesGroup):
    waiting_amount = State()


# ─────────────────────────────────────────
# SET BANK ACCOUNT
# ─────────────────────────────────────────

@router.callback_query(F.data == "set_bank")
async def set_bank_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    banks_text = banks_list_text()
    await callback.message.answer(
        f"💳 *Set Your Bank Account*\n\n"
        f"Send your *bank code* first.\n\n"
        f"{banks_text}\n\n"
        f"Example: Send `033` for UBA",
        parse_mode="Markdown"
    )
    await state.set_state(BankStates.waiting_bank_code)


@router.message(BankStates.waiting_bank_code)
async def receive_bank_code(message: Message, state: FSMContext):
    bank_code = message.text.strip()
    if bank_code not in NIGERIAN_BANKS:
        await message.answer(
            f"❌ Invalid bank code: `{bank_code}`\n\nPlease send a valid code from the list.",
            parse_mode="Markdown"
        )
        return

    bank_name = NIGERIAN_BANKS[bank_code]
    await state.update_data(bank_code=bank_code, bank_name=bank_name)
    await message.answer(
        f"✅ Bank selected: *{bank_name}*\n\nNow send your *10-digit account number*:",
        parse_mode="Markdown"
    )
    await state.set_state(BankStates.waiting_account_number)


@router.message(BankStates.waiting_account_number)
async def receive_account_number(message: Message, state: FSMContext, db):
    account = message.text.strip()
    if not account.isdigit() or len(account) != 10:
        await message.answer("❌ Account number must be exactly 10 digits. Try again:")
        return

    data = await state.get_data()
    bank_code = data["bank_code"]
    bank_name = data["bank_name"]

    await message.answer("🔍 Verifying account...")
    result = await verify_bank_account(account, bank_code)

    if not result["success"]:
        await message.answer(
            f"❌ Could not verify account: {result['message']}\n\nCheck the details and try again.",
            reply_markup=back_to_dashboard()
        )
        await state.clear()
        return

    account_name = result["account_name"]
    await state.update_data(account_number=account, account_name=account_name)

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Yes, Save", callback_data="confirm_bank"),
            InlineKeyboardButton(text="❌ No, Cancel", callback_data="cancel_bank"),
        ]
    ])

    await message.answer(
        f"🏦 *Confirm Bank Details*\n\n"
        f"Bank: *{bank_name}*\n"
        f"Account: *{account}*\n"
        f"Name: *{account_name}*\n\n"
        f"Is this correct?",
        reply_markup=confirm_kb,
        parse_mode="Markdown"
    )
    await state.set_state(BankStates.confirming_bank)


@router.callback_query(F.data == "confirm_bank")
async def confirm_bank(callback: CallbackQuery, state: FSMContext, db):
    await callback.answer()
    data = await state.get_data()
    user_id = callback.from_user.id

    await set_user_bank(
        db, user_id,
        data["account_number"],
        data["bank_name"],
        data["bank_code"]
    )
    await state.clear()

    await callback.message.edit_text(
        f"✅ *Bank account saved!*\n\n"
        f"Bank: {data['bank_name']}\n"
        f"Account: {data['account_number']}\n"
        f"Name: {data['account_name']}",
        parse_mode="Markdown"
    )
    await callback.message.answer("Your account is now set for withdrawals.", reply_markup=back_to_dashboard())


@router.callback_query(F.data == "cancel_bank")
async def cancel_bank(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text("❌ Bank setup cancelled.")
    await callback.message.answer("Back to dashboard.", reply_markup=back_to_dashboard())


# ─────────────────────────────────────────
# WITHDRAW
# ─────────────────────────────────────────

@router.callback_query(F.data == "withdraw")
async def withdraw_start(callback: CallbackQuery, state: FSMContext, db):
    await callback.answer()
    user_id = callback.from_user.id
    user = await get_user(db, user_id)
    bot_settings = await get_settings(db)
    min_withdraw = bot_settings.get("min_withdraw", 500)

    if not user.get("bank_account"):
        await callback.message.answer(
            "❌ You haven't set a bank account yet!\n\nTap *Set Bank Account* first.",
            reply_markup=back_to_dashboard(),
            parse_mode="Markdown"
        )
        return

    balance = user.get("balance", 0)
    if balance < min_withdraw:
        await callback.message.answer(
            f"❌ Insufficient balance!\n\n"
            f"Your balance: ₦{balance:,.0f}\n"
            f"Minimum withdrawal: ₦{min_withdraw:,.0f}",
            reply_markup=back_to_dashboard(),
            parse_mode="Markdown"
        )
        return

    await callback.message.answer(
        f"💸 *Withdraw Funds*\n\n"
        f"Your balance: ₦{balance:,.0f}\n"
        f"Minimum: ₦{min_withdraw:,.0f}\n\n"
        f"How much do you want to withdraw?\n"
        f"Send the amount (numbers only):",
        parse_mode="Markdown"
    )
    await state.set_state(WithdrawStates.waiting_amount)


@router.message(WithdrawStates.waiting_amount)
async def receive_withdraw_amount(message: Message, state: FSMContext, db):
    user_id = message.from_user.id

    try:
        amount = float(message.text.strip().replace(",", ""))
    except ValueError:
        await message.answer("❌ Invalid amount. Enter numbers only (e.g. 1000):")
        return

    user = await get_user(db, user_id)
    bot_settings = await get_settings(db)
    min_withdraw = bot_settings.get("min_withdraw", 500)
    balance = user.get("balance", 0)

    if amount < min_withdraw:
        await message.answer(f"❌ Minimum withdrawal is ₦{min_withdraw:,.0f}. Try again:")
        return

    if amount > balance:
        await message.answer(f"❌ Insufficient balance. Your balance is ₦{balance:,.0f}. Try again:")
        return

    # Create withdrawal record
    withdrawal = await create_withdrawal(
        db, user_id, amount,
        user["bank_account"], user["bank_name"], user["bank_code"]
    )
    withdrawal_id = str(withdrawal["_id"])

    await state.clear()

    await message.answer(
        f"💸 *Confirm Withdrawal*\n\n"
        f"Amount: *₦{amount:,.0f}*\n"
        f"Bank: *{user['bank_name']}*\n"
        f"Account: *{user['bank_account']}*\n\n"
        f"Confirm?",
        reply_markup=confirm_withdraw_keyboard(amount, withdrawal_id),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("confirm_withdraw:"))
async def confirm_withdrawal(callback: CallbackQuery, db, bot: Bot):
    await callback.answer()
    withdrawal_id = callback.data.split(":")[1]
    user_id = callback.from_user.id

    user = await get_user(db, user_id)
    withdrawal = await db.withdrawals.find_one({"_id": __import__("bson").ObjectId(withdrawal_id)})

    if not withdrawal or withdrawal["status"] != "pending":
        await callback.message.edit_text("❌ This withdrawal request is no longer valid.")
        return

    # Deduct balance immediately
    await update_user_balance(db, user_id, -withdrawal["amount"])

    await callback.message.edit_text(
        f"✅ *Withdrawal Request Submitted!*\n\n"
        f"Amount: ₦{withdrawal['amount']:,.0f}\n"
        f"Bank: {withdrawal['bank_name']}\n"
        f"Account: {withdrawal['bank_account']}\n\n"
        f"⏳ Your payment will be processed shortly.",
        parse_mode="Markdown"
    )

    # Notify admins
    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"💸 *New Withdrawal Request*\n\n"
                f"👤 User: @{user.get('username', user_id)} (`{user_id}`)\n"
                f"💰 Amount: ₦{withdrawal['amount']:,.0f}\n"
                f"🏦 Bank: {withdrawal['bank_name']}\n"
                f"📋 Account: {withdrawal['bank_account']}",
                reply_markup=admin_withdrawal_keyboard(withdrawal_id, user_id),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")


@router.callback_query(F.data == "cancel_withdraw")
async def cancel_withdrawal(callback: CallbackQuery, db):
    await callback.answer()
    withdrawal_id = callback.data.split(":")[-1] if ":" in callback.data else None
    if withdrawal_id:
        await db.withdrawals.delete_one({"_id": __import__("bson").ObjectId(withdrawal_id)})
    await callback.message.edit_text("❌ Withdrawal cancelled.")
    await callback.message.answer("Back to dashboard.", reply_markup=back_to_dashboard())


# ─────────────────────────────────────────
# WITHDRAWAL HISTORY
# ─────────────────────────────────────────

@router.callback_query(F.data == "withdraw_history")
async def withdraw_history(callback: CallbackQuery, db):
    await callback.answer()
    user_id = callback.from_user.id
    withdrawals = await get_user_withdrawals(db, user_id)

    if not withdrawals:
        await callback.message.answer(
            "📜 No withdrawal history yet.",
            reply_markup=back_to_dashboard()
        )
        return

    lines = ["📜 *Withdrawal History*\n"]
    status_emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌", "paid": "💰"}
    for w in withdrawals:
        emoji = status_emoji.get(w["status"], "•")
        date = w["requested_at"].strftime("%b %d, %Y")
        lines.append(f"{emoji} ₦{w['amount']:,.0f} — {w['status'].upper()} ({date})")

    await callback.message.answer(
        "\n".join(lines),
        reply_markup=back_to_dashboard(),
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# ADMIN — APPROVE WITHDRAWAL (Flutterwave)
# ─────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_approve:"))
async def admin_approve_withdrawal(callback: CallbackQuery, db, bot: Bot):
    if callback.from_user.id not in settings.ADMIN_IDS:
        await callback.answer("❌ Unauthorized", show_alert=True)
        return

    await callback.answer("Processing...")
    parts = callback.data.split(":")
    withdrawal_id = parts[1]
    user_id = int(parts[2])

    withdrawal = await db.withdrawals.find_one({"_id": __import__("bson").ObjectId(withdrawal_id)})
    if not withdrawal:
        await callback.message.edit_text("❌ Withdrawal not found.")
        return

    if withdrawal["status"] != "pending":
        await callback.message.edit_text(f"Already {withdrawal['status']}.")
        return

    # Trigger Flutterwave payout
    result = await send_payout(
        account_number=withdrawal["bank_account"],
        bank_code=withdrawal["bank_code"],
        bank_name=withdrawal["bank_name"],
        amount=withdrawal["amount"],
        user_id=user_id
    )

    if result["success"]:
        await update_withdrawal_status(db, withdrawal_id, "paid")
        await callback.message.edit_text(
            callback.message.text + f"\n\n✅ *PAID* via Flutterwave\nRef: `{result['reference']}`",
            parse_mode="Markdown"
        )
        try:
            await bot.send_message(
                user_id,
                f"🎉 *Payment Sent!*\n\n"
                f"₦{withdrawal['amount']:,.0f} has been transferred to your {withdrawal['bank_name']} account.\n"
                f"It should arrive within minutes.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Could not notify user {user_id}: {e}")
    else:
        await callback.message.edit_text(
            callback.message.text + f"\n\n❌ *Payout failed:* {result['message']}",
            parse_mode="Markdown"
        )


@router.callback_query(F.data.startswith("admin_reject:"))
async def admin_reject_withdrawal(callback: CallbackQuery, db, bot: Bot):
    if callback.from_user.id not in settings.ADMIN_IDS:
        await callback.answer("❌ Unauthorized", show_alert=True)
        return

    await callback.answer("Rejected")
    parts = callback.data.split(":")
    withdrawal_id = parts[1]
    user_id = int(parts[2])

    withdrawal = await db.withdrawals.find_one({"_id": __import__("bson").ObjectId(withdrawal_id)})
    if not withdrawal:
        return

    # Refund the balance
    await update_user_balance(db, user_id, withdrawal["amount"])
    await update_withdrawal_status(db, withdrawal_id, "rejected")

    await callback.message.edit_text(
        callback.message.text + f"\n\n❌ *REJECTED* by @{callback.from_user.username}",
        parse_mode="Markdown"
    )

    try:
        await bot.send_message(
            user_id,
            f"❌ *Withdrawal Rejected*\n\n"
            f"Your withdrawal of ₦{withdrawal['amount']:,.0f} was rejected.\n"
            f"Your balance has been refunded.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning(f"Could not notify user {user_id}: {e}")
