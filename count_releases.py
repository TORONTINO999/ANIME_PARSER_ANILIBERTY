import requests
import sys
import time

API_URL = "https://anilibria.top/api/v1/anime/catalog/releases"

def get_total_releases():
    """
    Получает точное количество релизов через мета-данные пагинации.
    Не скачивает список релизов, только счетчики.
    """
    headers = {
        "User-Agent": "AniLibertyCounter/1.0 (CI Check)",
        "Accept": "application/json"
    }
    
    # Запрашиваем limit=1, чтобы получить meta.pagination.total 
    # с минимальным объемом данных
    params = {
        "limit": 1,
        "page": 1
    }

    try:
        print(f"[{time.strftime('%H:%M:%S')}] 📡 Запрос к {API_URL}...", flush=True)
        resp = requests.get(API_URL, params=params, headers=headers, timeout=15)
        
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 10))
            print(f"⏳ Rate-limit. Ждем {wait}с...", flush=True)
            time.sleep(wait)
            resp = requests.get(API_URL, params=params, headers=headers, timeout=15)

        resp.raise_for_status()
        data = resp.json()
        
        # Извлекаем total из meta.pagination согласно OAS 3.0
        meta = data.get("meta", {})
        pagination = meta.get("pagination", {})
        total = pagination.get("total")
        
        if total is not None:
            print(f"\n✅ ВСЕГО РЕЛИЗОВ НА ANILIBERTY: {total}", flush=True)
            return total
        else:
            print("❌ Поле 'total' отсутствует в ответе API.", flush=True)
            print(f"Ответ: {data}", flush=True)
            sys.exit(1)
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Ошибка сети: {e}", flush=True)
        sys.exit(1)
    except ValueError as e:
        print(f"❌ Ошибка парсинга JSON: {e}", flush=True)
        sys.exit(1)

if __name__ == "__main__":
    get_total_releases()
