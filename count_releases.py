import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import sys
import json

# Базовый URL API (по умолчанию). При необходимости измените или передайте через аргумент.
DEFAULT_BASE_URL = "https://aniliberty.top/api/v1"

def get_total_releases(base_url, timeout=10, retries=3):
    """
    Выполняет запрос к /anime/releases/list и возвращает общее количество релизов.
    """
    session = requests.Session()

    # Настройка повторных попыток при тайм-аутах или ошибках 5xx
    retry_strategy = Retry(
        total=retries,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    url = f"{base_url}/anime/releases/list"
    params = {
        "page": 1,
        "limit": 1  # достаточно одной записи, чтобы получить мета-информацию
    }

    try:
        response = session.get(url, params=params, timeout=timeout)
        response.raise_for_status()  # выбросит исключение при 4xx/5xx

        data = response.json()
        total = data.get("meta", {}).get("pagination", {}).get("total")

        if total is None:
            print("Не удалось найти поле 'total' в ответе API.")
            print("Ответ сервера:", json.dumps(data, indent=2, ensure_ascii=False))
            return None

        return total

    except requests.exceptions.RequestException as e:
        print(f"Ошибка при выполнении запроса: {e}")
        return None

def main():
    # Можно передать базовый URL через аргумент командной строки
    base_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE_URL
    print(f"Используется базовый URL: {base_url}")

    total = get_total_releases(base_url)
    if total is not None:
        print(f"Общее количество релизов: {total}")
    else:
        print("Не удалось получить количество релизов.")
        sys.exit(1)

if __name__ == "__main__":
    main()
