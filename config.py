import os
import json
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# === КОНФИГУРАЦИЯ ===
API_BASE = "https://anilibria.top/api/v1"
CATALOG_URL = f"{API_BASE}/anime/catalog/releases"
OUTPUT_DIR = "mirrors"
ANIME_DIR = os.path.join(OUTPUT_DIR, "anime")       # папка с отдельными M3U
POSTERS_DIR = os.path.join(OUTPUT_DIR, "posters")    # постеры
M3U_FILE = os.path.join(OUTPUT_DIR, "aniliberty_all.m3u")  # общий в mirrors
ROOT_M3U = "aniliberty_all.m3u"                      # общий в корне (коммитится)
PROGRESS_FILE = os.path.join(OUTPUT_DIR, "parser_progress.json")

LIMIT = 50
MAX_WORKERS = 10
DELAY = 0.3
LOG_EVERY = 10
RETRY_DELAY = 5

os.makedirs(ANIME_DIR, exist_ok=True)
os.makedirs(POSTERS_DIR, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": "AniLibertyParser/2.0 (GitHub Actions)",
    "Accept": "application/json"
})

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {"processed_ids": [], "last_page": 1}

def save_progress(data):
    tmp = PROGRESS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROGRESS_FILE)

def fetch_page(page):
    params = {
        "page": page,
        "limit": LIMIT,
        "sort_by": "id",
        "order": "asc"
    }
    for attempt in range(3):
        try:
            resp = session.get(CATALOG_URL, params=params, timeout=30)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 60))
                log(f"⏳ Rate-limit. Ждём {wait}с...")
                time.sleep(wait)
                continue
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("data", [])
                pagination = data.get("meta", {}).get("pagination", {})
                return items, pagination.get("current_page", page), pagination.get("last_page", 9999)
            log(f"⚠️ HTTP {resp.status_code} на стр. {page}. Попытка {attempt+1}/3")
            time.sleep(RETRY_DELAY * (attempt + 1))
        except requests.exceptions.RequestException as e:
            log(f"❌ Ошибка сети: {e}. Попытка {attempt+1}/3")
            time.sleep(RETRY_DELAY * (attempt + 1))
    return None, page, 0

def download_poster(url, filename):
    path = os.path.join(POSTERS_DIR, filename)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return True
    try:
        r = session.get(url, timeout=20)
        if r.status_code == 200 and len(r.content) > 0:
            with open(path, "wb") as f:
                f.write(r.content)
            return True
    except Exception:
        pass
    return False

def sanitize_filename(name):
    """Убирает недопустимые символы из названия файла"""
    forbidden = '<>:"/\\|?*'
    for ch in forbidden:
        name = name.replace(ch, "_")
    return name[:100]  # ограничиваем длину

def process_release(release):
    rid = release.get("id")
    name_obj = release.get("name", {})
    title = name_obj.get("main") or name_obj.get("english") or release.get("alias", f"Unknown_{rid}")
    
    # Постер
    poster_url = None
    poster_obj = release.get("poster", {})
    if poster_obj:
        optimized = poster_obj.get("optimized", {})
        poster_url = optimized.get("preview") or optimized.get("thumbnail")
        if not poster_url:
            poster_url = poster_obj.get("preview")
    
    # Эпизоды
    episodes = release.get("episodes", [])
    if not episodes:
        return None, None, None, None
    
    # Собираем строки для M3U
    lines = []
    has_streams = False
    
    for ep in episodes:
        stream_url = ep.get("hls_720") or ep.get("hls_480") or ep.get("hls_1080")
        if not stream_url:
            continue
        
        has_streams = True
        ep_name = ep.get("name") or f"Серия {ep.get('ordinal', '?')}"
        extinf = f'#EXTINF:-1 tvg-logo="{poster_url or ""}",{title} — {ep_name}'
        lines.append(f"{extinf}\n{stream_url}")
    
    if not has_streams:
        return None, None, None, None
    
    # Имя файла для отдельного M3U
    safe_title = sanitize_filename(title)
    anime_m3u_filename = f"{safe_title}.m3u"
    
    return rid, title, lines, anime_m3u_filename, poster_url

def main():
    progress = load_progress()
    processed_set = set(progress.get("processed_ids", []))
    current_page = progress.get("last_page", 1)
    
    log("🚀 Старт парсинга AniLiberty API V1 Catalog...")
    
    all_m3u_lines = []        # для общего файла
    poster_tasks = []
    total_anime = 0
    
    while True:
        items, page, last_page = fetch_page(current_page)
        
        if items is None:
            log("💥 Не удалось получить страницу. Остановка.")
            break
        
        if not items:
            log(f"✅ Каталог закончен на странице {current_page}")
            break
        
        new_count = 0
        for release in items:
            result = process_release(release)
            if result[0] is None:
                continue
            
            rid, title, lines, anime_file, poster_url = result
            
            if rid in processed_set:
                continue
            
            # Сохраняем отдельный M3U для этого аниме
            anime_path = os.path.join(ANIME_DIR, anime_file)
            with open(anime_path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                f.write("\n".join(lines))
                f.write("\n")
            
            # Добавляем в общий список
            all_m3u_lines.extend(lines)
            
            # Постер в очередь
            if poster_url:
                poster_tasks.append((poster_url, f"{rid}.jpg"))
            
            processed_set.add(rid)
            new_count += 1
            total_anime += 1
        
        # Логирование
        if current_page % LOG_EVERY == 0 or current_page >= last_page:
            log(f"📦 Стр. {current_page}/{last_page} | Новых аниме: {new_count} | Всего: {total_anime}")
        
        # Сохраняем прогресс каждые 10 страниц
        if current_page % 10 == 0:
            progress["processed_ids"] = list(processed_set)
            progress["last_page"] = current_page
            save_progress(progress)
            log(f"💾 Прогресс сохранён (стр. {current_page})")
        
        if current_page >= last_page:
            break
        
        current_page += 1
        time.sleep(DELAY)
    
    # Финальное сохранение прогресса
    progress["processed_ids"] = list(processed_set)
    progress["last_page"] = current_page
    save_progress(progress)
    
    # Сохраняем общий M3U в mirrors/
    if all_m3u_lines:
        log(f"💾 Сохраняю общий M3U ({len(all_m3u_lines)} эпизодов)...")
        
        # В mirrors/
        with open(M3U_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            f.write("\n".join(all_m3u_lines))
            f.write("\n")
        
        # В корень репозитория (для коммита)
        with open(ROOT_M3U, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            f.write("\n".join(all_m3u_lines))
            f.write("\n")
        
        log(f"✅ Общий M3U сохранён: {M3U_FILE} и {ROOT_M3U}")
    
    # Скачивание постеров
    if poster_tasks:
        log(f"🖼️ Скачивание {len(poster_tasks)} постеров...")
        done = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(download_poster, u, f): f for u, f in poster_tasks}
            for _ in as_completed(futures):
                done += 1
                if done % 200 == 0 or done == len(poster_tasks):
                    log(f"  Постеров: {done}/{len(poster_tasks)}")
    
    log(f"🎉 ГОТОВО! Аниме: {total_anime} | Эпизодов: {len(all_m3u_lines)} | Постеров: {len(poster_tasks)}")

if __name__ == "__main__":
    main()
