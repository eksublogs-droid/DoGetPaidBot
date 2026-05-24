import logging
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from models.db import (
    get_user, get_settings, set_user_bank,
    create_withdrawal, get_pending_withdrawals,
    update_withdrawal_status, update_user_balance,
    get_user_withdrawals
)
from utils.keyboards import back_to_dashboard, confirm_withdraw_keyboard, admin_withdrawal_keyboard
from utils.flutterwave import verify_bank_account, send_payout, NIGERIAN_BANKS
from config.settings import settings

logger = logging.getLogger(__name__)
router = Router()


class BankStates(StatesGroup):
    waiting_account_number = State()
    confirming_bank = State()


class WithdrawStates(StatesGroup):
    waiting_amount = State()


def bank_selection_keyboard():
    """Generate inline keyboard with all banks."""
    buttons = []
    row = []
    for code, name in NIGERIAN_BANKS.items():
        row.append(InlineKeyboardButton(
            text=name,
            callback_data=f"select_bank:{code}"
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ─────────────────────────────────────────
# SET BANK ACCOUNT
# ─────────────────────────────────────────

@router.callback_query(F.data == "set_bank")
async def set_bank_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "💳 *Set Your Bank Account*\n\nSelect your bank:",
        reply_markup=bank_selection_keyboard(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("select_bank:"))
async def select_bank(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bank_code = callback.data.split(":")[1]
    bank_name = NIGERIAN_BANKS.get(bank_code)

    if not bank_name:
        await callback.message.answer("❌ Invalid bank. Try again.")
        return

    await state.update_data(bank_code=bank_code, bank_name=bank_name)
    await callback.message.answer(
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

    # Re-check balance at processing time — auto-reject if insufficient
    current_balance = user.get("balance", 0) if user else 0
    if current_balance < withdrawal["amount"]:
        await update_withdrawal_status(db, withdrawal_id, "rejected", reason="Insufficient balance at time of processing")
        await callback.message.edit_text(
            f"❌ *Withdrawal cancelled.*\n\n"
            f"Your current balance (₦{current_balance:,.0f}) is less than the requested amount "
            f"(₦{withdrawal['amount']:,.0f}).\n\n"
            f"Please check your balance and try again.",
            parse_mode="Markdown"
        )
        return

    await update_user_balance(db, user_id, -withdrawal["amount"])

    await callback.message.edit_text(
        f"✅ *Withdrawal Request Submitted!*\n\n"
        f"Amount: ₦{withdrawal['amount']:,.0f}\n"
        f"Bank: {withdrawal['bank_name']}\n"
        f"Account: {withdrawal['bank_account']}\n\n"
        f"⏳ Your withdrawal is being processed. We'll notify you once it's approved.",
        parse_mode="Markdown"
    )

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
# ADMIN — APPROVE/REJECT WITHDRAWAL
# ─────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_approve:"))
async def admin_approve_withdrawal(callback: CallbackQuery, db, bot: Bot):
    if callback.from_user.id not in settings.ADMIN_IDS:
        await callback.answer("❌ Unauthorized", show_alert=True)
        return

    await callback.answer("Marked as paid!")
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

    await update_withdrawal_status(db, withdrawal_id, "paid")
    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ *MARKED AS PAID* by @{callback.from_user.username}",
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
