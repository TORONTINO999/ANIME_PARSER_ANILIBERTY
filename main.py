import os
import json
import time
import requests
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# === КОНФИГУРАЦИЯ ===
BASE_API = "https://api.anilibria.top/v3/"  # Внутренний API v3 (самый полный)
CACHE_HOST = "https://cache.libria.fun"     # CDN для видео и постеров
OUTPUT_DIR = "mirrors"
M3U_FILE = os.path.join(OUTPUT_DIR, "aniliberty_all.m3u")
PROGRESS_FILE = os.path.join(OUTPUT_DIR, "parser_progress.json")
POSTERS_DIR = os.path.join(OUTPUT_DIR, "posters")
QUALITY = "720"  # Запрошенное качество
MAX_WORKERS = 10 # Потоков для скачивания постеров
DELAY = 0.3      # Задержка между запросами к API (чтобы не получить бан)

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(POSTERS_DIR, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Origin": "https://www.anilibria.top"
})

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_id": 0, "processed_ids": [], "total_episodes": 0}

def save_progress(data):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def fetch_all_releases():
    """Получает ВСЕ релизы через пагинацию внутреннего API"""
    all_releases = []
    page = 1
    limit = 50
    
    print("🔍 Поиск всех релизов от А до Я...")
    while True:
        try:
            resp = session.get(f"{BASE_API}title/updates", params={
                "page": page, 
                "items_per_page": limit
            }, timeout=30)
            
            if resp.status_code != 200:
                print(f"⚠️ Ошибка API: {resp.status_code}. Пауза 5с...")
                time.sleep(5)
                continue
                
            data = resp.json()
            items = data.get("list", [])
            
            if not items:
                break
                
            all_releases.extend(items)
            print(f"\r📦 Загружено страниц: {page} | Релизов: {len(all_releases)}", end="", flush=True)
            
            if len(items) < limit:
                break
                
            page += 1
            time.sleep(DELAY)
            
        except Exception as e:
            print(f"\n❌ Ошибка сети: {e}. Повтор через 5с...")
            time.sleep(5)
    
    print(f"\n✅ Всего найдено релизов: {len(all_releases)}")
    return all_releases

def download_poster(url, filename):
    """Скачивает постер асинхронно"""
    path = os.path.join(POSTERS_DIR, filename)
    if os.path.exists(path):
        return path
    try:
        r = session.get(url, timeout=15)
        if r.status_code == 200:
            with open(path, "wb") as f:
                f.write(r.content)
            return path
    except:
        pass
    return None

def build_m3u_entry(release):
    """Формирует записи для M3U8 из данных релиза"""
    entries = []
    player = release.get("player", {})
    host = player.get("host", "").rstrip("/")
    playlist = player.get("list", []) or player.get("playlist", [])
    
    names = release.get("names", {})
    title_ru = names.get("ru") or names.get("en") or release.get("code", "Unknown")
    poster_url = None
    
    # Постер
    posters = release.get("posters", {})
    medium = posters.get("medium", {}) or posters.get("small", {})
    if medium.get("url"):
        poster_url = urljoin(CACHE_HOST, medium["url"])
    
    for ep in playlist:
        ep_num = ep.get("episode") or ep.get("name") or "?"
        hls_file = ep.get("hls") or ep.get("file") or ""
        
        if not hls_file:
            continue
            
        # Формирование прямой ссылки на HLS
        if hls_file.startswith("http"):
            stream_url = hls_file
        else:
            # Стандартный паттерн Anilibria cache
            rid = release.get("id") or release.get("code")
            stream_url = f"{host}/{rid}/{ep_num}/{QUALITY}/{hls_file}"
        
        extinf = f'#EXTINF:-1 tvg-logo="{poster_url or ""}",{title_ru} — {ep_num}'
        entries.append((extinf, stream_url))
    
    return entries, poster_url

def main():
    progress = load_progress()
    processed_set = set(progress.get("processed_ids", []))
    
    releases = fetch_all_releases()
    
    # Фильтруем уже обработанные
    new_releases = [r for r in releases if r.get("id") not in processed_set]
    print(f"🆕 Новых релизов для обработки: {len(new_releases)}")
    
    if not new_releases:
        print("ℹ️ Все релизы уже обработаны.")
        return
    
    all_m3u_lines = []
    poster_tasks = []
    
    print("⚙️ Обработка плееров и серий...")
    for rel in tqdm(new_releases, desc="Parsing"):
        entries, poster_url = build_m3u_entry(rel)
        all_m3u_lines.extend(entries)
        
        if poster_url:
            fname = f"{rel.get('id', 'unknown')}.jpg"
            poster_tasks.append((poster_url, fname))
        
        processed_set.add(rel.get("id"))
        time.sleep(DELAY * 0.5)
    
    # Скачивание постеров
    if poster_tasks:
        print(f"🖼️ Скачивание {len(poster_tasks)} постеров...")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(download_poster, url, fn): fn for url, fn in poster_tasks}
            for _ in tqdm(as_completed(futures), total=len(futures), desc="Posters"):
                pass
    
    # Запись M3U8
    print(f"💾 Сохранение {M3U_FILE}...")
    mode = "a" if os.path.exists(M3U_FILE) and progress["total_episodes"] > 0 else "w"
    
    with open(M3U_FILE, mode, encoding="utf-8") as f:
        if mode == "w":
            f.write("#EXTM3U\n")
        for extinf, url in all_m3u_lines:
            f.write(f"{extinf}\n{url}\n")
    
    # Обновление прогресса
    progress["processed_ids"] = list(processed_set)
    progress["total_episodes"] += len(all_m3u_lines)
    progress["last_id"] = max(processed_set) if processed_set else 0
    save_progress(progress)
    
    print(f"🎉 ГОТОВО! Эпизодов добавлено: {len(all_m3u_lines)} | Всего: {progress['total_episodes']}")

if __name__ == "__main__":
    main()
