# config.py
import json
from pathlib import Path

# === НАСТРОЙКИ ===
BASE_URL = "https://aniliberty.top/api/v1"
RELEASE_URL = f"{BASE_URL}/anime/releases"

# Папки и файлы
MIRRORS_DIR = Path("mirrors/anime")
M3U_FILE = Path("all_anime.m3u")
IDS_FILE = Path("ids.json")
ERRORS_FILE = Path("errors.json")
LAST_SYNC_FILE = Path("last_sync.txt")

# Загружаем ID (только эти)
with open(IDS_FILE, "r", encoding="utf-8") as f:
    ALL_IDS = json.load(f)

# Параметры запросов
THREADS = 3
TIMEOUT = 45
MAX_RETRIES = 3
USER_AGENT = "AniLiberty-Mirror/1.0 (weekly sync)"
