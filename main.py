#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AniLibria / AniLiberty Mirror Builder - GitHub Actions версия
Поддерживает оба сайта: .tv (старый) и .top (новый)
Извлекает все возможные ссылки на видео через парсинг JavaScript и HTML
Автоматически пушит изменения в mirrors и обновляет мастер-плейлист
"""

import os
import re
import json
import time
import requests
import sys
from pathlib import Path
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ===== КОНФИГУРАЦИЯ =====
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MIRRORS_ROOT = os.path.join(SCRIPT_DIR, "mirrors")
M3U_MASTER = os.path.join(SCRIPT_DIR, "anilibria_all.m3u")
TIMESTAMP_FILE = os.path.join(SCRIPT_DIR, "last_run.txt")

SITES = {
    'old': {
        'base': 'https://anilibria.tv',
        'api': 'https://api.anilibria.tv/v3',
        'name': 'AniLibria TV'
    },
    'new': {
        'base': 'https://anilibria.top',
        'api': 'https://api.anilibria.top/v1',
        'name': 'AniLiberty'
    }
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
    'Referer': 'https://anilibria.top/'
}

# ===== ЗАГРУЗКА СПИСКА =====
def load_anime_titles():
    titles_file = os.path.join(SCRIPT_DIR, "aliases.txt")
    if os.path.exists(titles_file):
        with open(titles_file, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip()]
    
    print("⚠️ aliases.txt не найден, использую встроенный список")
    return [
        "bleach",
        "naruto",
        "one-piece",
        "attack-on-titan",
        "demon-slayer"
    ]

def load_processed_titles():
    processed_file = os.path.join(SCRIPT_DIR, "processed.txt")
    if os.path.exists(processed_file):
        with open(processed_file, 'r', encoding='utf-8') as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_processed_titles(processed):
    processed_file = os.path.join(SCRIPT_DIR, "processed.txt")
    with open(processed_file, 'w', encoding='utf-8') as f:
        for title in sorted(processed):
            f.write(f"{title}\n")

# ===== ИЗВЛЕЧЕНИЕ ДАННЫХ =====
def extract_js_data(html_content):
    result = {}
    
    match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', html_content, re.DOTALL)
    if match:
        try:
            result['initial_state'] = json.loads(match.group(1))
        except:
            pass
    
    for var in ['__NUXT__', '__NEXT_DATA__', '__DATA__', 'appData']:
        pattern = r'window\.' + re.escape(var) + r'\s*=\s*({.*?});'
        match = re.search(pattern, html_content, re.DOTALL)
        if match:
            try:
                result[var.lower()] = json.loads(match.group(1))
            except:
                pass
    
    m3u8_links = re.findall(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html_content)
    if m3u8_links:
        result['m3u8_links'] = m3u8_links
    
    iframe_match = re.search(r'<iframe[^>]*src="([^"]*)"[^>]*>', html_content)
    if iframe_match:
        result['player_url'] = iframe_match.group(1)
    
    return result

def extract_video_links_from_data(data):
    links = {'1080': [], '720': [], '480': []}
    
    def _traverse(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, str) and value.startswith('http'):
                    if '.m3u8' in value or 'video' in key.lower() or 'stream' in key.lower() or 'hls' in key.lower():
                        quality = '480'
                        if '1080' in value or 'fhd' in value.lower():
                            quality = '1080'
                        elif '720' in value or 'hd' in value.lower():
                            quality = '720'
                        links[quality].append(value)
                elif isinstance(value, (dict, list)):
                    _traverse(value)
        elif isinstance(obj, list):
            for item in obj:
                _traverse(item)
    
    _traverse(data)
    return links

def get_page_and_extract_links(code, site_key):
    site = SITES[site_key]
    links = {'1080': [], '720': [], '480': []}
    
    try:
        url = f"{site['base']}/release/{code}.html"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return links
        html = resp.text
        
        js_data = extract_js_data(html)
        
        if 'm3u8_links' in js_data:
            for link in js_data['m3u8_links']:
                quality = '480'
                if '1080' in link:
                    quality = '1080'
                elif '720' in link:
                    quality = '720'
                links[quality].append(link)
        
        for key, data in js_data.items():
            if key.endswith('_state') or key in ['__nuxt__', '__next_data__', '__data__']:
                extracted = extract_video_links_from_data(data)
                for q in links:
                    links[q].extend(extracted[q])
        
        if 'player_url' in js_data:
            player_url = js_data['player_url']
            if not player_url.startswith('http'):
                player_url = urljoin(site['base'], player_url)
            try:
                player_resp = requests.get(player_url, headers=HEADERS, timeout=10)
                if player_resp.status_code == 200:
                    player_html = player_resp.text
                    m3u8 = re.findall(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', player_html)
                    for link in m3u8:
                        quality = '480'
                        if '1080' in link:
                            quality = '1080'
                        elif '720' in link:
                            quality = '720'
                        links[quality].append(link)
            except:
                pass
    except Exception as e:
        print(f"    ⚠️ Ошибка парсинга: {e}")
    
    for q in links:
        links[q] = list(dict.fromkeys(links[q]))
    
    return links

# ===== API ФУНКЦИИ =====
def search_new_api(title):
    try:
        url = f"{SITES['new']['api']}/search"
        params = {'q': title, 'limit': 1}
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data and isinstance(data, list) and len(data) > 0:
                return data[0]
    except:
        pass
    return None

def get_new_details(code):
    try:
        url = f"{SITES['new']['api']}/titles/{code}"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    return None

def search_old_api(title):
    try:
        url = f"{SITES['old']['api']}/title/search"
        params = {'search': title, 'limit': 1}
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data and len(data) > 0:
                return data[0]
    except:
        pass
    return None

def get_old_details(code):
    try:
        url = f"{SITES['old']['api']}/title"
        params = {'code': code}
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    return None

# ===== ОСНОВНЫЕ ФУНКЦИИ =====
def download_poster(poster_url, folder_path):
    if not poster_url:
        return None
    if poster_url.startswith('/'):
        poster_url = SITES['new']['base'] + poster_url
    try:
        resp = requests.get(poster_url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            ext = poster_url.split('.')[-1].split('?')[0]
            if ext.lower() not in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
                ext = 'jpg'
            poster_path = os.path.join(folder_path, f"poster.{ext}")
            with open(poster_path, 'wb') as f:
                f.write(resp.content)
            return poster_path
    except:
        pass
    return None

def clean_title(title):
    """Очищает название от лишних символов для M3U"""
    if not title:
        return "Unknown"
    title = re.sub(r'[^\w\s\-]', '', title)
    title = re.sub(r'\s+', ' ', title).strip()
    return title

def process_anime(title):
    print(f"🔄 Обработка: {title}")
    clean_title_input = title.strip()
    anime_data = None
    used_site = None
    code = None

    print(f"  🔍 Ищем на {SITES['new']['name']}...")
    anime_data = search_new_api(clean_title_input)
    if anime_data:
        used_site = 'new'
        code = anime_data.get('code') or anime_data.get('slug') or clean_title_input
        details = get_new_details(code)
        if details:
            anime_data.update(details)
        print(f"  ✅ Найдено на {SITES['new']['name']}")
    
    if not anime_data:
        print(f"  🔍 Ищем на {SITES['old']['name']}...")
        anime_data = search_old_api(clean_title_input)
        if anime_data:
            used_site = 'old'
            code = anime_data.get('code', clean_title_input)
            details = get_old_details(code)
            if details:
                anime_data.update(details)
            print(f"  ✅ Найдено на {SITES['old']['name']}")

    if not anime_data:
        print(f"  ❌ Не найдено ни на одном сайте")
        return None

    folder_name = code.replace('/', '_').replace('\\', '_')
    folder_path = os.path.join(MIRRORS_ROOT, folder_name)
    os.makedirs(folder_path, exist_ok=True)

    names = anime_data.get('names', {})
    title_ru = names.get('ru', code)
    title_en = names.get('en', code)
    title_alt = names.get('alternative', '')
    
    clean_title_ru = clean_title(title_ru)
    clean_title_en = clean_title(title_en)
    
    info = {
        'site': used_site,
        'code': anime_data.get('code', ''),
        'names': {
            'ru': title_ru,
            'en': title_en,
            'alternative': title_alt
        },
        'description': anime_data.get('description', ''),
        'description_short': anime_data.get('description_short', ''),
        'genres': anime_data.get('genres', []),
        'year': anime_data.get('year', ''),
        'season': anime_data.get('season', ''),
        'type': anime_data.get('type', ''),
        'status': anime_data.get('status', ''),
        'rating': anime_data.get('rating', ''),
        'episodes_total': anime_data.get('episodes_total', 0),
        'episodes_released': anime_data.get('episodes_released', 0),
        'updated_at': datetime.now().isoformat()
    }
    info_path = os.path.join(folder_path, "info.json")
    with open(info_path, 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    poster_url = anime_data.get('poster') or anime_data.get('image') or anime_data.get('cover')
    poster_path = None
    if poster_url:
        poster_path = download_poster(poster_url, folder_path)
        if poster_path:
            print(f"  ✅ Постер сохранён")

    video_links = extract_video_links_from_data(anime_data)
    if used_site:
        page_links = get_page_and_extract_links(code, used_site)
        for quality in video_links:
            video_links[quality].extend(page_links[quality])
            video_links[quality] = list(dict.fromkeys(video_links[quality]))

    if any(video_links.values()):
        links_path = os.path.join(folder_path, "links.txt")
        with open(links_path, 'w', encoding='utf-8') as f:
            f.write("# Найденные ссылки на видео (m3u8)\n")
            f.write("# Формат: качество: URL\n\n")
            for quality in ['1080', '720', '480']:
                if video_links[quality]:
                    for link in video_links[quality]:
                        f.write(f"{quality}p: {link}\n")

    if any(video_links.values()):
        m3u_path = os.path.join(folder_path, f"{code}.m3u")
        with open(m3u_path, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            f.write(f'#PLAYLIST:{clean_title_ru}\n')
            f.write(f'# Группа: {clean_title_ru}\n')
            f.write(f'# Английское название: {clean_title_en}\n')
            if title_alt:
                f.write(f'# Альтернативное название: {title_alt}\n')
            f.write(f'# Год: {info.get("year", "Неизвестно")}\n')
            f.write(f'# Тип: {info.get("type", "Неизвестно")}\n')
            f.write(f'# Статус: {info.get("status", "Неизвестно")}\n')
            f.write(f'# Серий всего: {info.get("episodes_total", 0)}\n')
            if info.get('description_short'):
                f.write(f'# Описание: {info.get("description_short", "")[:200]}\n')
            f.write('#\n')
            
            ep_num = 1
            for quality in ['1080', '720', '480']:
                for link in video_links[quality]:
                    name = f"{clean_title_ru} - {quality}p"
                    poster_rel = "poster.jpg" if poster_path and os.path.exists(poster_path) else ""
                    if poster_rel:
                        f.write(f'#EXTINF:-1 tvg-id="{code}_{ep_num}" tvg-name="{name}" tvg-logo="{poster_rel}" group-title="{clean_title_ru}",{name}\n')
                    else:
                        f.write(f'#EXTINF:-1 tvg-id="{code}_{ep_num}" tvg-name="{name}" group-title="{clean_title_ru}",{name}\n')
                    f.write(link + '\n')
                    ep_num += 1
        print(f"  ✅ M3U создан")

    print(f"  ✅ Готово: {folder_name}")
    return {
        'code': code,
        'title_ru': clean_title_ru,
        'title_en': clean_title_en,
        'folder': folder_name,
        'poster': poster_path,
        'links': video_links
    }

def create_master_m3u(results):
    lines = ["#EXTM3U"]
    lines.append("# Playlist: AniLibria Mirror")
    lines.append(f"# Updated: {datetime.now().isoformat()}")
    lines.append(f"# Total titles: {len(results)}")
    lines.append("#")
    
    sorted_results = sorted(results, key=lambda x: x.get('title_ru', '').lower())
    
    for anime in sorted_results:
        if not anime:
            continue
        
        title_ru = anime.get('title_ru', 'Unknown')
        title_en = anime.get('title_en', '')
        folder = anime.get('folder', '')
        
        lines.append(f'#=== {title_ru} ===#')
        if title_en:
            lines.append(f'# Англ: {title_en}')
        
        poster_rel = ""
        if folder:
            for ext in ['jpg', 'png', 'webp', 'jpeg', 'gif']:
                poster_path = os.path.join(MIRRORS_ROOT, folder, f"poster.{ext}")
                if os.path.exists(poster_path):
                    poster_rel = f"mirrors/{folder}/poster.{ext}"
                    break
        
        for quality in ['1080', '720', '480']:
            for link in anime['links'].get(quality, []):
                name = f"{title_ru} - {quality}p"
                if poster_rel:
                    lines.append(f'#EXTINF:-1 tvg-id="{anime["code"]}" tvg-name="{name}" tvg-logo="{poster_rel}" group-title="{title_ru}",{name}')
                else:
                    lines.append(f'#EXTINF:-1 tvg-id="{anime["code"]}" tvg-name="{name}" group-title="{title_ru}",{name}')
                lines.append(link)
        
        lines.append("#")
    
    os.makedirs(os.path.dirname(M3U_MASTER), exist_ok=True)
    with open(M3U_MASTER, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    
    print(f"✅ Мастер M3U создан: {M3U_MASTER}")
    return len(sorted_results)

def main():
    print("🚀 AniLibria/AniLiberty Mirror Builder (GitHub Actions)")
    print("=" * 50)
    print(f"⏰ Запуск: {datetime.now().isoformat()}")
    
    os.makedirs(MIRRORS_ROOT, exist_ok=True)
    os.makedirs(os.path.dirname(M3U_MASTER), exist_ok=True)

    anime_titles = load_anime_titles()
    if not anime_titles:
        print("❌ Нет названий для обработки")
        sys.exit(1)
    
    print(f"📊 Загружено названий: {len(anime_titles)}")
    
    processed = load_processed_titles()
    new_titles = [t for t in anime_titles if t not in processed]
    
    if not new_titles:
        print("✅ Все названия уже обработаны")
        sys.exit(0)
    
    print(f"🆕 Новых названий для обработки: {len(new_titles)}")

    results = []
    total = len(new_titles)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(process_anime, title): title for title in new_titles}
        for i, future in enumerate(as_completed(futures), 1):
            title = futures[future]
            try:
                result = future.result()
                if result:
                    results.append(result)
                    processed.add(title)
                print(f"📊 Прогресс: {i}/{total} ({i/total*100:.1f}%)")
            except Exception as e:
                print(f"  ❌ Ошибка обработки {title}: {e}")
            time.sleep(0.5)

    save_processed_titles(processed)
    
    all_results = []
    for folder in os.listdir(MIRRORS_ROOT):
        folder_path = os.path.join(MIRRORS_ROOT, folder)
        if os.path.isdir(folder_path):
            info_path = os.path.join(folder_path, "info.json")
            links_path = os.path.join(folder_path, "links.txt")
            if os.path.exists(info_path) and os.path.exists(links_path):
                with open(info_path, 'r', encoding='utf-8') as f:
                    info = json.load(f)
                links = {'1080': [], '720': [], '480': []}
                with open(links_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if 'p:' in line:
                            parts = line.strip().split('p: ')
                            if len(parts) == 2:
                                quality = parts[0]
                                url = parts[1]
                                if quality in links:
                                    links[quality].append(url)
                all_results.append({
                    'code': info.get('code', folder),
                    'title_ru': info.get('names', {}).get('ru', folder),
                    'title_en': info.get('names', {}).get('en', folder),
                    'folder': folder,
                    'links': links
                })
    
    count = create_master_m3u(all_results)
    
    with open(TIMESTAMP_FILE, 'w', encoding='utf-8') as f:
        f.write(datetime.now().isoformat())
    
    print("\n✅ Готово!")
    print(f"📁 Папка: {MIRRORS_ROOT}")
    print(f"📊 Всего аниме в зеркале: {len(all_results)}")
    print(f"📄 Мастер плейлист: {M3U_MASTER}")
    print(f"🆕 Обработано новых: {len(results)}")

if __name__ == "__main__":
    main()
