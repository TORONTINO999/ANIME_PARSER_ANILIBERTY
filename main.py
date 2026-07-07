import os
import sys
import json
import time
import requests
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

# === КОНФИГУРАЦИЯ ===
BASE_API = "https://anilibria.top/api/v1/"
CACHE_HOST = "https://cache.libria.fun"
OUTPUT_DIR = "mirrors"
M3U_FILE = os.path.join(OUTPUT_DIR, "aniliberty_all.m3u")
PROGRESS_FILE = os.path.join(OUTPUT_DIR, "parser_progress.json")
POSTERS_DIR = os.path.join(OUTPUT_DIR, "posters")
QUALITY = "720"
MAX_WORKERS = 10
DELAY = 0.5
LOG_EVERY = 100  # Писать в лог каждые N релизов вместо tqdm

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(POSTERS_DIR, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json"
})


def log(msg):
    """Логирование с принудительным сбросом буфера для CI"""
    print(msg, flush=True)


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_id": 0, "processed_ids": [], "total_episodes": 0}


def save_progress(data):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_all_releases_v1():
    all_releases = []
    after = 0
    limit = 50

    log("🔍 Поиск всех релизов через API v1...")
    while True:
        try:
            resp = session.get(
                f"{BASE_API}title/updates",
                params={"after": after, "limit": limit},
                timeout=30
            )

            if resp.status_code == 429:
                log("⏳ Rate-limit. Пауза 30с...")
                time.sleep(30)
                continue

            if resp.status_code != 200:
                log(f"⚠️ Ошибка API v1: {resp.status_code}. Пауза 5с...")
                time.sleep(5)
                continue

            data = resp.json()
            items = data if isinstance(data, list) else data.get("list", [])

            if not items:
                break

            all_releases.extend(items)
            last_item = items[-1]
            after = last_item.get("id", after + 1)

            # Логирование порциями вместо tqdm
            if len(all_releases) % LOG_EVERY == 0 or len(items) < limit:
                log(f"📦 Загружено: {len(all_releases)} | Last ID: {after}")

            if len(items) < limit:
                break

            time.sleep(DELAY)

        except requests.exceptions.ConnectionError as e:
            log(f"❌ Ошибка соединения: {e}. Повтор через 10с...")
            time.sleep(10)
        except Exception as e:
            log(f"❌ Неизвестная ошибка: {e}. Повтор через 5с...")
            time.sleep(5)

    log(f"✅ Всего найдено релизов: {len(all_releases)}")
    return all_releases


def download_poster(url, filename):
    path = os.path.join(POSTERS_DIR, filename)
    if os.path.exists(path):
        return path
    try:
        r = session.get(url, timeout=15)
        if r.status_code == 200:
            with open(path, "wb") as f:
                f.write(r.content)
            return path
    except Exception:
        pass
    return None


def build_m3u_entry(release):
    entries = []
    player = release.get("player", {}) or {}
    host = player.get("host", "").rstrip("/")
    playlist = player.get("list", []) or []

    names = release.get("names", {}) or {}
    title_ru = names.get("ru") or names.get("en") or release.get("code", "Unknown")

    poster_url = None
    posters = release.get("posters", {}) or {}
    medium = posters.get("medium", {}) or posters.get("small", {}) or {}
    poster_path = medium.get("url") or ""
    if poster_path:
        poster_url = urljoin(CACHE_HOST, poster_path)

    for ep in playlist:
        ep_num = ep.get("episode") or ep.get("name") or "?"
        hls_file = ep.get("hls") or ep.get("file") or ""

        if not hls_file:
            continue

        if hls_file.startswith("http"):
            stream_url = hls_file
        else:
            rid = release.get("id") or release.get("code")
            stream_url = f"{host}/{rid}/{ep_num}/{QUALITY}/{hls_file}"

        extinf = f'#EXTINF:-1 tvg-logo="{poster_url or ""}",{title_ru} — {ep_num}'
        entries.append((extinf, stream_url))

    return entries, poster_url


def main():
    progress = load_progress()
    processed_set = set(progress.get("processed_ids", []))

    releases = fetch_all_releases_v1()

    new_releases = [r for r in releases if r.get("id") not in processed_set]
    log(f"🆕 Новых релизов для обработки: {len(new_releases)}")

    if not new_releases:
        log("ℹ️ Все релизы уже обработаны.")
        return

    all_m3u_lines = []
    poster_tasks = []

    log("⚙️ Обработка плееров и серий...")
    for i, rel in enumerate(new_releases, 1):
        entries, poster_url = build_m3u_entry(rel)
        all_m3u_lines.extend(entries)

        if poster_url:
            fname = f"{rel.get('id', 'unknown')}.jpg"
            poster_tasks.append((poster_url, fname))

        processed_set.add(rel.get("id"))

        # Прогресс без tqdm
        if i % LOG_EVERY == 0 or i == len(new_releases):
            log(f"  Обработано: {i}/{len(new_releases)} | Эпизодов: {len(all_m3u_lines)}")

        time.sleep(DELAY * 0.5)

    if poster_tasks:
        log(f"🖼️ Скачивание {len(poster_tasks)} постеров...")
        done_count = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(download_poster, url, fn): fn for url, fn in poster_tasks}
            for _ in as_completed(futures):
                done_count += 1
                if done_count % LOG_EVERY == 0 or done_count == len(poster_tasks):
                    log(f"  Постеров скачано: {done_count}/{len(poster_tasks)}")

    log(f"💾 Сохранение {M3U_FILE}...")
    mode = "a" if os.path.exists(M3U_FILE) and progress["total_episodes"] > 0 else "w"

    with open(M3U_FILE, mode, encoding="utf-8") as f:
        if mode == "w":
            f.write("#EXTM3U\n")
        for extinf, url in all_m3u_lines:
            f.write(f"{extinf}\n{url}\n")

    progress["processed_ids"] = list(processed_set)
    progress["total_episodes"] += len(all_m3u_lines)
    progress["last_id"] = max(processed_set) if processed_set else 0
    save_progress(progress)

    log(f"🎉 ГОТОВО! Эпизодов добавлено: {len(all_m3u_lines)} | Всего: {progress['total_episodes']}")


if __name__ == "__main__":
    main()
