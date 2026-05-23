from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Persistent bottom reply keyboard — always visible in chat."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Menu")]
        ],
        resize_keyboard=True,
        persistent=True
    )


def colourful_dashboard_keyboard(balance: float, referral_count: int) -> InlineKeyboardMarkup:
    """Colourful dashboard panel — shown when Menu button or /menu is pressed."""
    buttons = [
        [
            InlineKeyboardButton(text=f"🔵 💰 Balance: ₦{balance:,.0f}", callback_data="show_balance"),
            InlineKeyboardButton(text=f"🔵 👥 Referrals: {referral_count}", callback_data="show_referrals"),
        ],
        [InlineKeyboardButton(text="🟢 🔗 My Referral Link", callback_data="get_referral_link")],
        [InlineKeyboardButton(text="🟡 ✅ Tasks", callback_data="show_tasks")],
        [
            InlineKeyboardButton(text="🟠 💳 Set Bank Account", callback_data="set_bank"),
            InlineKeyboardButton(text="🔴 💸 Withdraw", callback_data="withdraw"),
        ],
        [InlineKeyboardButton(text="🟣 📜 Withdrawal History", callback_data="withdraw_history")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def welcome_keyboard() -> InlineKeyboardMarkup:
    """Welcome screen proceed button."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Proceed", callback_data="proceed_onboarding")]
    ])


def onboarding_keyboard(tasks: list, show_done: bool = True) -> InlineKeyboardMarkup:
    """Build the channel join keyboard."""
    buttons = []

    if tasks:
        first = tasks[0]
        buttons.append([
            InlineKeyboardButton(text=f"💸 {first['title']} ↗", url=first["link"])
        ])

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
    """WhatsApp groups keyboard with I've Joined button."""
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
    buttons = [
        [
            InlineKeyboardButton(text=f"💰 Balance: ₦{balance:,.0f}", callback_data="show_balance"),
            InlineKeyboardButton(text=f"👥 Referrals: {referral_count}", callback_data="show_referrals"),
        ],
        [InlineKeyboardButton(text="🔗 My Referral Link", callback_data="get_referral_link")],
        [InlineKeyboardButton(text="✅ Tasks", callback_data="show_tasks")],
        [
            InlineKeyboardButton(text="💳 Set Bank Account", callback_data="set_bank"),
            InlineKeyboardButton(text="💸 Withdraw", callback_data="withdraw"),
        ],
        [InlineKeyboardButton(text="📜 Withdrawal History", callback_data="withdraw_history")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def tasks_keyboard(tasks: list, completed_ids: list) -> InlineKeyboardMarkup:
    buttons = []
    for task in tasks:
        task_id = str(task["_id"])
        done = task_id in completed_ids
        label = f"{'✅' if done else '🔲'} {task['title']} (+₦{task['reward']:,.0f})"
        if done:
            buttons.append([InlineKeyboardButton(text=label, callback_data="already_done")])
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
            InlineKeyboardButton(text="✅ Approve & Pay", callback_data=f"admin_approve:{withdrawal_id}:{user_id}"),
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
