#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AniLiberty HTML Parser — Простой парсер без API и Playwright
"""

import os
import re
import json
import time
import requests
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from bs4 import BeautifulSoup

# ══════════════════════════════════════════════════════════════
#  НАСТРОЙКИ
# ══════════════════════════════════════════════════════════════
BASE_URL = "https://aniliberty.top"
CATALOG_URL = f"{BASE_URL}/anime/catalog/"
MIRRORS_DIR = "mirrors"
GLOBAL_M3U = "aniliberty_all.m3u"
PROGRESS_FILE = "parser_progress.json"

MAX_WORKERS = 10
REQUEST_DELAY = 0.3
TIMEOUT = 15

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
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

def retry_request(url: str, max_retries: int = 3):
    for attempt in range(max_retries):
        try:
            resp = session.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp
        except:
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                return None

# ══════════════════════════════════════════════════════════════
#  ПРОГРЕСС
# ══════════════════════════════════════════════════════════════
def load_progress() -> set:
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return set(data.get('done_urls', []))
        except:
            pass
    return set()

def save_progress(done_urls: set):
    try:
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump({'done_urls': list(done_urls), 'updated': time.time()}, f)
    except:
        pass

# ══════════════════════════════════════════════════════════════
#  ПАРСИНГ КАТАЛОГА
# ══════════════════════════════════════════════════════════════
def get_total_pages() -> int:
    resp = retry_request(CATALOG_URL)
    if not resp:
        return 1
    
    soup = BeautifulSoup(resp.content, 'html.parser')
    max_page = 1
    
    for link in soup.select('a[href*="page="], .pagination a'):
        text = link.get_text(strip=True)
        if text.isdigit():
            max_page = max(max_page, int(text))
    
    if max_page == 1:
        match = re.search(r'Страница\s+\d+\s+из\s+(\d+)', soup.get_text())
        if match:
            max_page = int(match.group(1))
    
    return max_page

def fetch_catalog_urls() -> list:
    print("=" * 60)
    print("📡  Сканирую каталог...")
    print("=" * 60)
    
    total_pages = get_total_pages()
    print(f"  📊 Всего страниц: {total_pages}")
    
    all_urls = []
    
    for page in range(1, total_pages + 1):
        url = f"{CATALOG_URL}?page={page}" if page > 1 else CATALOG_URL
        resp = retry_request(url)
        if not resp:
            continue
        
        soup = BeautifulSoup(resp.content, 'html.parser')
        page_urls = []
        
        for link in soup.find_all('a', href=True):
            href = link['href']
            if '/anime/' in href and '/catalog' not in href and href != '/anime/':
                full_url = urljoin(BASE_URL, href)
                if full_url not in page_urls:
                    page_urls.append(full_url)
        
        all_urls.extend(page_urls)
        print(f"  📄 Стр. {page}/{total_pages} | Найдено: {len(page_urls)} | Всего: {len(all_urls)}")
        time.sleep(REQUEST_DELAY)
    
    return list(set(all_urls))

# ══════════════════════════════════════════════════════════════
#  ПАРСИНГ РЕЛИЗА
# ══════════════════════════════════════════════════════════════
def parse_release_page(url: str) -> dict:
    resp = retry_request(url)
    if not resp:
        return None
    
    soup = BeautifulSoup(resp.content, 'html.parser')
    
    data = {
        'url': url,
        'title': '',
        'year': None,
        'poster_url': '',
        'episodes': []
    }
    
    title_elem = soup.select_one('h1, .title, .anime-title')
    if title_elem:
        data['title'] = title_elem.get_text(strip=True)
    
    year_match = re.search(r'(20\d{2})', soup.get_text())
    if year_match:
        data['year'] = int(year_match.group(1))
    
    poster = soup.select_one('img.poster, .poster img, img.anime-poster, img[src*="poster"]')
    if poster:
        data['poster_url'] = urljoin(BASE_URL, poster.get('src', ''))
    
    m3u8_urls = set()
    
    for link in soup.find_all('a', href=True):
        if '.m3u8' in link['href']:
            m3u8_urls.add(urljoin(BASE_URL, link['href']))
    
    scripts = soup.find_all('script')
    for script in scripts:
        if script.string and '.m3u8' in script.string:
            found = re.findall(r'https?://[^\s"\']+\.m3u8', script.string)
            m3u8_urls.update(found)
    
    sorted_urls = sorted(list(m3u8_urls))
    for idx, m3u8_url in enumerate(sorted_urls, 1):
        data['episodes'].append({
            'number': idx,
            'name': f"Серия {idx}",
            'url': m3u8_url
        })
    
    return data

# ══════════════════════════════════════════════════════════════
#  ОБРАБОТКА И СОХРАНЕНИЕ
# ══════════════════════════════════════════════════════════════
def process_and_save(url: str, done_urls: set) -> dict:
    if url in done_urls:
        with lock:
            stats['skipped'] += 1
        return {'skipped': True}
    
    data = parse_release_page(url)
    if not data or not data['episodes']:
        with lock:
            stats['processed'] += 1
        return None
    
    title = data['title'] or 'Unknown'
    
    folder_name = safe_filename(f"{title}_{data.get('year', '')}")
    folder_path = os.path.join(MIRRORS_DIR, folder_name)
    os.makedirs(folder_path, exist_ok=True)
    
    poster_filename = None
    if data['poster_url']:
        ext = '.jpg'
        if '.png' in data['poster_url'].lower(): ext = '.png'
        elif '.webp' in data['poster_url'].lower(): ext = '.webp'
        poster_filename = f"poster{ext}"
        try:
            resp = session.get(data['poster_url'], timeout=TIMEOUT)
            resp.raise_for_status()
            with open(os.path.join(folder_path, poster_filename), 'wb') as f:
                f.write(resp.content)
        except:
            poster_filename = None
    
    with open(os.path.join(folder_path, 'playlist.m3u'), 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        f.write(f"#PLAYLIST:{title}\n\n")
        for ep in data['episodes']:
            f.write(f"#EXTINF:-1 tvg-logo=\"{data['poster_url']}\",{title} - {ep['name']}\n")
            f.write(f"{ep['url']}\n")
    
    metadata = {
        'title': title,
        'year': data.get('year'),
        'poster': poster_filename,
        'poster_url': data['poster_url'],
        'episodes_count': len(data['episodes']),
        'url': url
    }
    with open(os.path.join(folder_path, 'info.json'), 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    
    with lock:
        stats['processed'] += 1
        stats['saved'] += 1
    
    return {'url': url, 'title': title, 'episodes': len(data['episodes']), 'skipped': False}

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
        f.write("#EXTM3U\n")
        f.write("#PLAYLIST:AniLiberty — Все релизы\n\n")
        
        for folder_name in folders:
            m3u_path = os.path.join(MIRRORS_DIR, folder_name, 'playlist.m3u')
            info_path = os.path.join(MIRRORS_DIR, folder_name, 'info.json')
            
            if not os.path.exists(m3u_path) or not os.path.exists(info_path):
                continue
            
            try:
                with open(info_path, 'r', encoding='utf-8') as jf:
                    info = json.load(jf)
                
                group = f"{info.get('year', '?')} | {info.get('title', 'Unknown')}"
                logo = info.get('poster_url', '')
                title = info.get('title', 'Unknown')
                
                with open(m3u_path, 'r', encoding='utf-8') as mf:
                    lines = mf.readlines()
                
                for line in lines:
                    if line.startswith('#EXTINF:'):
                        title_part = line.split(',', 1)[1].strip()
                        f.write(f"#EXTINF:-1 tvg-name=\"{title}\" tvg-logo=\"{logo}\" group-title=\"{group}\",{title_part}\n")
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
    print("║" + "  AniLiberty HTML Parser".center(58) + "║")
    print("║" + f"  Потоков: {MAX_WORKERS} | Таймаут: {TIMEOUT}с".center(58) + "║")
    print("╚" + "═" * 58 + "╝")
    print()
    
    os.makedirs(MIRRORS_DIR, exist_ok=True)
    
    done_urls = load_progress()
    if done_urls:
        print(f"🔄  Найдено сохранение прогресса: {len(done_urls)} уже обработано")
    
    catalog_urls = fetch_catalog_urls()
    if not catalog_urls:
        print("❌ Не удалось получить каталог")
        return
    
    stats['total'] = len(catalog_urls)
    
    print(f"\n{'=' * 60}")
    print(f"🚀  Обрабатываю {len(catalog_urls)} релизов в {MAX_WORKERS} потоков...")
    print(f"{'=' * 60}")
    
    last_print = time.time()
    last_save = time.time()
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_and_save, url, done_urls): url for url in catalog_urls}
        
        for future in as_completed(futures):
            try:
                result = future.result()
                
                if result and not result.get('skipped') and result.get('url'):
                    done_urls.add(result['url'])
                    
                    now = time.time()
                    if now - last_save >= 10:
                        save_progress(done_urls)
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
    
    save_progress(done_urls)
    
    total_eps = generate_global_m3u()
    
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
