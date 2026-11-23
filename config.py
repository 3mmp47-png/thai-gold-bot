import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Settings
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Gemini AI Settings (Optional - falls back to rule-based Thai summary if empty)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

# Market & Trading Parameters
TIMEFRAME = os.getenv("TIMEFRAME", "5m")           # 5m, 15m, 1h
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "30"))
SPREAD_THB = float(os.getenv("SPREAD_THB", "50"))  # ~50 THB for online gold apps, 100 THB for physical bar
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", "300"))  # 5 minutes

# Thai Gold Conversion Constants (1 Baht Gold = 15.244g, 96.5% purity, 1 Troy Oz = 31.1035g)
THAI_BAHT_GOLD_GRAMS = 15.244
THAI_GOLD_PURITY = 0.965
TROY_OZ_GRAMS = 31.1035
THAI_GOLD_FACTOR = (THAI_BAHT_GOLD_GRAMS * THAI_GOLD_PURITY) / TROY_OZ_GRAMS  # ~0.472909
