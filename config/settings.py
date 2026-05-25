import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    # Bot
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # MongoDB
    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    DB_NAME: str = os.getenv("DB_NAME", "dogpaid_db")

    # Admin
    ADMIN_IDS: list = None

    # Paystack
    PAYSTACK_SECRET_KEY: str = os.getenv("PAYSTACK_SECRET_KEY", "")

    # Channel to publish ads (set your channel username or ID)
    ADS_CHANNEL_ID: str = os.getenv("ADS_CHANNEL_ID", "")

    def __post_init__(self):
        admin_ids_str = os.getenv("ADMIN_IDS", "")
        self.ADMIN_IDS = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip()]


settings = Settings()
