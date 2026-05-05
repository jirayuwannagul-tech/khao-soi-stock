import os
from dotenv import load_dotenv

load_dotenv()

LINE_NOTIFY_TOKEN = os.getenv("LINE_NOTIFY_TOKEN", "")
SECRET_KEY = os.getenv("SECRET_KEY", "khao-soi-secret-key-2024")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin1234")
DATABASE_PATH = os.getenv("DATABASE_PATH", "database.db")

# Duplicate submission guard (seconds)
SUBMIT_COOLDOWN_SECONDS = 120
