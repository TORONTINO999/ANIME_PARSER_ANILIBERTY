#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import concurrent.futures
import requests
import json
import threading
import time
import sys
from pathlib import Path

BASE_URL = "https://aniliberty.top/api/v1/anime/catalog/releases"
LIMIT = 50          # максимум по API
THREADS = 20        # уменьшено для стабильности
MAX_RETRIES = 5     # попыток на страницу

# Имена файлов
IDS_TXT = "aniliberty_ids.txt"
IDS_JSON = "aniliberty_ids.json"
PROGRESS_FILE = "aniliberty_progress.json"

session = requests.Session()
session.headers.update({
    "User-Agent": "AniLiberty-Scraper/2.0"
})

lock = threading.Lock()

# Глобальные переменные для статистики
stats = {
    "success": 0,
    "failed": 0,
    "total_ids": 0
}


def first_request():
    """Получаем первую страницу и мета-информацию."""
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(
                BASE_URL,
                params={"page": 1, "limit": LIMIT, "include": "id"},
                timeout=30
            )
            r.raise_for_status()
            data = r.json()
            return data
        except Exception as e:
            print(f"❌ Не удалось получить первую страницу (попытка {attempt+1}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
            else:
                raise


def load_page(page):
    """Загружает одну страницу, возвращает список ID."""
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(
                BASE_URL,
                params={"page": page, "limit": LIMIT, "include": "id"},
                timeout=30
            )
            r.raise_for_status()
            data = r.json()
            ids = [item["id"] for item in data["data"]]

            with lock:
                stats["success"] += 1
                stats["total_ids"] += len(ids)

            return page, ids, None

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                delay = 2 ** attempt
                time.sleep(delay)
            else:
                with lock:
                    stats["failed"] += 1
                return page, [], str(e)

    return page, [], "max retries exceeded"


def save_progress(all_ids_set, completed_pages, total_pages):
    """Сохраняет промежуточный прогресс."""
    progress = {
        "unique_ids_count": len(all_ids_set),
        "completed_pages": completed_pages,
        "total_pages": total_pages,
        "min_id": min(all_ids_set) if all_ids_set else None,
        "max_id": max(all_ids_set) if all_ids_set else None,
        "stats": stats.copy()
    }

    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def save_final(all_ids_sorted):
    """Сохраняет финальные результаты."""
    # Текстовый файл с ID
    with open(IDS_TXT, "w", encoding="utf-8") as f:
        for i in all_ids_sorted:
            f.write(f"{i}\n")

    # JSON файл со всеми ID
    with open(IDS_JSON, "w", encoding="utf-8") as f:
        json.dump(all_ids_sorted, f, ensure_ascii=False)

    print(f"\n✅ Файлы сохранены:")
    print(f"   {IDS_TXT}")
    print(f"   {IDS_JSON}")
    print(f"   {PROGRESS_FILE}")


def main():
    print("=" * 70)
    print("🔍 AniLiberty — получение всех ID релизов")
    print("=" * 70)

    # Первый запрос для мета-информации
    print("\n📡 Запрашиваю первую страницу...")
    first = first_request()

    meta = first["meta"]["pagination"]
    total_releases = meta["total"]
    total_pages = meta["total_pages"]

    print(f"""
📊 Информация о каталоге:
   Всего релизов (заявлено): {total_releases}
   Всего страниц:            {total_pages}
   Лимит на страницу:        {LIMIT}
   Потоков:                  {THREADS}
   Ожидаемое время:          ~{total_pages * 0.1 / THREADS:.1f} сек.
""")

    # Собираем ID с первой страницы
    all_ids = set(item["id"] for item in first["data"])
    print(f"✅ Страница 1/{total_pages}: получено {len(all_ids)} ID")

    # Сохраняем прогресс
    save_progress(all_ids, 1, total_pages)

    # Загружаем остальные страницы в потоках
    remaining_pages = list(range(2, total_pages + 1))
    failed_pages = []

    print(f"\n🚀 Загружаю оставшиеся {len(remaining_pages)} страниц...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = {executor.submit(load_page, page): page for page in remaining_pages}

        completed_after_first = 0

        for future in concurrent.futures.as_completed(futures):
            page, ids, error = future.result()
            completed_after_first += 1

            if error:
                failed_pages.append(page)
                print(f"❌ Страница {page}/{total_pages}: ОШИБКА — {error}")
            else:
                all_ids.update(ids)
                print(f"✅ Страница {page}/{total_pages}: +{len(ids)} ID (всего уникальных: {len(all_ids)})")

            # Периодически сохраняем прогресс
            if completed_after_first % 100 == 0:
                save_progress(all_ids, completed_after_first + 1, total_pages)

    # Сортируем
    all_ids_sorted = sorted(all_ids)

    # Финальный вывод
    print("\n" + "=" * 70)
    print("📈 РЕЗУЛЬТАТЫ")
    print("=" * 70)
    print(f"""
   Заявлено релизов (total):   {total_releases}
   Фактически получено ID:     {len(all_ids_sorted)}
   Успешно загружено страниц:  {stats['success']}
   Страниц с ошибками:         {stats['failed']}
   Минимальный ID:             {all_ids_sorted[0]}
   Максимальный ID:            {all_ids_sorted[-1]}
""")

    if total_releases != len(all_ids_sorted):
        print(f"⚠️  Расхождение! Разница: {total_releases - len(all_ids_sorted)}")
        print("   Возможные причины: скрытые релизы, дубликаты на разных страницах,")
        print("   или проблемы с пагинацией на стороне API.")

    if failed_pages:
        print(f"\n⚠️  Не загружено {len(failed_pages)} страниц: {failed_pages[:10]}...")

    # Сохраняем результат
    save_final(all_ids_sorted)
    save_progress(set(all_ids_sorted), total_pages, total_pages)

    # Удаляем файл прогресса, если всё ок
    if not failed_pages and total_releases == len(all_ids_sorted):
        Path(PROGRESS_FILE).unlink(missing_ok=True)
        print("   (файл прогресса удалён — загрузка успешна)")

    print("\n🏁 Готово!")
    return 0 if not failed_pages else 1


if __name__ == "__main__":
    sys.exit(main())
