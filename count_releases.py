import os
import json
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# === КОНФИГУРАЦИЯ ANILIBERTY API V1 (OAS 3.0) ===
API_BASE = "https://anilibria.top/api/v1"
LIST_ENDPOINT = f"{API_BASE}/anime/releases/list"
OUTPUT_DIR = "mirrors"
M3U_FILE = os.path.join(OUTPUT_DIR, "aniliberty_all.m3u")
PROGRESS_FILE = os.path.join(OUTPUT_DIR, "parser_progress.json")
POSTERS_DIR = os.path.join(OUTPUT_DIR, "posters")

LIMIT = 50            # Оптимальный размер страницы согласно доке
MAX_WORKERS = 10      # Потоки для постеров
LOG_EVERY = 50        # Частота логов для CI
RETRY_DELAY = 5       # Базовая задержка при ошибках
RATE_LIMIT_DELAY = 60 # Задержка при 429

# Поля, которые нам НУЖНЫ (согласно моделям v1)
INCLUDE_FIELDS = "id,name.main,episodes.ordinal,episodes.name,episodes.hls_720,poster.optimized.preview"
# Поля, которые ИСКЛЮЧАЕМ для ускорения (приоритет над include)
EXCLUDE_FIELDS = "description,torrents,members,sponsors,genres,season,type,age_rating,publish_day"

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(POSTERS_DIR, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": "AniLibertyParser/3.0-OAS (GitHub Actions)",
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
    return {"processed_ids": [], "total_episodes": 0, "last_page": 1}


def save_progress(data):
    tmp_file = PROGRESS_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, PROGRESS_FILE)


def fetch_releases_page(page_num):
    """
    GET /anime/releases/list
    Согласно OAS 3.0: поддерживает page, limit, include, exclude
    Возвращает: (items, current_page, last_page, total)
    """
    params = {
        "page": page_num,
        "limit": LIMIT,
        "include": INCLUDE_FIELDS,
        "exclude": EXCLUDE_FIELDS
    }

    for attempt in range(3):
        try:
            resp = session.get(LIST_ENDPOINT, params=params, timeout=30)

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", RATE_LIMIT_DELAY))
                log(f"⏳ Rate-limit 429. Ждем {wait}с...")
                time.sleep(wait)
                continue

            if resp.status_code == 200:
                data = resp.json()
                items = data.get("data", [])
                meta = data.get("meta", {}).get("pagination", {})
                return (
                    items,
                    meta.get("current_page", page_num),
                    meta.get("total_pages", 9999),
                    meta.get("total", 0)
                )

            log(f"⚠️ HTTP {resp.status_code} на стр. {page_num}. Попытка {attempt+1}/3")
            time.sleep(RETRY_DELAY * (attempt + 1))

        except requests.exceptions.RequestException as e:
            log(f"❌ Сеть: {e}. Попытка {attempt+1}/3")
            time.sleep(RETRY_DELAY * (attempt + 1))

    return None, page_num, 0, 0


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
    Обрабатывает модель models.anime.releases.v1.release
    Извлекает hls_720 и poster.optimized.preview
    """
    rid = release.get("id")
    name_obj = release.get("name", {}) or {}
    title = name_obj.get("main") or "Unknown"

    # Постер: poster.optimized.preview (согласно include)
    poster_url = None
    poster_obj = release.get("poster", {}) or {}
    optimized = poster_obj.get("optimized", {}) or {}
    poster_path = optimized.get("preview") or poster_obj.get("preview")
    if poster_path:
        # Если путь относительный, добавляем хост
        if poster_path.startswith("/"):
            poster_url = f"https://anilibria.top{poster_path}"
        else:
            poster_url = poster_path

    m3u_lines = []
    episodes = release.get("episodes", []) or []

    for ep in episodes:
        stream_url = ep.get("hls_720")
        if not stream_url:
            continue

        ordinal = ep.get("ordinal") or "?"
        ep_name = ep.get("name") or f"Серия {ordinal}"
        extinf = f'#EXTINF:-1 tvg-logo="{poster_url or ""}",{title} — {ep_name}'
        m3u_lines.append((extinf, stream_url))

    poster_task = (poster_url, f"{rid}.jpg") if poster_url else None
    return rid, m3u_lines, poster_task


def main():
    progress = load_progress()
    processed_set = set(progress.get("processed_ids", []))
    current_page = progress.get("last_page", 1)

    log("🚀 Старт парсинга AniLiberty API V1 (/anime/releases/list)...")

    # Первый запрос для получения total
    first_items, _, _, total = fetch_releases_page(1)
    if first_items is not None:
        log(f"📊 Всего релизов в базе: {total}")

    total_new_episodes = 0
    poster_tasks = []
    batch_m3u = []

    while True:
        items, page, last_page, _ = fetch_releases_page(current_page)

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

        if current_page % LOG_EVERY == 0 or current_page >= last_page:
            log(f"📦 Стр. {current_page}/{last_page} | Новых: {new_count} | Эп.: {total_new_episodes}")

        # Сохраняем прогресс каждые 10 страниц
        if current_page % 10 == 0:
            progress["processed_ids"] = list(processed_set)
            progress["total_episodes"] = progress.get("total_episodes", 0) + total_new_episodes
            progress["last_page"] = current_page
            save_progress(progress)
            total_new_episodes = 0

        if current_page >= last_page:
            break

        current_page += 1
        time.sleep(0.3)

    # Финальное сохранение
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

    log(f"🎉 ГОТОВО! Релизов: {len(processed_set)} | Эпизодов: {progress['total_episodes']}")


if __name__ == "__main__":
    main()
