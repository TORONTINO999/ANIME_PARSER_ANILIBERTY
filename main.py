#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AniLibria / AniLiberty Mirror Builder – с полным рендерингом через браузер.
Парсит динамические страницы как реальный пользователь, извлекает всё:
- русское и английское название
- описание
- постер
- жанры
- ссылки на все серии (m3u8) с указанием качества
Создаёт структуру папок и M3U-плейлисты.
"""

import os
import re
import json
import time
import sys
from datetime import datetime
from urllib.parse import urljoin

# Импортируем Playwright (обязательно)
from playwright.sync_api import sync_playwright

# ===== КОНФИГУРАЦИЯ =====
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MIRRORS_ROOT = os.path.join(SCRIPT_DIR, "mirrors")
M3U_MASTER = os.path.join(SCRIPT_DIR, "anilibria_all.m3u")
PROCESSED_FILE = os.path.join(SCRIPT_DIR, "processed.txt")
TIMESTAMP_FILE = os.path.join(SCRIPT_DIR, "last_run.txt")

SITES = [
    {'base': 'https://anilibria.top', 'name': 'AniLiberty'},
    {'base': 'https://anilibria.tv', 'name': 'AniLibria TV'}
]

# ===== ЗАГРУЗКА СПИСКА АНИМЕ =====
def load_anime_titles():
    titles_file = os.path.join(SCRIPT_DIR, "aliases.txt")
    if os.path.exists(titles_file):
        with open(titles_file, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip()]
    print("⚠️ aliases.txt не найден, использую встроенный список")
    return ["bleach", "naruto", "one-piece"]

# ===== РАБОТА С ПРОЦЕССОМ =====
def load_processed():
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, 'r', encoding='utf-8') as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_processed(processed):
    with open(PROCESSED_FILE, 'w', encoding='utf-8') as f:
        for title in sorted(processed):
            f.write(f"{title}\n")

# ===== ИЗВЛЕЧЕНИЕ ДАННЫХ ИЗ СТРАНИЦЫ =====
def fetch_page_content(url):
    """Загружает страницу через браузер (Playwright) и возвращает HTML."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            content = page.content()
            browser.close()
            return content
    except Exception as e:
        print(f"    ⚠️ Ошибка загрузки {url}: {e}")
        return None

def parse_page(html, base_url):
    """Парсит HTML и извлекает все нужные данные."""
    result = {
        'title_ru': None,
        'title_en': None,
        'description': None,
        'poster_url': None,
        'genres': [],
        'video_links': {'1080': [], '720': [], '480': []}
    }

    # 1. Заголовок (мета-тег title)
    title_match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
    if title_match:
        full_title = title_match.group(1).strip()
        if '|' in full_title:
            result['title_ru'] = full_title.split('|')[0].strip()
        else:
            result['title_ru'] = full_title

    # 2. Описание (meta name="description")
    desc_match = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html, re.IGNORECASE)
    if desc_match:
        result['description'] = desc_match.group(1).strip()

    # 3. Постер (og:image)
    poster_match = re.search(r'<meta\s+property="og:image"\s+content="([^"]*)"', html, re.IGNORECASE)
    if poster_match:
        poster_url = poster_match.group(1).strip()
        if not poster_url.startswith('http'):
            poster_url = urljoin(base_url, poster_url)
        result['poster_url'] = poster_url

    # 4. Жанры (ищем в JSON-данных или в тексте)
    # Пробуем найти в __INITIAL_STATE__
    state_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', html, re.DOTALL)
    if state_match:
        try:
            state = json.loads(state_match.group(1))
            # Ищем жанры
            if 'title' in state and 'genres' in state['title']:
                result['genres'] = [g.get('name', '') for g in state['title']['genres']]
            # Ищем видео-ссылки
            def find_links(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if isinstance(v, str) and v.startswith('http') and '.m3u8' in v:
                            quality = '480'
                            if '1080' in v or 'fhd' in v.lower():
                                quality = '1080'
                            elif '720' in v or 'hd' in v.lower():
                                quality = '720'
                            result['video_links'][quality].append(v)
                        else:
                            find_links(v)
                elif isinstance(obj, list):
                    for item in obj:
                        find_links(item)
            find_links(state)
        except:
            pass

    # 5. Если ссылок всё ещё нет, ищем m3u8 напрямую в HTML
    if not any(result['video_links'].values()):
        m3u8_urls = re.findall(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
        for url in m3u8_urls:
            quality = '480'
            if '1080' in url:
                quality = '1080'
            elif '720' in url:
                quality = '720'
            result['video_links'][quality].append(url)

    # 6. Удаляем дубликаты
    for q in result['video_links']:
        result['video_links'][q] = list(dict.fromkeys(result['video_links'][q]))

    # Если название не найдено, пробуем взять из og:title
    if not result['title_ru']:
        og_title = re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', html, re.IGNORECASE)
        if og_title:
            result['title_ru'] = og_title.group(1).strip()

    return result

# ===== ОСНОВНАЯ ФУНКЦИЯ ОБРАБОТКИ =====
def process_anime(title):
    print(f"🔄 Обработка: {title}")
    clean_title = title.strip()
    html_content = None
    used_site = None
    base_url = None

    # Пробуем оба сайта
    for site in SITES:
        url = f"{site['base']}/release/{clean_title}.html"
        print(f"  🔍 Загружаю {url} ...")
        html = fetch_page_content(url)
        if html:
            html_content = html
            used_site = site['name']
            base_url = site['base']
            print(f"  ✅ Найдено на {used_site}")
            break
        else:
            print(f"  ⚠️ Не загрузилось с {site['name']}")

    if not html_content:
        print(f"  ❌ Не найдено ни на одном сайте")
        return None

    # Парсим
    data = parse_page(html_content, base_url)

    # Если нет видео-ссылок – пробуем найти iframe плеера
    if not any(data['video_links'].values()):
        iframe_match = re.search(r'<iframe[^>]*src="([^"]*)"[^>]*>', html_content)
        if iframe_match:
            iframe_url = iframe_match.group(1)
            if not iframe_url.startswith('http'):
                iframe_url = urljoin(base_url, iframe_url)
            print(f"  🔍 Загружаю плеер: {iframe_url}")
            iframe_html = fetch_page_content(iframe_url)
            if iframe_html:
                iframe_data = parse_page(iframe_html, base_url)
                for q in data['video_links']:
                    data['video_links'][q].extend(iframe_data['video_links'][q])
                    data['video_links'][q] = list(dict.fromkeys(data['video_links'][q]))

    if not any(data['video_links'].values()):
        print(f"  ⚠️ Видео-ссылки не найдены для {clean_title}")
        return None

    # Создаём папку
    folder_name = clean_title.replace('/', '_').replace('\\', '_')
    folder_path = os.path.join(MIRRORS_ROOT, folder_name)
    os.makedirs(folder_path, exist_ok=True)

    # Сохраняем info.json
    title_ru = data['title_ru'] or clean_title
    info = {
        'code': clean_title,
        'names': {'ru': title_ru, 'en': ''},
        'description': data['description'] or '',
        'genres': data['genres'],
        'year': '',
        'type': '',
        'status': '',
        'site': used_site,
        'updated_at': datetime.now().isoformat()
    }
    with open(os.path.join(folder_path, 'info.json'), 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    # Скачиваем постер
    poster_path = None
    if data['poster_url']:
        try:
            import requests
            resp = requests.get(data['poster_url'], timeout=10)
            if resp.status_code == 200:
                ext = data['poster_url'].split('.')[-1].split('?')[0]
                if ext.lower() not in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
                    ext = 'jpg'
                poster_path = os.path.join(folder_path, f"poster.{ext}")
                with open(poster_path, 'wb') as f:
                    f.write(resp.content)
                print(f"  ✅ Постер сохранён")
        except:
            pass

    # Сохраняем links.txt
    with open(os.path.join(folder_path, 'links.txt'), 'w', encoding='utf-8') as f:
        f.write("# Найденные видео-ссылки (m3u8)\n")
        for quality in ['1080', '720', '480']:
            if data['video_links'][quality]:
                for link in data['video_links'][quality]:
                    f.write(f"{quality}p: {link}\n")

    # Создаём M3U для этого аниме
    m3u_path = os.path.join(folder_path, f"{clean_title}.m3u")
    with open(m3u_path, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        f.write(f'#PLAYLIST:{title_ru}\n')
        ep_num = 1
        for quality in ['1080', '720', '480']:
            for link in data['video_links'][quality]:
                name = f"{title_ru} - {quality}p"
                poster_rel = os.path.basename(poster_path) if poster_path else ""
                if poster_rel:
                    f.write(f'#EXTINF:-1 tvg-id="{clean_title}_{ep_num}" tvg-name="{name}" tvg-logo="{poster_rel}" group-title="{title_ru}",{name}\n')
                else:
                    f.write(f'#EXTINF:-1 tvg-id="{clean_title}_{ep_num}" tvg-name="{name}" group-title="{title_ru}",{name}\n')
                f.write(link + '\n')
                ep_num += 1

    print(f"  ✅ Готово: {folder_name} (найдено ссылок: {sum(len(data['video_links'][q]) for q in data['video_links'])})")
    return {
        'code': clean_title,
        'title_ru': title_ru,
        'folder': folder_name,
        'poster': poster_path,
        'links': data['video_links']
    }

# ===== СОЗДАНИЕ МАСТЕР-ПЛЕЙЛИСТА =====
def create_master_m3u(results):
    lines = ["#EXTM3U"]
    lines.append(f"# Playlist: AniLibria Mirror (updated {datetime.now().isoformat()})")
    lines.append(f"# Total titles: {len(results)}")
    lines.append("#")
    for anime in sorted(results, key=lambda x: x.get('title_ru', '').lower()):
        if not anime:
            continue
        title = anime['title_ru']
        code = anime['code']
        folder = anime['folder']
        # Определяем постер
        poster_rel = ""
        if folder:
            for ext in ['jpg', 'png', 'webp', 'jpeg', 'gif']:
                test_path = os.path.join(MIRRORS_ROOT, folder, f"poster.{ext}")
                if os.path.exists(test_path):
                    poster_rel = f"mirrors/{folder}/poster.{ext}"
                    break
        lines.append(f'#=== {title} ===#')
        for quality in ['1080', '720', '480']:
            for link in anime['links'].get(quality, []):
                name = f"{title} - {quality}p"
                if poster_rel:
                    lines.append(f'#EXTINF:-1 tvg-id="{code}" tvg-name="{name}" tvg-logo="{poster_rel}" group-title="{title}",{name}')
                else:
                    lines.append(f'#EXTINF:-1 tvg-id="{code}" tvg-name="{name}" group-title="{title}",{name}')
                lines.append(link)
        lines.append("#")
    with open(M3U_MASTER, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f"✅ Мастер M3U создан: {M3U_MASTER}")

# ===== ГЛАВНАЯ =====
def main():
    print("🚀 AniLibria/AniLiberty Mirror Builder (полный рендеринг через браузер)")
    print("=" * 60)
    os.makedirs(MIRRORS_ROOT, exist_ok=True)

    anime_titles = load_anime_titles()
    if not anime_titles:
        print("❌ Нет названий для обработки")
        sys.exit(1)
    print(f"📊 Загружено названий: {len(anime_titles)}")

    processed = load_processed()
    new_titles = [t for t in anime_titles if t not in processed]
    if not new_titles:
        print("✅ Все названия уже обработаны")
        sys.exit(0)
    print(f"🆕 Новых названий: {len(new_titles)}")

    results = []
    total = len(new_titles)

    # Можно запускать последовательно или параллельно (но Playwright не очень дружит с threading)
    # Оставим последовательно, чтобы не было конфликтов с браузером
    for i, title in enumerate(new_titles, 1):
        result = process_anime(title)
        if result:
            results.append(result)
            processed.add(title)
        print(f"📊 Прогресс: {i}/{total} ({i/total*100:.1f}%)")

    save_processed(processed)

    # Собираем все результаты из папки mirrors для мастер-плейлиста
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
                    'folder': folder,
                    'links': links
                })

    create_master_m3u(all_results)

    with open(TIMESTAMP_FILE, 'w', encoding='utf-8') as f:
        f.write(datetime.now().isoformat())

    print("\n✅ Готово!")
    print(f"📁 Папка: {MIRRORS_ROOT}")
    print(f"📊 Всего аниме в зеркале: {len(all_results)}")
    print(f"📄 Мастер плейлист: {M3U_MASTER}")

if __name__ == "__main__":
    main()
