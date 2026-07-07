#!/usr/bin/env python3
"""
AniLiberty Weekly Mirror + M3U Generator
Собирает только ID из ids.json:
  - metadata.json (название, постер, описание, жанры, год)
  - poster.jpg
  - episodes.json (серии с прямыми ссылками hls_480/720/1080, rutube, youtube)
  - all_anime.m3u (общий плейлист)
"""

import json
import time
import sys
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# Импорт настроек
from config import (
    RELEASE_URL, MIRRORS_DIR, M3U_FILE, IDS_FILE,
    ERRORS_FILE, LAST_SYNC_FILE, ALL_IDS,
    THREADS, TIMEOUT, MAX_RETRIES, USER_AGENT
)

# Создаём папку mirrors
MIRRORS_DIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "application/json"
})

errors = {}
m3u_entries = []  # для плейлиста
stats = {"ok": 0, "fail": 0, "no_episodes": 0}


# ======================================================================
# 1. ЗАГРУЗКА ОДНОГО РЕЛИЗА
# ======================================================================
def fetch_release(anime_id: int) -> dict:
    """Получает полные данные релиза с эпизодами."""
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(
                f"{RELEASE_URL}/{anime_id}",
                params={"include": "genres,episodes"},
                timeout=TIMEOUT
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
            else:
                raise
    return None


# ======================================================================
# 2. СКАЧИВАНИЕ ПОСТЕРА
# ======================================================================
def download_poster(anime_id: int, poster_url: str, folder: Path):
    """Скачивает постер, определяя формат (jpg/webp)."""
    if not poster_url:
        return None

    # Пробуем оба формата
    for ext in ["jpg", "webp"]:
        url = poster_url.replace(".(jpg|webp)", f".{ext}")
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200 and len(r.content) > 1000:
                poster_path = folder / f"poster.{ext}"
                with open(poster_path, "wb") as f:
                    f.write(r.content)
                return str(poster_path)
        except Exception:
            continue
    return None


# ======================================================================
# 3. ИЗВЛЕЧЕНИЕ ССЫЛОК ИЗ ЭПИЗОДОВ
# ======================================================================
def extract_stream_links(episode: dict) -> dict:
    """Извлекает прямые ссылки на видео из эпизода."""
    links = {}
    # HLS ссылки
    for quality in ["hls_480", "hls_720", "hls_1080"]:
        if episode.get(quality):
            links[quality] = episode[quality]
    # Rutube
    if episode.get("rutube_id"):
        links["rutube"] = f"https://rutube.ru/video/{episode['rutube_id']}/"
    # YouTube
    if episode.get("youtube_id"):
        links["youtube"] = f"https://www.youtube.com/watch?v={episode['youtube_id']}"
    return links


# ======================================================================
# 4. ГЕНЕРАЦИЯ M3U-ЗАПИСИ
# ======================================================================
def make_m3u_entry(anime_id: int, metadata: dict, episodes: list) -> str:
    """Создаёт блок M3U для одного аниме."""
    name = metadata.get("name", {}).get("main", f"Anime {anime_id}")
    name_en = metadata.get("name", {}).get("english", "")
    year = metadata.get("year", "")
    desc = metadata.get("description", "")[:200].replace("\n", " ")
    poster = metadata.get("_poster_path", "")

    lines = [f"#EXTINF:-1 group-title=\"{name}\" tvg-logo=\"{poster}\",{name}"]
    if year:
        lines[0] += f" ({year})"
    lines.append(f"#EXTINF:-1,{name} — описание: {desc}")

    for ep in sorted(episodes, key=lambda e: e.get("sort_order", 0)):
        ep_name = ep.get("name", f"Серия {ep.get('ordinal', '?')}")
        links = ep.get("_stream_links", {})

        # Предпочитаем HLS 720p, затем 1080p, затем 480p
        stream_url = links.get("hls_720") or links.get("hls_1080") or links.get("hls_480")
        if stream_url:
            lines.append(f"#EXTINF:-1,{name} — {ep_name}")
            lines.append(stream_url)

        # Rutube как fallback
        if links.get("rutube") and not stream_url:
            lines.append(f"#EXTINF:-1,{name} — {ep_name} (Rutube)")
            lines.append(links["rutube"])

    return "\n".join(lines)


# ======================================================================
# 5. ОБРАБОТКА ОДНОГО ID
# ======================================================================
def process_anime(anime_id: int):
    """Полный цикл: загрузка, сохранение, M3U."""
    folder = MIRRORS_DIR / str(anime_id)
    folder.mkdir(parents=True, exist_ok=True)

    try:
        # Загружаем данные
        data = fetch_release(anime_id)
        if not data:
            raise Exception("Пустой ответ API")

        # Извлекаем метаданные
        metadata = {
            "id": data.get("id"),
            "name": data.get("name", {}),
            "alias": data.get("alias"),
            "year": data.get("year"),
            "season": data.get("season"),
            "description": data.get("description"),
            "age_rating": data.get("age_rating"),
            "episodes_total": data.get("episodes_total"),
            "is_ongoing": data.get("is_ongoing"),
            "genres": [g.get("name") for g in data.get("genres", [])],
            "poster_url": data.get("poster", {}).get("optimized", {}).get("thumbnail", ""),
        }

        # Скачиваем постер
        poster_path = download_poster(anime_id, metadata["poster_url"], folder)
        metadata["_poster_path"] = poster_path or ""

        # Обрабатываем эпизоды
        episodes = data.get("episodes", [])
        for ep in episodes:
            ep["_stream_links"] = extract_stream_links(ep)

        # Сохраняем
        with open(folder / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        with open(folder / "episodes.json", "w", encoding="utf-8") as f:
            json.dump(episodes, f, ensure_ascii=False, indent=2)

        # Генерируем M3U-запись
        m3u_block = make_m3u_entry(anime_id, metadata, episodes)
        m3u_entries.append(m3u_block)

        # Статистика
        if episodes:
            stats["ok"] += 1
            print(f"  ✅ {anime_id}: {metadata['name'].get('main', '?')} ({len(episodes)} эп.)")
        else:
            stats["no_episodes"] += 1
            print(f"  ⚠️  {anime_id}: {metadata['name'].get('main', '?')} (0 эпизодов)")

    except Exception as e:
        stats["fail"] += 1
        errors[str(anime_id)] = str(e)[:300]
        print(f"  ❌ {anime_id}: {str(e)[:100]}")


# ======================================================================
# 6. ГЛАВНАЯ ФУНКЦИЯ
# ======================================================================
def main():
    print("=" * 60)
    print(f"🔄 AniLiberty Weekly Sync — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   Релизов для обработки: {len(ALL_IDS)}")
    print("=" * 60)

    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = {executor.submit(process_anime, aid): aid for aid in ALL_IDS}
        for i, future in enumerate(as_completed(futures), 1):
            future.result()
            if i % 100 == 0:
                print(f"   ... обработано {i}/{len(ALL_IDS)}")

    # Сохраняем общий M3U
    if m3u_entries:
        m3u_content = "#EXTM3U\n\n" + "\n\n".join(m3u_entries) + "\n"
        with open(M3U_FILE, "w", encoding="utf-8") as f:
            f.write(m3u_content)

    # Сохраняем ошибки
    if errors:
        with open(ERRORS_FILE, "w", encoding="utf-8") as f:
            json.dump(errors, f, ensure_ascii=False, indent=2)

    # Сохраняем дату синхронизации
    with open(LAST_SYNC_FILE, "w", encoding="utf-8") as f:
        f.write(datetime.now().isoformat())

    # Итоги
    print("\n" + "=" * 60)
    print("📊 ИТОГИ")
    print(f"   ✅ Успешно: {stats['ok']}")
    print(f"   ⚠️  Без эпизодов: {stats['no_episodes']}")
    print(f"   ❌ Ошибок: {stats['fail']}")
    print(f"   📁 Папок: mirrors/anime/<id>/")
    print(f"   📄 M3U: {M3U_FILE}")
    if errors:
        print(f"   ⚠️  Ошибки сохранены в {ERRORS_FILE}")
    print("=" * 60)

    return 1 if stats["fail"] > len(ALL_IDS) * 0.1 else 0  # ошибка если >10%


if __name__ == "__main__":
    sys.exit(main())
