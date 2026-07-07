#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AniLiberty / AniLibria Parser v6 — PURE API v3
Без BeautifulSoup, без Selenium, без HTML-парсинга.
Только публичное API AniLibria v3.

Рабочие endpoint'ы:
  - api.anilibria.tv/v3/title/updates   — список релизов
  - api.anilibria.tv/v3/title           — детали одного релиза
  - cache.libria.fun                    — CDN для HLS-стримов

HLS-ссылки строятся: https://cache.libria.fun + player.list[N].hls.fhd/hd/sd
"""

import os
import re
import json
import time
import requests
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# ══════════════════════════════════════════════════════════════
#  НАСТРОЙКИ
# ══════════════════════════════════════════════════════════════
API_BASE = "https://api.anilibria.tv/v3"
CDN_HOST = "https://cache.libria.fun"   # Хост для HLS-ссылок
MIRRORS_DIR = "mirrors"
GLOBAL_M3U = "aniliberty_all.m3u"
PROGRESS_FILE = "parser_progress.json"

MAX_WORKERS = 12
REQUEST_DELAY = 0.05
MAX_RETRIES = 3
TIMEOUT = 15
SAVE_PROGRESS_EVERY = 50

# HTTP сессия
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Referer': 'https://aniliberty.top/',
})

lock = Lock()
stats = {'processed': 0, 'saved': 0, 'skipped': 0, 'total': 0}

# ══════════════════════════════════════════════════════════════
#  УТИЛИТЫ
# ══════════════════════════════════════════════════════════════
def safe_filename(name: str) -> str:
    if not name:
        return "unknown"
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:120]

def retry_request(url: str, params: dict = None):
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(0.5 * (attempt + 1))
            else:
                return None
    return None

# ══════════════════════════════════════════════════════════════
#  ПРОГРЕСС
# ══════════════════════════════════════════════════════════════
def load_progress() -> set:
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return set(data.get('done_ids', []))
        except:
            pass
    return set()

def save_progress(done_ids: set):
    try:
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump({'done_ids': list(done_ids), 'updated': time.time()}, f)
    except:
        pass

# ══════════════════════════════════════════════════════════════
#  API — ПОЛУЧЕНИЕ ВСЕХ РЕЛИЗОВ (через title/updates)
# ══════════════════════════════════════════════════════════════
def fetch_all_releases() -> list:
    print("=" * 60)
    print("📡  Получаю список ВСЕХ релизов через API v3...")
    print("=" * 60)

    all_releases = []
    page = 0
    total_pages = None

    while True:
        url = f"{API_BASE}/title/updates"
        params = {
            'page': page,
            'items_per_page': 50,
            'filter': 'id,names,posters,status,year,type,season,description,genres,player,torrents',
        }

        data = retry_request(url, params)
        if not data:
            break

        releases = data.get('list', [])
        if not releases:
            break

        all_releases.extend(releases)

        pagination = data.get('pagination', {})
        total_pages = pagination.get('pages', 1)
        total = pagination.get('total_items', 0)

        percent = ((page + 1) / total_pages * 100) if total_pages else 0
        bar_len = 30
        filled = int(bar_len * (page + 1) / total_pages) if total_pages else 0
        bar = '█' * filled + '░' * (bar_len - filled)

        print(f"  📄 [{bar}] {percent:5.1f}% | Стр. {page + 1}/{total_pages} | Всего: {len(all_releases)}/{total}")

        if page >= total_pages - 1:
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    print(f"\n✅ Получено релизов: {len(all_releases)}")
    return all_releases

# ══════════════════════════════════════════════════════════════
#  ИЗВЛЕЧЕНИЕ HLS ИЗ PLAYER
# ══════════════════════════════════════════════════════════════
def get_best_hls(episode_player: dict, cdn_host: str) -> tuple:
    """
    Возвращает (url, quality_label) — лучший доступный HLS.
    episode_player — это значение из player.list[episode_number]
    """
    hls = episode_player.get('hls', {})
    for quality_key, label in [('fhd', '1080p'), ('hd', '720p'), ('sd', '480p')]:
        url = hls.get(quality_key)
        if url and isinstance(url, str):
            if url.startswith('http'):
                return url, label
            else:
                # Относительная ссылка — приписываем CDN
                return f"{cdn_host}{url}", label
    return None, None

def extract_episodes(release: dict) -> list:
    """Извлекает список эпизодов из release['player']"""
    player = release.get('player', {})
    if not player:
        return []

    # CDN-хост может быть указан в player.host
    cdn_host = player.get('host', 'cache.libria.fun')
    if not cdn_host.startswith('http'):
        cdn_host = f"https://{cdn_host}"

    episodes_list = player.get('list', {})
    if not episodes_list:
        return []

    episodes = []
    # player.list — это dict, где ключ = номер серии (строка)
    for ep_num_str, ep_data in episodes_list.items():
        hls_url, quality = get_best_hls(ep_data, cdn_host)
        if not hls_url:
            continue

        ep_num = ep_data.get('episode')
        if ep_num is None:
            try:
                ep_num = int(ep_num_str)
            except:
                ep_num = ep_num_str

        episodes.append({
            'number': ep_num,
            'name': ep_data.get('name') or f"Серия {ep_num}",
            'url': hls_url,
            'quality': quality,
            'uuid': ep_data.get('uuid', ''),
        })

    # Сортируем по номеру серии
    def sort_key(ep):
        try:
            return float(ep['number'])
        except:
            return 0
    episodes.sort(key=sort_key)
    return episodes

# ══════════════════════════════════════════════════════════════
#  ОБРАБОТКА РЕЛИЗА
# ══════════════════════════════════════════════════════════════
def process_and_save(release: dict, done_ids: set) -> dict:
    rid = release.get('id')
    names = release.get('names', {})
    title_ru = names.get('ru') or names.get('en') or 'Unknown'

    if not rid:
        return None

    # Пропускаем если уже обработан
    if str(rid) in done_ids or rid in done_ids:
        with lock:
            stats['skipped'] += 1
        return {'skipped': True, 'title': title_ru, 'id': rid}

    # Извлекаем эпизоды из player
    episodes = extract_episodes(release)
    if not episodes:
        with lock:
            stats['processed'] += 1
        return {'id': rid, 'title': title_ru, 'episodes': 0, 'skipped': False}

    # Постер
    posters = release.get('posters', {})
    poster_url = ''
    for size in ['original', 'medium', 'small']:
        p = posters.get(size, {})
        if p and p.get('url'):
            url = p['url']
            if url.startswith('http'):
                poster_url = url
            else:
                poster_url = f"https://anilibria.tv{url}"
            break

    # Сохраняем
    folder_name = safe_filename(f"{rid}_{title_ru}")
    folder_path = os.path.join(MIRRORS_DIR, folder_name)
    os.makedirs(folder_path, exist_ok=True)

    # Постер
    poster_filename = None
    if poster_url:
        ext = '.jpg'
        if '.png' in poster_url.lower(): ext = '.png'
        elif '.webp' in poster_url.lower(): ext = '.webp'
        poster_filename = f"poster{ext}"
        try:
            resp = session.get(poster_url, timeout=TIMEOUT)
            resp.raise_for_status()
            with open(os.path.join(folder_path, poster_filename), 'wb') as f:
                f.write(resp.content)
        except:
            poster_filename = None

    # M3U
    with open(os.path.join(folder_path, 'playlist.m3u'), 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        f.write(f"#PLAYLIST:{title_ru}\n\n")
        for ep in episodes:
            ep_title = f"S{ep['number']:02d} — {ep['name']}" if isinstance(ep['number'], (int, float)) else ep['name']
            f.write(f"#EXTINF:-1 tvg-logo=\"{poster_url}\",{title_ru} - {ep_title} [{ep['quality']}]\n")
            f.write(f"{ep['url']}\n")

    # JSON метаданные
    status = release.get('status', {})
    season = release.get('season', {})
    metadata = {
        'id': rid,
        'title_ru': names.get('ru', ''),
        'title_en': names.get('en', ''),
        'title_alt': names.get('alternative', ''),
        'alias': release.get('code', ''),
        'year': season.get('year', ''),
        'season': season.get('string', ''),
        'status': status.get('string', ''),
        'status_code': status.get('code', ''),
        'type': release.get('type', {}).get('full_string', ''),
        'genres': release.get('genres', []),
        'description': release.get('description', ''),
        'poster': poster_filename,
        'poster_url': poster_url,
        'episodes_count': len(episodes),
        'episodes': episodes,
    }
    with open(os.path.join(folder_path, 'info.json'), 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    with lock:
        stats['processed'] += 1
        stats['saved'] += 1

    return {
        'id': rid,
        'title': title_ru,
        'episodes': len(episodes),
        'poster': poster_filename is not None,
        'skipped': False
    }

# ══════════════════════════════════════════════════════════════
#  ГЛОБАЛЬНЫЙ M3U
# ══════════════════════════════════════════════════════════════
def generate_global_m3u():
    print(f"\n{'=' * 60}")
    print(f"📝  Генерирую общий M3U: {GLOBAL_M3U}")
    print(f"{'=' * 60}")

    total_eps = 0
    count = 0

    try:
        folders = [f for f in os.listdir(MIRRORS_DIR) if os.path.isdir(os.path.join(MIRRORS_DIR, f))]
    except:
        folders = []

    with open(GLOBAL_M3U, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U x-tvg-url=\"\"\n")
        f.write("#PLAYLIST:AniLiberty — Все релизы\n\n")

        for folder_name in folders:
            m3u_path = os.path.join(MIRRORS_DIR, folder_name, 'playlist.m3u')
            info_path = os.path.join(MIRRORS_DIR, folder_name, 'info.json')

            if not os.path.exists(m3u_path) or not os.path.exists(info_path):
                continue

            try:
                with open(info_path, 'r', encoding='utf-8') as jf:
                    info = json.load(jf)

                group = f"{info.get('year', '?')} | {info.get('title_ru', 'Unknown')}"
                logo = info.get('poster_url', '')
                title_ru = info.get('title_ru', 'Unknown')
                rid = info.get('id', '?')

                with open(m3u_path, 'r', encoding='utf-8') as mf:
                    lines = mf.readlines()

                for line in lines:
                    if line.startswith('#EXTINF:'):
                        parts = line.split(',', 1)
                        duration_part = parts[0]
                        title_part = parts[1].strip() if len(parts) > 1 else ''
                        f.write(f"{duration_part} tvg-id=\"{rid}\" tvg-name=\"{title_ru}\" tvg-logo=\"{logo}\" group-title=\"{group}\",{title_part}\n")
                    elif line.startswith('http'):
                        f.write(line)
                        total_eps += 1

                count += 1
            except:
                continue

    print(f"✅ Общий M3U создан: {total_eps} эпизодов из {count} релизов")
    return total_eps

# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════
def main():
    start_time = time.time()

    print("╔" + "═" * 58 + "╗")
    print("║" + "  AniLiberty Parser v6 — API v3 (cache.libria.fun)".center(58) + "║")
    print("║" + f"  Потоков: {MAX_WORKERS} | CDN: {CDN_HOST}".center(58) + "║")
    print("╚" + "═" * 58 + "╝")
    print()

    os.makedirs(MIRRORS_DIR, exist_ok=True)

    # Загружаем прогресс
    done_ids = load_progress()
    if done_ids:
        print(f"🔄  Найдено сохранение прогресса: {len(done_ids)} уже обработано")

    # 1. Получаем все релизы через API v3
    catalog = fetch_all_releases()
    if not catalog:
        print("❌ Не удалось получить каталог")
        return

    stats['total'] = len(catalog)

    # 2. Обрабатываем параллельно
    print(f"\n{'=' * 60}")
    print(f"🚀  Обрабатываю {len(catalog)} релизов в {MAX_WORKERS} потоков...")
    print(f"{'=' * 60}")

    last_print = time.time()
    last_save = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_and_save, r, done_ids): r for r in catalog}

        for future in as_completed(futures):
            try:
                result = future.result()

                if result and not result.get('skipped') and result.get('id'):
                    done_ids.add(str(result['id']))
                    done_ids.add(result['id'])

                    now = time.time()
                    if now - last_save >= 10:
                        save_progress(done_ids)
                        last_save = now

                now = time.time()
                if now - last_print >= 2:
                    last_print = now
                    with lock:
                        p = stats['processed']
                        s = stats['saved']
                        k = stats['skipped']
                        t = stats['total']

                    percent = (p / t * 100) if t else 0
                    bar_len = 30
                    filled = int(bar_len * p / t) if t else 0
                    bar = '█' * filled + '░' * (bar_len - filled)

                    elapsed = time.time() - start_time
                    speed = p / elapsed if elapsed > 0 else 0
                    eta = (t - p) / speed if speed > 0 else 0

                    print(f"  [{bar}] {percent:5.1f}% | {p}/{t} | ✅{s} ⏭️{k} | {speed:.1f} р/с | ETA: {eta:.0f}с")

            except Exception as e:
                pass

    save_progress(done_ids)

    # 3. Генерируем общий M3U
    total_eps = generate_global_m3u()

    # 4. Итоги
    elapsed = time.time() - start_time

    print(f"\n╔" + "═" * 58 + "╗")
    print("║" + "  ✅  ГОТОВО!".center(58) + "║")
    print("╠" + "═" * 58 + "╣")
    print(f"║  Релизов в каталоге: {stats['total']:<37}║")
    print(f"║  Сохранено: {stats['saved']:<46}║")
    print(f"║  Пропущено (уже есть): {stats['skipped']:<35}║")
    print(f"║  Всего эпизодов: {total_eps:<41}║")
    print(f"║  Время: {elapsed:.1f} сек ({elapsed/60:.1f} мин)".ljust(59) + "║")
    print(f"║  Папка: {MIRRORS_DIR}/".ljust(59) + "║")
    print(f"║  Общий M3U: {GLOBAL_M3U}".ljust(59) + "║")
    print("╚" + "═" * 58 + "╝")

if __name__ == '__main__':
    main()
