#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import concurrent.futures
import requests
import json
import threading
import time

BASE_URL = "https://aniliberty.top/api/v1/anime/catalog/releases"

# Максимальный лимит API
LIMIT = 50

# Количество потоков
THREADS = 50

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0"
})

lock = threading.Lock()


def first_request():
    r = session.get(
        BASE_URL,
        params={
            "page": 1,
            "limit": LIMIT,
            "include": "id"
        },
        timeout=30
    )

    r.raise_for_status()
    return r.json()


def load_page(page):
    for attempt in range(5):
        try:
            r = session.get(
                BASE_URL,
                params={
                    "page": page,
                    "limit": LIMIT,
                    "include": "id"
                },
                timeout=30
            )

            r.raise_for_status()

            data = r.json()

            ids = [item["id"] for item in data["data"]]

            with lock:
                print(f"[{page}] получено {len(ids)} ID")

            return ids

        except Exception as e:
            with lock:
                print(f"[{page}] Ошибка ({attempt+1}/5): {e}")

            time.sleep(1)

    return []


def main():
    first = first_request()

    total = first["meta"]["pagination"]["total"]
    total_pages = first["meta"]["pagination"]["total_pages"]

    print("=" * 60)
    print("Всего релизов :", total)
    print("Всего страниц :", total_pages)
    print("Лимит         :", LIMIT)
    print("Потоков       :", THREADS)
    print("=" * 60)

    all_ids = [item["id"] for item in first["data"]]

    with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = [
            executor.submit(load_page, page)
            for page in range(2, total_pages + 1)
        ]

        completed = 1

        for future in concurrent.futures.as_completed(futures):
            ids = future.result()

            all_ids.extend(ids)

            completed += 1

            print(
                f"Прогресс: {completed}/{total_pages} "
                f"страниц | ID: {len(all_ids)}"
            )

    # Удаляем дубликаты и сортируем
    all_ids = sorted(set(all_ids))

    with open("ids.txt", "w", encoding="utf-8") as f:
        for i in all_ids:
            f.write(f"{i}\n")

    with open("ids.json", "w", encoding="utf-8") as f:
        json.dump(all_ids, f, ensure_ascii=False, indent=4)

    print()
    print("=" * 60)
    print("ГОТОВО!")
    print(f"Получено уникальных ID: {len(all_ids)}")
    print(f"Минимальный ID: {min(all_ids)}")
    print(f"Максимальный ID: {max(all_ids)}")
    print("Файлы сохранены:")
    print("  ids.txt")
    print("  ids.json")
    print("=" * 60)


if __name__ == "__main__":
    main()
