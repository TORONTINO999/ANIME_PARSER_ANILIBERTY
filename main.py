import os
import json
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# === КОНФИГУРАЦИЯ ANILIBERTY API V1 ===
API_BASE = "https://anilibria.top/api/v1"
CATALOG_ENDPOINT = f"{API_BASE}/anime/catalog/releases"
OUTPUT_DIR = "mirrors"
M3U_FILE = os.path.join(OUTPUT_DIR, "aniliberty_all.m3u")
PROGRESS_FILE = os.path.join(OUTPUT_DIR, "parser_progress.json")
POSTERS_DIR = os.path.join(OUTPUT_DIR, "posters")

LIMIT = 50          # Максимально допустимый лимит для V1
MAX_WORKERS = 10    # Потоков для скачивания постеров
LOG_EVERY = 50      # Частота вывода логов (чтобы не спамить CI)
RETRY_DELAY = 5     # Базовая задержка при ошибках
RATE_LIMIT_DELAY = 60 # Задержка при получении 429 Too Many Requests

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(POSTERS_DIR, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": "AniLibertyParser/2.0 (GitHub Actions)",
    "Accept": "application/json"
})


def log(msg):
    """Логирование с принудительным сбросом буфера для GitHub Actions"""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {"processed_ids": [], "total_episodes": 0, "last_page": 1}


def save_progress(data):
    tmp_file = PROGRESS_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, PROGRESS_FILE)  # Атомарная запись


def fetch_catalog_page(page_num):
    """
    Получает страницу каталога через GET /anime/catalog/releases
    Сортировка по ID гарантирует стабильный порядок без дублей
    """
    params = {
        "page": page_num,
        "limit": LIMIT,
        "sort_by": "id",       # Детерминированная сортировка
        "order": "asc"         # От старых к новым
    }
    
    for attempt in range(3):
        try:
            resp = session.get(CATALOG_ENDPOINT, params=params, timeout=30)
            
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", RATE_LIMIT_DELAY))
                log(f"⏳ Rate-limit 429. Ждем {wait}с...")
                time.sleep(wait)
                continue
                
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("data", [])
                meta = data.get("meta", {}).get("pagination", {})
                return items, meta.get("current_page", page_num), meta.get("last_page", 9999)
                
            log(f"⚠️ HTTP {resp.status_code} на стр. {page_num}. Попытка {attempt+1}/3")
            time.sleep(RETRY_DELAY * (attempt + 1))
            
        except requests.exceptions.RequestException as e:
            log(f"❌ Сеть: {e}. Попытка {attempt+1}/3")
            time.sleep(RETRY_DELAY * (attempt + 1))
            
    return None, page_num, 0


def download_poster(url, filename):
    path = os.path.join(POSTERS_DIR, filename)
    if os.path.exists(path):
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


def process_release(release):
    """
    Извлекает данные из модели models.anime.releases.v1.release
    Возвращает: (m3u_lines, poster_task)
    """
    rid = release.get("id")
    name_obj = release.get("name", {})
    title = name_obj.get("main") or name_obj.get("english") or release.get("alias", "Unknown")
    
    # Постер: берем optimized -> preview -> thumbnail
    poster_url = None
    poster_obj = release.get("poster", {})
    if poster_obj:
        opt = poster_obj.get("optimized", {})
        poster_url = opt.get("preview") or opt.get("thumbnail") or poster_obj.get("preview")
    
    m3u_lines = []
    episodes = release.get("episodes", [])
    
    for ep in episodes:
        # Приоритет: 720 -> 480 -> 1080 (как запрошено пользователем)
        stream_url = ep.get("hls_720") or ep.get("hls_480") or ep.get("hls_1080")
        if not stream_url:
            continue
            
        ep_name = ep.get("name") or f"Серия {ep.get('ordinal', '?')}"
        extinf = f'#EXTINF:-1 tvg-logo="{poster_url or ""}",{title} — {ep_name}'
        m3u_lines.append((extinf, stream_url))
    
    poster_task = (poster_url, f"{rid}.jpg") if poster_url else None
    return rid, m3u_lines, poster_task


def main():
    progress = load_progress()
    processed_set = set(progress.get("processed_ids", []))
    current_page = progress.get("last_page", 1)
    
    log("🚀 Старт парсинга AniLiberty API V1 Catalog...")
    total_new_episodes = 0
    poster_tasks = []
    batch_m3u = []
    
    while True:
        items, page, last_page = fetch_catalog_page(current_page)
        
        if items is None:
            log("💥 Не удалось получить страницу после 3 попыток. Остановка.")
            break
            
        if not items:
            log(f"✅ Каталог закончен на странице {current_page}")
            break
            
        new_count = 0
        for rel in items:
            rid, lines, ptask = process_release(rel)
            if rid not in processed_set:
                batch_m3u.extend(lines)
                if ptask:
                    poster_tasks.append(ptask)
                processed_set.add(rid)
                new_count += 1
                total_new_episodes += len(lines)
        
        # Логирование прогресса
        if current_page % LOG_EVERY == 0 or current_page >= last_page:
            log(f"📦 Стр. {current_page}/{last_page} | Новых: {new_count} | Всего эп.: {total_new_episodes}")
        
        # Сохраняем прогресс каждые 10 страниц (защита от потери при таймауте CI)
        if current_page % 10 == 0:
            progress["processed_ids"] = list(processed_set)
            progress["total_episodes"] += total_new_episodes
            progress["last_page"] = current_page
            save_progress(progress)
            # Сбрасываем счетчик, т.к. сохранили
            total_new_episodes = 0 
            
        if current_page >= last_page:
            break
            
        current_page += 1
        time.sleep(0.3)  # Вежливая пауза между запросами
    
    # Финальное сохранение прогресса
    progress["processed_ids"] = list(processed_set)
    progress["total_episodes"] = progress.get("total_episodes", 0) + total_new_episodes
    progress["last_page"] = current_page
    save_progress(progress)
    
    # Запись M3U
    if batch_m3u:
        log(f"💾 Дописываем {len(batch_m3u)} записей в M3U...")
        mode = "a" if os.path.exists(M3U_FILE) else "w"
        with open(M3U_FILE, mode, encoding="utf-8") as f:
            if mode == "w":
                f.write("#EXTM3U\n")
            for extinf, url in batch_m3u:
                f.write(f"{extinf}\n{url}\n")
    
    # Скачивание постеров
    if poster_tasks:
        log(f"🖼️ Скачивание {len(poster_tasks)} постеров...")
        done = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(download_poster, u, f): f for u, f in poster_tasks}
            for _ in as_completed(futures):
                done += 1
                if done % LOG_EVERY == 0 or done == len(poster_tasks):
                    log(f"  Постеров: {done}/{len(poster_tasks)}")
    
    log(f"🎉 ГОТОВО! Обработано страниц: {current_page} | Уникальных релизов: {len(processed_set)}")


if __name__ == "__main__":
    main()
