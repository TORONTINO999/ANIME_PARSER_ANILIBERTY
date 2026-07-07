#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AniLiberty API Parser v5 — ULTRA TURBO
- 15 параллельных потоков
- Прогресс-файл (мгновенное продолжение)
- Уменьшенные таймауты
- Оптимизированный M3U генератор
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
#  НАСТРОЙКИ — МАКСИМАЛЬНАЯ СКОРОСТЬ
# ══════════════════════════════════════════════════════════════
BASE_URL = "https://anilibria.top"
API_BASE = f"{BASE_URL}/api/v1"
MIRRORS_DIR = "mirrors"
GLOBAL_M3U = "aniliberty_all.m3u"
PROGRESS_FILE = "parser_progress.json"

PAGE_LIMIT = 50
MAX_WORKERS = 15           # 15 параллельных потоков (было 6)
REQUEST_DELAY = 0.02       # 0.02 сек задержка (было 0.15)
MAX_RETRIES = 2            # 2 попытки (было 3)
TIMEOUT = 10               # 10 сек таймаут (было 20)
SAVE_PROGRESS_EVERY = 50   # сохранять прогресс каждые 50 релизов

# HTTP сессия
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Referer': 'https://anilibria.top/',
})

# Потокобезопасные счетчики
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

def make_absolute_url(path: str) -> str:
    if not path:
        return ""
    if path.startswith('http'):
        return path
    return urljoin(BASE_URL, path)

def get_best_hls(episode: dict) -> str:
    for quality in ['hls_1080', 'hls_720', 'hls_480']:
        url = episode.get(quality)
        if url and isinstance(url, str) and url.startswith('http'):
            return url
    return None

def retry_request(url: str, params: dict = None):
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except:
            if attempt < MAX_RETRIES - 1:
                time.sleep(0.5)
            else:
                return None

# ══════════════════════════════════════════════════════════════
#  ПРОГРЕСС — БЫСТРОЕ СОХРАНЕНИЕ/ЗАГРУЗКА
# ══════════════════════════════════════════════════════════════
def load_progress() -> set:
    """Загрузить ID уже обработанных релизов"""
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return set(data.get('done_ids', []))
        except:
            pass
    return set()

def save_progress(done_ids: set):
    """Сохранить прогресс"""
    try:
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump({'done_ids': list(done_ids), 'updated': time.time()}, f)
    except:
        pass

# ══════════════════════════════════════════════════════════════
#  API — ПОЛУЧЕНИЕ КАТАЛОГА
# ══════════════════════════════════════════════════════════════
def fetch_all_releases() -> list:
    print("=" * 60)
    print("📡  Получаю список ВСЕХ релизов...")
    print("=" * 60)
    
    all_releases = []
    page = 1
    total_pages = None
    
    while True:
        url = f"{API_BASE}/anime/catalog/releases"
        params = {
            'page': page,
            'limit': PAGE_LIMIT,
            'sorting': 'fresh_at',
            'sort_direction': 'desc'
        }
        
        data = retry_request(url, params)
        if not data:
            break
        
        releases = data.get('data', [])
        if not releases:
            break
        
        all_releases.extend(releases)
        
        pagination = data.get('meta', {}).get('pagination', {})
        total_pages = pagination.get('total_pages', 1)
        total = pagination.get('total', 0)
        
        percent = (page / total_pages * 100) if total_pages else 0
        bar_len = 30
        filled = int(bar_len * page / total_pages) if total_pages else 0
        bar = '█' * filled + '░' * (bar_len - filled)
        
        print(f"  📄 [{bar}] {percent:5.1f}% | Стр. {page}/{total_pages} | Всего: {len(all_releases)}/{total}")
        
        if page >= total_pages:
            break
        
        page += 1
        time.sleep(REQUEST_DELAY)
    
    print(f"\n✅ Получено релизов: {len(all_releases)}")
    return all_releases

# ══════════════════════════════════════════════════════════════
#  ОБРАБОТКА РЕЛИЗА (в отдельном потоке)
# ══════════════════════════════════════════════════════════════
def process_and_save(release: dict, done_ids: set) -> dict:
    """Полная обработка одного релиза: API → M3U + постер"""
    rid = release.get('id')
    title_ru = release.get('name', {}).get('main', 'Unknown')
    
    if not rid:
        return None
    
    # Пропускаем если уже обработан (из прогресс-файла)
    if rid in done_ids:
        with lock:
            stats['skipped'] += 1
        return {'skipped': True, 'title': title_ru}
    
    # Получаем детали
    url = f"{API_BASE}/anime/releases/{rid}"
    details = retry_request(url)
    if not details:
        with lock:
            stats['processed'] += 1
        return None
    
    episodes_raw = details.get('episodes', [])
    if not episodes_raw:
        with lock:
            stats['processed'] += 1
        return None
    
    # Извлекаем HLS
    episodes = []
    for ep in episodes_raw:
        hls_url = get_best_hls(ep)
        if not hls_url:
            continue
        episodes.append({
            'number': ep.get('ordinal', '?'),
            'name': ep.get('name', '') or f"Серия {ep.get('ordinal', '?')}",
            'url': hls_url,
            'duration': ep.get('duration', -1),
        })
    
    if not episodes:
        with lock:
            stats['processed'] += 1
        return None
    
    # Постер
    poster_obj = details.get('poster', {})
    poster_url = make_absolute_url(
        poster_obj.get('src') or 
        poster_obj.get('preview') or 
        (poster_obj.get('optimized', {}) or {}).get('src') or ''
    )
    
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
            duration = ep.get('duration', -1)
            ep_title = f"S{ep['number']:02d} — {ep['name']}" if isinstance(ep['number'], int) else ep['name']
            f.write(f"#EXTINF:{duration} tvg-logo=\"{poster_url}\",{title_ru} - {ep_title}\n")
            f.write(f"{ep['url']}\n")
    
    # JSON
    metadata = {
        'id': rid,
        'title_ru': title_ru,
        'title_en': details.get('name', {}).get('english', ''),
        'alias': details.get('alias', ''),
        'year': details.get('year', ''),
        'poster': poster_filename,
        'poster_url': poster_url,
        'episodes_count': len(episodes),
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
#  ГЛОБАЛЬНЫЙ M3U — ОПТИМИЗИРОВАННЫЙ
# ══════════════════════════════════════════════════════════════
def generate_global_m3u():
    print(f"\n{'=' * 60}")
    print(f"📝  Генерирую общий M3U: {GLOBAL_M3U}")
    print(f"{'=' * 60}")
    
    total_eps = 0
    count = 0
    
    # Получаем список папок один раз
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
                        duration_part = line.split(',')[0]
                        title_part = line.split(',', 1)[1].strip()
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
    print("║" + "  AniLiberty API Parser v5 — ULTRA TURBO".center(58) + "║")
    print("║" + f"  Потоков: {MAX_WORKERS} | Таймаут: {TIMEOUT}с".center(58) + "║")
    print("╚" + "═" * 58 + "╝")
    print()
    
    os.makedirs(MIRRORS_DIR, exist_ok=True)
    
    # Загружаем прогресс
    done_ids = load_progress()
    if done_ids:
        print(f"🔄  Найдено сохранение прогресса: {len(done_ids)} уже обработано")
    
    # 1. Получаем все релизы
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
                
                # Сохраняем прогресс каждые SAVE_PROGRESS_EVERY релизов
                if result and not result.get('skipped') and result.get('id'):
                    done_ids.add(result['id'])
                    
                    now = time.time()
                    if now - last_save >= 10:  # или каждые 10 секунд
                        save_progress(done_ids)
                        last_save = now
                
                # Обновляем прогресс каждые 2 секунды
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
    
    # Финальное сохранение прогресса
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