# 🤖 MOREMONEE-Style Telegram Earn Bot

A full task-and-earn Telegram bot with:
- ✅ Channel join tasks (auto-verified)
- ✅ WhatsApp / link / custom tasks (manual admin approval)
- ✅ Referral system with rewards
- ✅ Dashboard with balance, referral link, task list
- ✅ Bank account setup with Flutterwave verification
- ✅ Withdrawal with Flutterwave NGN bank transfer
- ✅ Full admin panel via bot commands

---

## 🚀 Setup

### 1. Clone & Install

```bash
git clone <your-repo>
cd moremonee_bot
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
nano .env
```

Fill in:
- `BOT_TOKEN` — from [@BotFather](https://t.me/BotFather)
- `MONGO_URI` — MongoDB connection (local or Atlas)
- `ADMIN_IDS` — your Telegram user ID (get from [@userinfobot](https://t.me/userinfobot))
- `FLW_SECRET_KEY` / `FLW_PUBLIC_KEY` — from [Flutterwave Dashboard](https://dashboard.flutterwave.com)

### 3. Run

```bash
python main.py
```

---

## ⚙️ Admin Commands

| Command | Description |
|---|---|
| `/admin` | Show admin menu |
| `/addtask` | Add a new task (guided) |
| `/savetask` | Save task after /addtask |
| `/listtasks` | List all active tasks |
| `/deltask [id]` | Delete a task |
| `/setminwithdraw [amount]` | Set minimum withdrawal (₦) |
| `/setreferralreward [amount]` | Set referral reward (₦) |
| `/setrewardpool [amount]` | Update reward pool display |
| `/stats` | View bot statistics |
| `/broadcast [message]` | Message all users |
| `/ban [user_id]` | Ban a user |
| `/unban [user_id]` | Unban a user |
| `/addbalance [user_id] [amount]` | Manually credit a user |
| `/pendingwithdrawals` | View and process withdrawals |
| `/pendingtasks` | View pending task approvals |

---

## 📋 Task Types

| Type | Confirm | Description |
|---|---|---|
| `join_channel` | auto | Bot checks via Telegram API |
| `join_group` | auto/manual | Telegram group join |
| `whatsapp` | manual | Admin approves manually |
| `visit_link` | manual | Trust-based, admin approves |
| `custom` | manual | Any custom task |

---

## 🏦 Flutterwave Setup

1. Sign up at [flutterwave.com](https://flutterwave.com)
2. Go to **Settings → API Keys**
3. Copy your **Secret Key** and **Public Key**
4. For live payouts, complete KYC and switch to Live mode
5. Update `.env` with live keys when ready

---

## ☁️ Hosting (Railway — Recommended)

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and deploy
railway login
railway init
railway up
```

Set environment variables in Railway dashboard under **Variables**.

---

## 📁 Project Structure

```
moremonee_bot/
├── main.py                  # Entry point
├── requirements.txt
├── .env.example
├── config/
│   └── settings.py          # Env config
├── models/
│   └── db.py                # All DB helpers
├── handlers/
│   ├── user.py              # /start, onboarding, dashboard
│   ├── tasks.py             # Task display & completion
│   ├── withdrawal.py        # Bank setup & withdrawals
│   └── admin.py             # Admin commands
└── utils/
    ├── keyboards.py         # All inline keyboards
    └── flutterwave.py       # Flutterwave API wrapper
```

---

## 🔒 Security Notes

- Never commit `.env` to git — add it to `.gitignore`
- Use Flutterwave **Test keys** while testing, **Live keys** for production
- Set up Flutterwave webhook for transfer status updates (optional)
- MongoDB Atlas recommended for production (free tier available)
