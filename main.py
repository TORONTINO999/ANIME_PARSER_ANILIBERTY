import os
import json
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# === КОНФИГУРАЦИЯ ANILIBERTY API V1 ===
API_ENDPOINT = "https://anilibria.top/api/v1/anime/catalog/releases"
OUTPUT_DIR = "mirrors"
M3U_FILE = os.path.join(OUTPUT_DIR, "aniliberty_all.m3u")
PROGRESS_FILE = os.path.join(OUTPUT_DIR, "parser_progress.json")
POSTERS_DIR = os.path.join(OUTPUT_DIR, "posters")

LIMIT = 50          # Оптимальный лимит для API V1
MAX_WORKERS = 10    # Потоков для параллельного скачивания картинок
LOG_EVERY = 20      # Частота вывода логов страниц в CI

# Создаем папки
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(POSTERS_DIR, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": "AniLibertyParser/2.0 (GitHub Actions)",
    "Accept": "application/json"
})

def log(msg):
    """Логирование с принудительным сбросом буфера для GitHub Actions логов"""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"processed_ids": [], "last_id": 0}

def save_progress(data):
    """Атомарная запись через временный файл во избежание повреждения JSON"""
    tmp_file = PROGRESS_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, PROGRESS_FILE)

def fetch_catalog_chunk(after_id):
    """Запрос пачки релизов, отсортированных детерминированно по ID"""
    params = {
        "limit": LIMIT,
        "sort_by": "id",
        "order": "asc"
    }
    if after_id > 0:
        params["after"] = after_id  # Запрашиваем релизы с ID выше сохраненного
        
    for attempt in range(3):
        try:
            resp = session.get(API_ENDPOINT, params=params, timeout=30)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 45))
                log(f"⏳ Rate-limit (429). Ждем {wait} сек...")
                time.sleep(wait)
                continue
            if resp.status_code == 200:
                return resp.json()
            time.sleep(5 * (attempt + 1))
        except:
            time.sleep(5 * (attempt + 1))
    return None

def download_poster(url, filename):
    path = os.path.join(POSTERS_DIR, filename)
    if os.path.exists(path):
        return True
    try:
        r = session.get(url, timeout=15)
        if r.status_code == 200 and len(r.content) > 0:
            with open(path, "wb") as f:
                f.write(r.content)
            return True
    except:
        pass
    return False

def main():
    progress = load_progress()
    processed_set = set(progress.get("processed_ids", []))
    last_id = progress.get("last_id", 0)
    
    log(f"🚀 Старт парсинга базы AniLiberty V1. Начинаем с ID: {last_id}")
    
    batch_m3u = []
    poster_tasks = []
    chunk_count = 0
    
    while True:
        data = fetch_catalog_chunk(last_id)
        if not data or "data" not in data:
            log("💥 Ошибка сети или API недоступно после повторов. Выходим.")
            break
            
        items = data["data"]
        if not items:
            log("✅ Новых данных в каталоге больше нет.")
            break
            
        for release in items:
            rid = release.get("id")
            if rid in processed_set:
                if rid > last_id:
                    last_id = rid
                continue
                
            name_obj = release.get("name", {})
            title = name_obj.get("main") or name_obj.get("english") or "Unknown"
            
            # Постер
            poster_url = None
            poster_obj = release.get("poster", {})
            if poster_obj:
                opt = poster_obj.get("optimized", {})
                poster_url = opt.get("preview") or opt.get("thumbnail") or poster_obj.get("preview")
                
            # Эпизоды
            for ep in release.get("episodes", []):
                stream_url = ep.get("hls_720") or ep.get("hls_480") or ep.get("hls_1080")
                if not stream_url:
                    continue
                ep_name = ep.get("name") or f"Серия {ep.get('ordinal', '?')}"
                extinf = f'#EXTINF:-1 tvg-logo="{poster_url or ""}",{title} — {ep_name}'
                batch_m3u.append((extinf, stream_url))
                
            if poster_url:
                poster_tasks.append((poster_url, f"{rid}.jpg"))
                
            processed_set.add(rid)
            if rid > last_id:
                last_id = rid

        chunk_count += 1
        if chunk_count % LOG_EVERY == 0:
            log(f"📦 Обработано пачек: {chunk_count} | Текущий Last ID: {last_id} | В буфере серий: {len(batch_m3u)}")
            
        # Инкрементальный сброс прогресса каждые 5 пачек (защита от падения CI)
        if chunk_count % 5 == 0:
            progress["processed_ids"] = list(processed_set)
            progress["last_id"] = last_id
            save_progress(progress)
            
        time.sleep(0.4) # Бережное отношение к API

    # Запись результатов в M3U плейлист
    if batch_m3u:
        log(f"💾 Запись {len(batch_m3u)} новых записей в M3U файл...")
        mode = "a" if os.path.exists(M3U_FILE) else "w"
        with open(M3U_FILE, mode, encoding="utf-8") as f:
            if mode == "w":
                f.write("#EXTM3U\n")
            for extinf, url in batch_m3u:
                f.write(f"{extinf}\n{url}\n")

    # Многопоточное скачивание картинок
    if poster_tasks:
        log(f"🖼️ Скачивание {len(poster_tasks)} новых постеров в {MAX_WORKERS} потоках...")
        done = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(download_poster, u, f): f for u, f in poster_tasks}
            for _ in as_completed(futures):
                done += 1
                if done % 100 == 0 or done == len(poster_tasks):
                    log(f"  Загружено картинок: {done}/{len(poster_tasks)}")

    # Финальное сохранение состояния
    progress["processed_ids"] = list(processed_set)
    progress["last_id"] = last_id
    save_progress(progress)
    log(f"🎉 Скрипт успешно завершил работу. Уникальных тайтлов в базе: {len(processed_set)}")

if __name__ == "__main__":
    main()
