#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AniLiberty Parser Fast — Параллельная версия с кэшем
Обработка всех ~3750 аниме с оптимизацией и прогрессом
"""

import os
import re
import json
import asyncio
from pathlib import Path
from typing import Dict, List
import aiohttp
import time

API_BASE = "https://anilibria.top/api/v1/anime"
POSTER_BASE = "https://anilibria.top/storage/releases/posters"
MIRRORS_DIR = "mirrors"
CACHE_FILE = "parse_cache.json"

# Параллельные запросы
CONCURRENT_REQUESTS = 10
BATCH_SIZE = 50

class FastParser:
    def __init__(self):
        self.cache = self.load_cache()
        self.animes = {}
        self.processed = 0
        self.total = 0
        self.start_time = time.time()
    
    def load_cache(self) -> dict:
        """Загружаем кэш предыдущего запуска"""
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def save_cache(self):
        """Сохраняем кэш"""
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f)
    
    async def fetch_with_retry(self, session, url: str, retries=3):
        """Fetch с retry"""
        for attempt in range(retries):
            try:
                async with session.get(url, timeout=15) as resp:
                    if resp.status == 200:
                        return await resp.json()
            except asyncio.TimeoutError:
                await asyncio.sleep(2 ** attempt)
            except:
                pass
        return None
    
    async def process_batch(self, session, releases: List[dict]):
        """Обрабатываем батч релизов параллельно"""
        tasks = []
        for release in releases:
            task = self.process_release(session, release)
            tasks.append(task)
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def process_release(self, session, release: dict):
        """Обрабатываем релиз"""
        try:
            release_id = release.get('id')
            alias = release.get('alias')
            title = release.get('name', {}).get('main', '')
            
            if not release_id or not title:
                return
            
            cache_key = f"release_{release_id}"
            
            # Проверяем кэш
            if cache_key in self.cache:
                self.animes[alias] = self.cache[cache_key]
                self.processed += 1
                return
            
            # Получаем детали
            url = f"{API_BASE}/releases/{alias}"
            details = await self.fetch_with_retry(session, url)
            
            if not details:
                return
            
            # Извлекаем данные
            anime_data = {
                'id': release_id,
                'alias': alias,
                'title': title,
                'year': release.get('year'),
                'type': release.get('type', {}).get('value'),
                'genres': [g.get('name', '') for g in release.get('genres', [])],
                'episodes': [],
                'poster': release.get('poster', {}).get('src')
            }
            
            # Парсим эпизоды
            episodes = details.get('episodes', {})
            if isinstance(episodes, dict):
                for ep_num, ep_data in episodes.items():
                    hls_url = self.extract_hls(ep_data)
                    if hls_url:
                        anime_data['episodes'].append({
                            'num': ep_num,
                            'hls': hls_url
                        })
            
            # Сохраняем
            self.animes[alias] = anime_data
            self.cache[cache_key] = anime_data
            
            self.processed += 1
            if self.processed % 100 == 0:
                elapsed = time.time() - self.start_time
                rate = self.processed / elapsed
                eta = (self.total - self.processed) / rate if rate > 0 else 0
                print(f"  [{self.processed}/{self.total}] ETA {int(eta)}s")
        
        except Exception as e:
            pass
    
    def extract_hls(self, ep_data: dict) -> str:
        """Извлекаем HLS URL"""
        try:
            if 'players' in ep_data:
                for player_data in ep_data['players'].values():
                    if isinstance(player_data, dict):
                        if 'data' in player_data and 'hls' in player_data['data']:
                            hls = player_data['data']['hls']
                            if isinstance(hls, list) and hls:
                                return hls[0]
                            elif isinstance(hls, str):
                                return hls
                        if 'files' in player_data and 'hls' in player_data['files']:
                            hls = player_data['files']['hls']
                            if isinstance(hls, list) and hls:
                                return hls[0]
                            elif isinstance(hls, str):
                                return hls
        except:
            pass
        return None
    
    async def run(self):
        """Главный цикл"""
        print("🚀 Fast Parser запущен")
        
        os.makedirs(MIRRORS_DIR, exist_ok=True)
        
        connector = aiohttp.TCPConnector(limit_per_host=CONCURRENT_REQUESTS)
        timeout = aiohttp.ClientTimeout(total=60)
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            # Загружаем все релизы со всех страниц
            all_releases = []
            
            print("📥 Загружаем каталог...")
            
            for page in range(1, 76):
                url = f"{API_BASE}/catalog/releases"
                params = {'page': page, 'limit': 50}
                
                data = await self.fetch_with_retry(session, url)
                if data and 'data' in data:
                    all_releases.extend(data['data'])
                
                if page % 10 == 0:
                    print(f"  Page {page}/75 -> {len(all_releases)} релизов")
                
                await asyncio.sleep(0.2)
            
            self.total = len(all_releases)
            print(f"\n📊 Всего релизов: {self.total}")
            print(f"💾 Кэш ранее: {len(self.cache)} записей")
            print("\n⚙️ Обработка...")
            
            # Обрабатываем батчами
            for i in range(0, len(all_releases), BATCH_SIZE):
                batch = all_releases[i:i + BATCH_SIZE]
                await self.process_batch(session, batch)
            
            # Сохраняем кэш
            self.save_cache()
            
            print(f"\n✓ Обработано: {len(self.animes)} аниме")
            print("💾 M3U генерируем...")
            
            # Генерируем M3U
            self.generate_m3u()
            
            elapsed = time.time() - self.start_time
            print(f"\n✅ Готово за {int(elapsed)}s")
    
    def generate_m3u(self):
        """Генерируем M3U плейлисты"""
        main_m3u = "#EXTM3U\n"
        
        for alias, data in self.animes.items():
            if not data['episodes']:
                continue
            
            # Папка аниме
            safe_name = re.sub(r'[<>:"/\\|?*]', '_', data['title'])[:100]
            anime_dir = os.path.join(MIRRORS_DIR, safe_name)
            os.makedirs(anime_dir, exist_ok=True)
            
            # M3U аниме
            m3u_content = f"#EXTM3U\n#Название: {data['title']}\n#Эпизодов: {len(data['episodes'])}\n\n"
            
            for ep in data['episodes']:
                m3u_content += f"#EXTINF:-1,{data['title']} - Серия {ep['num']}\n{ep['hls']}\n\n"
            
            anime_m3u_path = os.path.join(anime_dir, f"{safe_name}.m3u8")
            with open(anime_m3u_path, 'w', encoding='utf-8') as f:
                f.write(m3u_content)
            
            # Главный M3U
            main_m3u += f"#EXTINF:-1 group-title=\"{data['type']}\",{data['title']}\n{anime_m3u_path}\n\n"
        
        with open("aniliberty.m3u8", 'w', encoding='utf-8') as f:
            f.write(main_m3u)

if __name__ == '__main__':
    asyncio.run(FastParser().run())
