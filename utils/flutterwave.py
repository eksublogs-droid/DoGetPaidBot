import httpx
import uuid
import logging
from config.settings import settings

logger = logging.getLogger(__name__)

FLW_BASE = "https://api.flutterwave.com/v3"
FLW_TOKEN_URL = "https://idp.flutterwave.com/realms/flutterwave/protocol/openid-connect/token"

# Nigerian banks list (code: name)
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


async def get_access_token() -> str:
    """Get OAuth2 access token from Flutterwave."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                FLW_TOKEN_URL,
                data={
                    "client_id": settings.FLW_CLIENT_ID,
                    "client_secret": settings.FLW_CLIENT_SECRET,
                    "grant_type": "client_credentials"
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15.0
            )
            data = response.json()
            return data.get("access_token", "")
    except Exception as e:
        logger.error(f"Token error: {e}")
        return ""


async def verify_bank_account(account_number: str, bank_code: str) -> dict:
    """Verify a Nigerian bank account via Flutterwave."""
    try:
        token = await get_access_token()
        if not token:
            return {"success": False, "message": "Auth error. Try again."}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{FLW_BASE}/accounts/resolve",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                },
                json={
                    "account_number": account_number,
                    "account_bank": bank_code
                },
                timeout=15.0
            )
            data = response.json()
            if data.get("status") == "success":
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
    """Send NGN payout via Flutterwave Transfer API."""
    reference = f"mm_{user_id}_{uuid.uuid4().hex[:8]}"
    try:
        token = await get_access_token()
        if not token:
            return {"success": False, "message": "Auth error. Try again."}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{FLW_BASE}/transfers",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                },
                json={
                    "account_bank": bank_code,
                    "account_number": account_number,
                    "amount": amount,
                    "narration": narration,
                    "currency": "NGN",
                    "reference": reference,
                    "callback_url": "",
                    "debit_currency": "NGN"
                },
                timeout=30.0
            )
            data = response.json()
            if data.get("status") == "success":
                return {
                    "success": True,
                    "reference": reference,
                    "flw_id": data["data"].get("id")
                }
            return {
                "success": False,
                "message": data.get("message", "Transfer failed")
            }
    except Exception as e:
        logger.error(f"Payout error: {e}")
        return {"success": False, "message": "Network error during transfer."}


def get_bank_name(bank_code: str) -> str:
    return NIGERIAN_BANKS.get(bank_code, "Unknown Bank")


def banks_list_text() -> str:
    """Format bank list for display."""
    lines = ["*Available Banks:*\n"]
    for code, name in NIGERIAN_BANKS.items():
        lines.append(f"`{code}` — {name}")
    return "\n".join(lines)
