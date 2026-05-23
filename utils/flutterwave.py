import httpx
import uuid
import logging
from config.settings import settings

logger = logging.getLogger(__name__)

FLW_BASE = "https://api.flutterwave.com/v3"
PAYSTACK_BASE = "https://api.paystack.co"

NIGERIAN_BANKS = {
    "044": "Access Bank",
    "023": "Citibank Nigeria",
    "050": "EcoBank Nigeria",
    "011": "First Bank of Nigeria",
    "214": "First City Monument Bank (FCMB)",
    "070": "Fidelity Bank",
    "058": "Guaranty Trust Bank (GTBank)",
    "030": "Heritage Bank",
    "301": "Jaiz Bank",
    "082": "Keystone Bank",
    "526": "Moniepoint MFB",
    "076": "Polaris Bank",
    "101": "ProvidusBank",
    "221": "Stanbic IBTC Bank",
    "068": "Standard Chartered Bank",
    "232": "Sterling Bank",
    "100": "Suntrust Bank",
    "032": "Union Bank of Nigeria",
    "033": "United Bank for Africa (UBA)",
    "215": "Unity Bank",
    "566": "VFD MFB",
    "035": "Wema Bank",
    "057": "Zenith Bank",
    "999992": "OPay",
    "999991": "PalmPay",
    "090115": "Loan Box MFB",
}


async def verify_bank_account(account_number: str, bank_code: str) -> dict:
    """Verify a Nigerian bank account via Paystack."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{PAYSTACK_BASE}/bank/resolve",
                headers={
                    "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
                },
                params={
                    "account_number": account_number,
                    "bank_code": bank_code
                },
                timeout=15.0
            )
            data = response.json()
            if data.get("status") == True:
                return {
                    "success": True,
                    "account_name": data["data"]["account_name"],
                    "account_number": data["data"]["account_number"]
                }
            return {"success": False, "message": data.get("message", "Verification failed")}
    except Exception as e:
        logger.error(f"Bank verify error: {e}")
        return {"success": False, "message": "Network error. Try again."}


async def send_payout(account_number: str, bank_code: str, bank_name: str, amount: float, user_id: int, narration: str = "MOREMONEE Withdrawal") -> dict:
    """Manual payout - admin handles this."""
    return {"success": False, "message": "Manual payout required."}


def get_bank_name(bank_code: str) -> str:
    return NIGERIAN_BANKS.get(bank_code, "Unknown Bank")


def banks_list_text() -> str:
    lines = ["*Available Banks:*\n"]
    for code, name in NIGERIAN_BANKS.items():
        lines.append(f"`{code}` — {name}")
    return "\n".join(lines)
