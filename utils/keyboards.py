from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📋 Menu")]],
        resize_keyboard=True,
        persistent=True
    )


def colourful_dashboard_keyboard(balance: float, referral_count: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"💰 Balance: ₦{balance:,.0f}", callback_data="show_balance")],
        [
            InlineKeyboardButton(text=f"👥 Referrals: {referral_count}", callback_data="show_referrals"),
            InlineKeyboardButton(text="🔗 Referral Link", callback_data="get_referral_link"),
        ],
        [InlineKeyboardButton(text="📢 Run Ad", callback_data="run_ad")],
        [
            InlineKeyboardButton(text="✅ Tasks", callback_data="show_tasks"),
            InlineKeyboardButton(text="💸 Withdraw", callback_data="withdraw"),
        ],
        [InlineKeyboardButton(text="💳 Set Bank Account", callback_data="set_bank")],
        [
            InlineKeyboardButton(text="📜 History", callback_data="show_history"),
            InlineKeyboardButton(text="🎁 Daily Check-In", callback_data="daily_checkin"),
        ],
        [
            InlineKeyboardButton(text="👤 My Profile", callback_data="show_profile"),
            InlineKeyboardButton(text="📊 Leaderboard", callback_data="show_leaderboard"),
        ],
        [InlineKeyboardButton(text="📋 My Ads", callback_data="my_ads_status")],
        [InlineKeyboardButton(text="🗑️ Delete My Account", callback_data="delete_account")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_delete_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Yes, Delete Everything", callback_data="confirm_delete"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="dashboard"),
        ]
    ])


def welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Proceed", callback_data="proceed_onboarding")]
    ])


def onboarding_keyboard(tasks: list, show_done: bool = True) -> InlineKeyboardMarkup:
    buttons = []
    if tasks:
        first = tasks[0]
        buttons.append([InlineKeyboardButton(text=f"💸 {first['title']} ↗", url=first["link"])])
    rest = tasks[1:]
    row = []
    for task in rest:
        row.append(InlineKeyboardButton(text=f"🌐 {task['title']} ↗", url=task["link"]))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    if show_done:
        buttons.append([InlineKeyboardButton(text="✅ Done", callback_data="onboarding_done")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def whatsapp_tasks_keyboard(tasks: list) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for task in tasks:
        row.append(InlineKeyboardButton(text=f"📱 {task['title']} ↗", url=task["link"]))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="✅ I've Joined", callback_data="whatsapp_joined")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def dashboard_keyboard(balance: float, referral_count: int) -> InlineKeyboardMarkup:
    # Kept for backward compat; just calls colourful version
    return colourful_dashboard_keyboard(balance, referral_count)


def tasks_keyboard(tasks: list, completed_ids: list) -> InlineKeyboardMarkup:
    buttons = []
    for task in tasks:
        task_id = str(task["_id"])
        done = task_id in completed_ids
        paused = task.get("paused", False)
        slots = task.get("slots")
        slots_filled = task.get("slots_filled", 0)
        full = slots is not None and slots_filled >= slots

        pin_icon = "📌 " if task.get("pinned") else ""
        label = f"{pin_icon}{'✅' if done else ('⏸' if paused else ('🔴' if full else '🔲'))} {task['title']} (+₦{task['reward']:,.0f})"
        if slots is not None:
            label += f" [{slots_filled}/{slots}]"

        if done:
            buttons.append([InlineKeyboardButton(text=label, callback_data="already_done")])
        elif paused or full:
            buttons.append([InlineKeyboardButton(text=label, callback_data="task_unavailable")])
        else:
            row = []
            if task.get("link"):
                row.append(InlineKeyboardButton(text="🔗 Open Task", url=task["link"]))
            row.append(InlineKeyboardButton(text="✅ Mark Done", callback_data=f"task_done:{task_id}"))
            buttons.append([InlineKeyboardButton(text=label, callback_data="task_info")])
            buttons.append(row)

    buttons.append([InlineKeyboardButton(text="🔙 Back", callback_data="dashboard")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_to_dashboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="dashboard")]
    ])


def confirm_withdraw_keyboard(amount: float, withdrawal_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Confirm", callback_data=f"confirm_withdraw:{withdrawal_id}"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_withdraw"),
        ]
    ])


def admin_withdrawal_keyboard(withdrawal_id: str, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Mark as Paid", callback_data=f"admin_approve:{withdrawal_id}:{user_id}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"admin_reject:{withdrawal_id}:{user_id}"),
        ]
    ])


def admin_task_completion_keyboard(completion_id: str, user_id: int, task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_task:{user_id}:{task_id}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_task:{user_id}:{task_id}"),
        ]
    ])


# ─────────────────────────────────────────
# WITHDRAWAL METHOD KEYBOARD
# ─────────────────────────────────────────

def withdraw_method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🏦 Bank Account", callback_data="withdraw_method:bank"),
            InlineKeyboardButton(text="📱 Airtime", callback_data="withdraw_method:airtime"),
        ],
        [InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="dashboard")],
    ])


def airtime_network_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="MTN", callback_data="airtime_network:MTN"),
            InlineKeyboardButton(text="Airtel", callback_data="airtime_network:Airtel"),
        ],
        [
            InlineKeyboardButton(text="Glo", callback_data="airtime_network:Glo"),
            InlineKeyboardButton(text="9mobile", callback_data="airtime_network:9mobile"),
        ],
        [InlineKeyboardButton(text="🔙 Cancel", callback_data="dashboard")],
    ])


# ─────────────────────────────────────────
# ADS KEYBOARDS
# ─────────────────────────────────────────

def ad_tier_keyboard(tiers: dict) -> InlineKeyboardMarkup:
    buttons = []
    tier_labels = {"basic": "🟢 Basic", "standard": "🔵 Standard", "premium": "⭐ Premium"}
    for tier_key, info in tiers.items():
        label = f"{tier_labels.get(tier_key, tier_key)} — ₦{info['price']:,} ({info['description']})"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"ad_tier:{tier_key}")])
    buttons.append([InlineKeyboardButton(text="🔙 Cancel", callback_data="dashboard")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def ad_confirm_keyboard(ad_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Pay & Submit", callback_data=f"ad_pay:{ad_id}"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="dashboard"),
        ]
    ])


def admin_ad_review_keyboard(ad_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Approve & Publish", callback_data=f"admin_ad_approve:{ad_id}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"admin_ad_reject:{ad_id}"),
        ]
    ])


# ─────────────────────────────────────────
# HISTORY KEYBOARD
# ─────────────────────────────────────────

def history_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💰 Transactions", callback_data="history_transactions"),
            InlineKeyboardButton(text="💸 Withdrawals", callback_data="history_withdrawals"),
        ],
        [InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="dashboard")],
    ])
