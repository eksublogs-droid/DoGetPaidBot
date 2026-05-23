import os
from dataclasses import dataclass


@dataclass
class Settings:
    # Bot
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # MongoDB
    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    DB_NAME: str = os.getenv("DB_NAME", "moremonee_db")

    # Admin
    ADMIN_IDS: list = None  # Will be set from env

    # Flutterwave
    FLW_SECRET_KEY: str = os.getenv("FLW_SECRET_KEY", "")
    FLW_PUBLIC_KEY: str = os.getenv("FLW_PUBLIC_KEY", "")
    
    def __post_init__(self):
        admin_ids_str = os.getenv("ADMIN_IDS", "")
        self.ADMIN_IDS = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip()]


settings = Settings()
