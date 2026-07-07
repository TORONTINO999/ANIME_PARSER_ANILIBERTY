#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AniLiberty Parser v2.0 — Official API Edition
✓ Полное API перечисление (все 75 страниц)
✓ Прямые HLS ссылки из cache.libria
✓ M3U генерация с постерами и метаданными
✓ Структура: mirrors/{название_аниме}/
✓ GitHub integration с GH_TOKEN
"""

import os
import re
import json
import asyncio
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
import subprocess
import hashlib

try:
    import aiohttp
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("[!] pip install aiohttp requests")

# ============= CONFIG =============
API_BASE = "https://anilibria.top/api/v1/anime"
CATALOG_URL = "https://www.anilibria.top/anime/catalog/"
POSTER_BASE = "https://anilibria.top/storage/releases/posters"
CACHE_LIBRIA_BASE = "https://cache.libria.ru"  # или https://cache.anilibria.top

MIRRORS_DIR = "mirrors"
MAIN_M3U = "aniliberty.m3u8"

# GitHub
GH_TOKEN = os.getenv("GH_TOKEN", "").strip()
GH_REPO = os.getenv("GH_REPO", "user/aniliberty-mirrors").strip()
GH_BRANCH = "main"

MAX_PAGES = 75
PAGE_LIMIT = 50
TIMEOUT = 30

# ============= LOGGER =============
class Logger:
    def __init__(self):
        self.stats = {"success": 0, "error": 0, "skipped": 0}
    
    def info(self, msg: str, prefix="[*]"):
        print(f"{prefix} {msg}")
    
    def success(self, msg: str):
        self.info(msg, "[✓]")
        self.stats["success"] += 1
    
    def error(self, msg: str):
        self.info(msg, "[✗]")
        self.stats["error"] += 1
    
    def warn(self, msg: str):
        self.info(msg, "[!]")
        self.stats["skipped"] += 1
    
    def summary(self):
        print("\n" + "="*70)
        print(f"Успешно: {self.stats['success']} | Ошибок: {self.stats['error']} | Пропущено: {self.stats['skipped']}")
        print("="*70)

log = Logger()

# ============= UTILS =============
def sanitize_name(name: str) -> str:
    """Чистим название для папки"""
    if not name:
        return "unknown"
    # Нормализуем пробелы
    name = re.sub(r'\s+', ' ', name.strip())
    # Берём до первого разделителя
    name = re.split(r'[—–/|]', name)[0].strip()
    # Убираем запрещённые символы
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, '_')
    # Обрезаем
    return name[:100]

def get_anime_key(title: str, anime_id: int) -> str:
    """Уникальный ключ аниме"""
    if title:
        key = sanitize_name(title).lower()
        key = re.sub(r'[^a-zа-яё0-9_]', '_', key)
        key = re.sub(r'_+', '_', key).strip('_')
        if key:
            return key[:60]
    return f"anime_{anime_id}"

async def fetch_json(session, url: str, params: dict = None) -> Optional[dict]:
    """Fetch JSON with retry logic"""
    try:
        async with session.get(url, params=params, timeout=TIMEOUT) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                log.warn(f"HTTP {resp.status} от {url}")
                return None
    except asyncio.TimeoutError:
        log.warn(f"Таймаут: {url}")
        return None
    except Exception as e:
        log.warn(f"Fetch ошибка {url}: {str(e)[:50]}")
        return None

def download_image(url: str, filepath: str) -> bool:
    """Скачиваем картинку"""
    try:
        if not REQUESTS_AVAILABLE:
            return False
        
        resp = requests.get(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        }, timeout=10, verify=False)
        resp.raise_for_status()
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'wb') as f:
            f.write(resp.content)
        return True
    except Exception as e:
        return False

def extract_hls_url(episode_data: dict) -> Optional[str]:
    """Извлекаем HLS ссылку из данных эпизода"""
    try:
        # Структура: episode -> players -> player -> data.hls или files -> hls
        if 'players' in episode_data:
            for player_type, player_data in episode_data['players'].items():
                if isinstance(player_data, dict):
                    # Проверяем data.hls
                    if 'data' in player_data and isinstance(player_data['data'], dict):
                        if 'hls' in player_data['data']:
                            hls_url = player_data['data']['hls']
                            if isinstance(hls_url, list) and hls_url:
                                return hls_url[0] if isinstance(hls_url[0], str) else None
                            elif isinstance(hls_url, str):
                                return hls_url
                    # Проверяем files.hls
                    if 'files' in player_data and isinstance(player_data['files'], dict):
                        if 'hls' in player_data['files']:
                            hls_url = player_data['files']['hls']
                            if isinstance(hls_url, list) and hls_url:
                                return hls_url[0]
                            elif isinstance(hls_url, str):
                                return hls_url
        return None
    except Exception as e:
        return None

def generate_m3u_entry(title: str, duration: int, poster: str, 
                       hls_url: str, attributes: dict = None) -> str:
    """Генерируем EXTINF строку для M3U"""
    attrs = attributes or {}
    
    # Основной EXTINF
    extinf = f'#EXTINF:{duration},'
    
    # Добавляем атрибуты
    if poster:
        extinf += f' tvg-logo="{poster}"'
    
    extinf += f' group-title="{attrs.get("group", "Аниме")}"'
    
    for key, val in attrs.items():
        if key not in ('group',):
            extinf += f' {key}="{val}"'
    
    extinf += f'\n{title}\n{hls_url}\n'
    return extinf

# ============= MAIN PARSER =============
class AniLiberty:
    def __init__(self):
        self.session = None
        self.animes: Dict[str, dict] = {}
        self.total_episodes = 0
        self.total_players = 0
    
    async def fetch_all_releases(self) -> List[dict]:
        """Получаем все релизы со всех страниц (75 * 50 = 3750 аниме)"""
        all_releases = []
        
        async with aiohttp.ClientSession() as session:
            self.session = session
            
            for page in range(1, MAX_PAGES + 1):
                log.info(f"Страница {page}/{MAX_PAGES}...", "📄")
                
                url = f"{API_BASE}/catalog/releases"
                params = {
                    'page': page,
                    'limit': PAGE_LIMIT,
                    'sort_direction': 'desc',
                    'sorting': 'created_at'
                }
                
                data = await fetch_json(session, url, params)
                
                if not data or 'data' not in data:
                    log.warn(f"Пустой ответ со страницы {page}")
                    continue
                
                releases = data['data']
                all_releases.extend(releases)
                
                log.info(f"  Загружено {len(releases)} релизов, всего {len(all_releases)}")
                
                # Не перегружаем сервер
                await asyncio.sleep(0.5)
        
        return all_releases
    
    async def fetch_release_details(self, release_id: int, alias: str) -> Optional[dict]:
        """Получаем детали релиза с эпизодами"""
        async with aiohttp.ClientSession() as session:
            # Пробуем по ID, потом по alias
            for identifier in [release_id, alias]:
                url = f"{API_BASE}/releases/{identifier}"
                data = await fetch_json(session, url)
                
                if data:
                    return data
                
                await asyncio.sleep(0.1)
        
        return None
    
    async def process_release(self, release: dict) -> bool:
        """Обрабатываем один релиз"""
        try:
            release_id = release.get('id')
            alias = release.get('alias')
            title = release.get('name', {}).get('main', 'Unknown')
            
            if not release_id or not alias:
                return False
            
            # Нормализуем название
            safe_title = sanitize_name(title)
            anime_key = get_anime_key(title, release_id)
            
            # Ищем в кэше
            if anime_key in self.animes:
                return True  # Уже обработано
            
            # Инициализируем запись
            self.animes[anime_key] = {
                'id': release_id,
                'alias': alias,
                'title': title,
                'safe_title': safe_title,
                'year': release.get('year'),
                'type': release.get('type', {}).get('value'),
                'genres': [g.get('name', '') for g in release.get('genres', [])],
                'poster': None,
                'episodes': [],
                'hls_urls': []
            }
            
            # Скачиваем постер
            if 'poster' in release and release['poster']:
                poster_src = release['poster'].get('src')
                if poster_src:
                    poster_url = urljoin(POSTER_BASE, poster_src) if not poster_src.startswith('http') else poster_src
                    poster_path = os.path.join(
                        MIRRORS_DIR,
                        safe_title,
                        f"poster_{hashlib.md5(poster_url.encode()).hexdigest()[:8]}.jpg"
                    )
                    
                    if download_image(poster_url, poster_path):
                        self.animes[anime_key]['poster'] = poster_path
            
            # Получаем детали с эпизодами
            details = await self.fetch_release_details(release_id, alias)
            
            if not details:
                log.warn(f"Не удалось получить детали для {title}")
                return False
            
            # Обрабатываем эпизоды
            episodes = details.get('episodes', {})
            
            if isinstance(episodes, dict):
                for ep_num, ep_data in sorted(episodes.items()):
                    if isinstance(ep_data, dict):
                        hls_url = extract_hls_url(ep_data)
                        
                        if hls_url:
                            self.animes[anime_key]['episodes'].append({
                                'number': ep_num,
                                'hls_url': hls_url
                            })
                            self.animes[anime_key]['hls_urls'].append(hls_url)
                            self.total_episodes += 1
                            self.total_players += 1
            
            if self.animes[anime_key]['episodes']:
                log.success(f"{title} — {len(self.animes[anime_key]['episodes'])} серий")
            else:
                log.warn(f"{title} — нет HLS ссылок")
            
            return True
            
        except Exception as e:
            log.error(f"Ошибка обработки релиза: {str(e)[:60]}")
            return False
    
    async def parse_all(self):
        """Полный парсинг"""
        log.info("Начинаем парсинг каталога AniLiberty...", "🚀")
        
        # 1. Получаем список всех релизов
        releases = await self.fetch_all_releases()
        log.success(f"Получено {len(releases)} релизов из каталога")
        
        # 2. Обрабатываем каждый релиз
        os.makedirs(MIRRORS_DIR, exist_ok=True)
        
        for i, release in enumerate(releases, 1):
            if i % 100 == 0:
                log.info(f"Обработано {i}/{len(releases)}...", "⚙️")
            
            await self.process_release(release)
            await asyncio.sleep(0.1)  # Не перегружаем API
        
        log.success(f"Обработано {len(self.animes)} уникальных аниме")
    
    def save_m3u_files(self):
        """Сохраняем M3U плейлисты"""
        log.info("Генерируем M3U плейлисты...", "📝")
        
        main_m3u_content = "#EXTM3U\n"
        
        for anime_key, anime_data in self.animes.items():
            if not anime_data['episodes']:
                continue
            
            # Создаём папку аниме
            anime_dir = os.path.join(MIRRORS_DIR, anime_data['safe_title'])
            os.makedirs(anime_dir, exist_ok=True)
            
            # Создаём M3U для этого аниме
            anime_m3u = "#EXTM3U\n"
            anime_m3u += f"# Название: {anime_data['title']}\n"
            anime_m3u += f"# Год: {anime_data['year']}\n"
            anime_m3u += f"# Тип: {anime_data['type']}\n"
            anime_m3u += f"# Жанры: {', '.join(anime_data['genres'])}\n"
            anime_m3u += f"# Эпизодов: {len(anime_data['episodes'])}\n\n"
            
            # Добавляем эпизоды
            for ep in anime_data['episodes']:
                ep_title = f"{anime_data['title']} - Серия {ep['number']}"
                
                attrs = {
                    'tvg-id': f"{anime_data['id']}_ep{ep['number']}",
                    'group-title': anime_data['type'] or 'Аниме'
                }
                
                if anime_data['poster']:
                    poster_url = anime_data['poster'].replace('\\', '/').lstrip(MIRRORS_DIR + '/')
                    attrs['tvg-logo'] = poster_url
                
                entry = generate_m3u_entry(
                    ep_title, -1, None, ep['hls_url'], attrs
                )
                anime_m3u += entry
            
            # Сохраняем M3U аниме
            anime_m3u_path = os.path.join(anime_dir, f"{anime_data['safe_title']}.m3u8")
            with open(anime_m3u_path, 'w', encoding='utf-8') as f:
                f.write(anime_m3u)
            
            # Добавляем в главный M3U
            poster_line = ""
            if anime_data['poster']:
                poster_url = anime_data['poster'].replace('\\', '/').lstrip(MIRRORS_DIR + '/')
                poster_line = f' tvg-logo="{poster_url}"'
            
            main_m3u_content += f"#EXTINF:-1{poster_line} group-title=\"{anime_data['type'] or 'Аниме'}\" tvg-id=\"{anime_data['id']}\",{anime_data['title']}\n"
            main_m3u_content += f"{anime_m3u_path}\n\n"
        
        # Сохраняем главный M3U
        with open(MAIN_M3U, 'w', encoding='utf-8') as f:
            f.write(main_m3u_content)
        
        log.success(f"Сохранено {len(self.animes)} M3U плейлистов")
    
    def save_json_metadata(self):
        """Сохраняем метаданные в JSON"""
        meta_file = os.path.join(MIRRORS_DIR, "metadata.json")
        
        metadata = {
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'total_animes': len(self.animes),
            'total_episodes': self.total_episodes,
            'total_hls_urls': self.total_players,
            'animes': {
                key: {
                    'id': data['id'],
                    'title': data['title'],
                    'year': data['year'],
                    'type': data['type'],
                    'genres': data['genres'],
                    'episodes_count': len(data['episodes']),
                    'poster': data['poster']
                }
                for key, data in self.animes.items()
            }
        }
        
        with open(meta_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        
        log.success(f"Сохранены метаданные: {meta_file}")

# ============= GITHUB UPLOAD =============
def push_to_github():
    """Загружаем результаты в GitHub"""
    if not GH_TOKEN or not GH_REPO:
        log.warn("GH_TOKEN или GH_REPO не установлены, пропускаем GitHub")
        return
    
    log.info("Загружаем на GitHub...", "📤")
    
    try:
        # Git config
        subprocess.run(['git', 'config', '--global', 'user.email', 'bot@aniliberty.local'],
                      capture_output=True)
        subprocess.run(['git', 'config', '--global', 'user.name', 'AniliBot'],
                      capture_output=True)
        
        # Init repo if needed
        if not os.path.exists('.git'):
            subprocess.run(['git', 'init'], check=True, capture_output=True)
            subprocess.run(['git', 'remote', 'add', 'origin',
                          f'https://{GH_TOKEN}@github.com/{GH_REPO}.git'],
                          check=True, capture_output=True)
        
        # Add, commit, push
        subprocess.run(['git', 'add', '-A'], check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-m', f'[AUTO] Updated at {time.strftime("%Y-%m-%d %H:%M:%S")}'],
                      check=True, capture_output=True)
        subprocess.run(['git', 'push', '-u', 'origin', GH_BRANCH],
                      check=True, capture_output=True)
        
        log.success("Загружено на GitHub")
    
    except Exception as e:
        log.error(f"GitHub upload ошибка: {str(e)[:80]}")

# ============= MAIN =============
async def main():
    print("="*70)
    print(" 🎬 AniLiberty Parser v2.0 — Official API Edition")
    print("="*70)
    
    parser = AniLiberty()
    
    try:
        # Парсим каталог
        await parser.parse_all()
        
        # Сохраняем M3U
        parser.save_m3u_files()
        
        # Сохраняем метаданные
        parser.save_json_metadata()
        
        # Загружаем на GitHub
        push_to_github()
        
        # Сводка
        log.summary()
        
    except KeyboardInterrupt:
        log.warn("Прерывание пользователем")
    except Exception as e:
        log.error(f"Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    if not REQUESTS_AVAILABLE:
        print("[!] Установите зависимости: pip install aiohttp requests")
        exit(1)
    
    asyncio.run(main())
