import logging
from aiogram import Router, F, Bot
from aiogram.types import (
    CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from models.db import (
    get_user, get_settings, set_user_bank,
    create_withdrawal, get_pending_withdrawals,
    update_withdrawal_status, update_user_balance,
    get_user_withdrawals, log_transaction, increment_total_withdrawn
)
from utils.keyboards import (
    back_to_dashboard, confirm_withdraw_keyboard,
    admin_withdrawal_keyboard, withdraw_method_keyboard,
    airtime_network_keyboard
)
from utils.flutterwave import verify_bank_account, NIGERIAN_BANKS
from config.settings import settings

logger = logging.getLogger(__name__)
router = Router()


class BankStates(StatesGroup):
    waiting_account_number = State()
    confirming_bank = State()


class WithdrawStates(StatesGroup):
    choosing_method = State()
    waiting_amount_bank = State()
    waiting_amount_airtime = State()
    waiting_phone = State()
    waiting_network = State()
    confirming_airtime = State()


def bank_selection_keyboard():
    buttons = []
    row = []
    for code, name in NIGERIAN_BANKS.items():
        row.append(InlineKeyboardButton(text=name, callback_data=f"select_bank:{code}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _change_bank_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Change Bank Account", callback_data="set_bank_proceed")],
        [InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="dashboard")],
    ])


# ─────────────────────────────────────────
# SET BANK ACCOUNT
# ─────────────────────────────────────────

@router.callback_query(F.data == "set_bank")
async def set_bank_start(callback: CallbackQuery, state: FSMContext, db):
    await callback.answer()
    user = await get_user(db, callback.from_user.id)
    if user and user.get("bank_account"):
        await callback.message.answer(
            f"💳 *Your Current Bank Account*\n\n"
            f"Bank: *{user['bank_name']}*\n"
            f"Account: *{user['bank_account']}*\n\n"
            f"Do you want to change it?",
            reply_markup=_change_bank_keyboard(),
            parse_mode="Markdown"
        )
        return
    await callback.message.answer(
        "💳 *Set Your Bank Account*\n\nSelect your bank:",
        reply_markup=bank_selection_keyboard(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "set_bank_proceed")
async def set_bank_proceed(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "💳 *Change Bank Account*\n\nSelect your new bank:",
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
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Yes, Save", callback_data="confirm_bank"),
        InlineKeyboardButton(text="❌ No, Cancel", callback_data="cancel_bank"),
    ]])
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
    await set_user_bank(db, user_id, data["account_number"], data["bank_name"], data["bank_code"])
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
# WITHDRAW — METHOD SELECTION
# ─────────────────────────────────────────

@router.callback_query(F.data == "withdraw")
async def withdraw_start(callback: CallbackQuery, state: FSMContext, db):
    await callback.answer()
    user = await get_user(db, callback.from_user.id)
    balance = user.get("balance", 0) if user else 0
    await callback.message.answer(
        f"💸 *Withdraw Funds*\n\n"
        f"Your balance: *₦{balance:,.0f}*\n\n"
        f"Choose withdrawal method:",
        reply_markup=withdraw_method_keyboard(),
        parse_mode="Markdown"
    )


# ─── BANK WITHDRAWAL ───

@router.callback_query(F.data == "withdraw_method:bank")
async def withdraw_bank(callback: CallbackQuery, state: FSMContext, db):
    await callback.answer()
    user_id = callback.from_user.id
    user = await get_user(db, user_id)
    bot_settings = await get_settings(db)
    min_withdraw = bot_settings.get("min_withdraw_bank", 1500)

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
            f"Minimum bank withdrawal: ₦{min_withdraw:,.0f}",
            reply_markup=back_to_dashboard(),
            parse_mode="Markdown"
        )
        return

    await callback.message.answer(
        f"🏦 *Bank Withdrawal*\n\n"
        f"Balance: ₦{balance:,.0f}\n"
        f"Minimum: ₦{min_withdraw:,.0f}\n\n"
        f"Bank: *{user['bank_name']}*\n"
        f"Account: *{user['bank_account']}*\n\n"
        f"How much do you want to withdraw?\nSend amount (numbers only):",
        parse_mode="Markdown"
    )
    await state.set_state(WithdrawStates.waiting_amount_bank)


@router.message(WithdrawStates.waiting_amount_bank)
async def receive_bank_withdraw_amount(message: Message, state: FSMContext, db):
    user_id = message.from_user.id
    try:
        amount = float(message.text.strip().replace(",", "").replace("₦", ""))
    except ValueError:
        await message.answer("❌ Invalid amount. Enter numbers only (e.g. 2000):")
        return

    user = await get_user(db, user_id)
    bot_settings = await get_settings(db)
    min_withdraw = bot_settings.get("min_withdraw_bank", 1500)
    balance = user.get("balance", 0)

    if amount < min_withdraw:
        await message.answer(f"❌ Minimum bank withdrawal is ₦{min_withdraw:,.0f}. Try again:")
        return
    if amount > balance:
        await message.answer(f"❌ Insufficient balance. Your balance is ₦{balance:,.0f}. Try again:")
        return

    withdrawal = await create_withdrawal(
        db, user_id, amount,
        user["bank_account"], user["bank_name"], user["bank_code"],
        method="bank"
    )
    withdrawal_id = str(withdrawal["_id"])
    await state.clear()

    await message.answer(
        f"💸 *Confirm Bank Withdrawal*\n\n"
        f"Amount: *₦{amount:,.0f}*\n"
        f"Bank: *{user['bank_name']}*\n"
        f"Account: *{user['bank_account']}*\n\n"
        f"Confirm?",
        reply_markup=confirm_withdraw_keyboard(amount, withdrawal_id),
        parse_mode="Markdown"
    )


# ─── AIRTIME WITHDRAWAL ───

@router.callback_query(F.data == "withdraw_method:airtime")
async def withdraw_airtime(callback: CallbackQuery, state: FSMContext, db):
    await callback.answer()
    user_id = callback.from_user.id
    user = await get_user(db, user_id)
    bot_settings = await get_settings(db)
    min_airtime = bot_settings.get("min_withdraw_airtime", 700)
    balance = user.get("balance", 0) if user else 0

    if balance < min_airtime:
        await callback.message.answer(
            f"❌ Insufficient balance!\n\n"
            f"Your balance: ₦{balance:,.0f}\n"
            f"Minimum airtime withdrawal: ₦{min_airtime:,.0f}",
            reply_markup=back_to_dashboard(),
            parse_mode="Markdown"
        )
        return

    await callback.message.answer(
        f"📱 *Airtime Withdrawal*\n\n"
        f"Balance: ₦{balance:,.0f}\n"
        f"Minimum: ₦{min_airtime:,.0f}\n\n"
        f"How much airtime do you want?\nSend amount (numbers only):",
        parse_mode="Markdown"
    )
    await state.set_state(WithdrawStates.waiting_amount_airtime)


@router.message(WithdrawStates.waiting_amount_airtime)
async def receive_airtime_amount(message: Message, state: FSMContext, db):
    user_id = message.from_user.id
    try:
        amount = float(message.text.strip().replace(",", "").replace("₦", ""))
    except ValueError:
        await message.answer("❌ Invalid amount. Enter numbers only:")
        return

    bot_settings = await get_settings(db)
    min_airtime = bot_settings.get("min_withdraw_airtime", 700)
    user = await get_user(db, user_id)
    balance = user.get("balance", 0)

    if amount < min_airtime:
        await message.answer(f"❌ Minimum airtime withdrawal is ₦{min_airtime:,.0f}. Try again:")
        return
    if amount > balance:
        await message.answer(f"❌ Insufficient balance. Your balance is ₦{balance:,.0f}. Try again:")
        return

    await state.update_data(airtime_amount=amount)
    await message.answer(
        "📱 Enter the *phone number* to receive the airtime:\n(e.g. 08012345678)",
        parse_mode="Markdown"
    )
    await state.set_state(WithdrawStates.waiting_phone)


@router.message(WithdrawStates.waiting_phone)
async def receive_airtime_phone(message: Message, state: FSMContext):
    phone = message.text.strip().replace(" ", "").replace("-", "")
    if not phone.isdigit() or len(phone) not in (10, 11):
        await message.answer("❌ Invalid phone number. Must be 10 or 11 digits. Try again:")
        return
    await state.update_data(airtime_phone=phone)
    await message.answer(
        "📡 Select the network for this number:",
        reply_markup=airtime_network_keyboard()
    )
    await state.set_state(WithdrawStates.waiting_network)


@router.callback_query(F.data.startswith("airtime_network:"), WithdrawStates.waiting_network)
async def receive_airtime_network(callback: CallbackQuery, state: FSMContext, db):
    await callback.answer()
    network = callback.data.split(":")[1]
    data = await state.get_data()
    amount = data["airtime_amount"]
    phone = data["airtime_phone"]

    await state.update_data(airtime_network=network)

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Confirm", callback_data="confirm_airtime"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="dashboard"),
    ]])

    await callback.message.answer(
        f"📱 *Confirm Airtime Withdrawal*\n\n"
        f"Amount: *₦{amount:,.0f}*\n"
        f"Phone: *{phone}*\n"
        f"Network: *{network}*\n\n"
        f"Confirm?",
        reply_markup=confirm_kb,
        parse_mode="Markdown"
    )
    await state.set_state(WithdrawStates.confirming_airtime)


@router.callback_query(F.data == "confirm_airtime", WithdrawStates.confirming_airtime)
async def confirm_airtime_withdrawal(callback: CallbackQuery, state: FSMContext, db, bot: Bot):
    await callback.answer()
    user_id = callback.from_user.id
    data = await state.get_data()
    amount = data["airtime_amount"]
    phone = data["airtime_phone"]
    network = data["airtime_network"]
    await state.clear()

    user = await get_user(db, user_id)
    balance = user.get("balance", 0) if user else 0
    if balance < amount:
        await callback.message.answer(
            f"❌ Insufficient balance (₦{balance:,.0f}). Request cancelled.",
            reply_markup=back_to_dashboard()
        )
        return

    withdrawal = await create_withdrawal(
        db, user_id, amount,
        bank_account="", bank_name="", bank_code="",
        method="airtime", phone_number=phone, network=network
    )
    withdrawal_id = str(withdrawal["_id"])

    await update_user_balance(db, user_id, -amount)
    await log_transaction(db, user_id, "withdrawal_airtime", -amount,
                          f"Airtime withdrawal ₦{amount:,.0f} to {phone} ({network})",
                          status="pending", ref_id=withdrawal_id)

    await callback.message.edit_text(
        f"✅ *Airtime Request Submitted!*\n\n"
        f"Amount: ₦{amount:,.0f}\n"
        f"Phone: {phone}\n"
        f"Network: {network}\n\n"
        f"⏳ Admin will process this shortly.",
        parse_mode="Markdown"
    )

    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📱 *New Airtime Request*\n\n"
                f"👤 User: @{user.get('username', user_id)} (`{user_id}`)\n"
                f"💰 Amount: ₦{amount:,.0f}\n"
                f"📞 Phone: {phone}\n"
                f"📡 Network: {network}",
                reply_markup=admin_withdrawal_keyboard(withdrawal_id, user_id),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")


# ─────────────────────────────────────────
# CONFIRM BANK WITHDRAWAL
# ─────────────────────────────────────────

@router.callback_query(F.data.startswith("confirm_withdraw:"))
async def confirm_withdrawal(callback: CallbackQuery, db, bot: Bot):
    await callback.answer()
    withdrawal_id = callback.data.split(":")[1]
    user_id = callback.from_user.id

    user = await get_user(db, user_id)
    from bson import ObjectId
    withdrawal = await db.withdrawals.find_one({"_id": ObjectId(withdrawal_id)})

    if not withdrawal or withdrawal["status"] != "pending":
        await callback.message.edit_text("❌ This withdrawal request is no longer valid.")
        return

    current_balance = user.get("balance", 0) if user else 0
    if current_balance < withdrawal["amount"]:
        await update_withdrawal_status(db, withdrawal_id, "rejected",
                                       reason="Insufficient balance at time of processing")
        await callback.message.edit_text(
            f"❌ *Withdrawal cancelled.*\n\n"
            f"Your balance (₦{current_balance:,.0f}) is less than the requested amount "
            f"(₦{withdrawal['amount']:,.0f}).",
            parse_mode="Markdown"
        )
        return

    await update_user_balance(db, user_id, -withdrawal["amount"])
    await log_transaction(db, user_id, "withdrawal_bank", -withdrawal["amount"],
                          f"Bank withdrawal ₦{withdrawal['amount']:,.0f} to {withdrawal['bank_name']}",
                          status="pending", ref_id=withdrawal_id)

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
                f"💸 *New Bank Withdrawal Request*\n\n"
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
    await callback.message.edit_text("❌ Withdrawal cancelled.")
    await callback.message.answer("Back to dashboard.", reply_markup=back_to_dashboard())


# ─────────────────────────────────────────
# WITHDRAWAL HISTORY
# ─────────────────────────────────────────

@router.callback_query(F.data == "history_withdrawals")
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
        method = w.get("method", "bank")
        method_label = "Bank" if method == "bank" else f"Airtime ({w.get('network', '')})"
        lines.append(f"{emoji} ₦{w['amount']:,.0f} — {method_label} — {w['status'].upper()} ({date})")

    await callback.message.answer(
        "\n".join(lines),
        reply_markup=back_to_dashboard(),
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────
# ADMIN APPROVE / REJECT
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

    from bson import ObjectId
    withdrawal = await db.withdrawals.find_one({"_id": ObjectId(withdrawal_id)})
    if not withdrawal:
        await callback.message.edit_text("❌ Withdrawal not found.")
        return
    if withdrawal["status"] != "pending":
        await callback.message.edit_text(f"Already {withdrawal['status']}.")
        return

    await update_withdrawal_status(db, withdrawal_id, "paid")
    await increment_total_withdrawn(db, user_id, withdrawal["amount"])
    await log_transaction(db, user_id, "withdrawal_bank" if withdrawal.get("method") != "airtime" else "withdrawal_airtime",
                          -withdrawal["amount"],
                          f"Withdrawal paid — ₦{withdrawal['amount']:,.0f}",
                          status="confirmed", ref_id=withdrawal_id)

    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ *PAID* by @{callback.from_user.username}",
        parse_mode="Markdown"
    )

    try:
        method = withdrawal.get("method", "bank")
        if method == "airtime":
            msg = (f"🎉 *Airtime Sent!*\n\n"
                   f"₦{withdrawal['amount']:,.0f} airtime sent to {withdrawal.get('phone_number')} "
                   f"({withdrawal.get('network')}).")
        else:
            msg = (f"🎉 *Payment Sent!*\n\n"
                   f"₦{withdrawal['amount']:,.0f} has been transferred to your "
                   f"{withdrawal['bank_name']} account ({withdrawal['bank_account']}).\n"
                   f"It should arrive within minutes.")
        await bot.send_message(user_id, msg, parse_mode="Markdown")
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

    from bson import ObjectId
    withdrawal = await db.withdrawals.find_one({"_id": ObjectId(withdrawal_id)})
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
