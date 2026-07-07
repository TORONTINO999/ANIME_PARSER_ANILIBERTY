#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://aniliberty.top/api/v1"


def session():
    s = requests.Session()

    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )

    adapter = HTTPAdapter(max_retries=retry)

    s.mount("https://", adapter)
    s.mount("http://", adapter)

    s.headers.update({
        "User-Agent": "AniLiberty-Test/1.0",
        "Accept": "application/json"
    })

    return s


def show(resp):
    print("HTTP:", resp.status_code)

    try:
        data = resp.json()
    except Exception:
        print(resp.text[:1000])
        return

    print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])

    meta = data.get("meta")

    if isinstance(meta, dict):
        pag = meta.get("pagination")
        if isinstance(pag, dict):
            print("\n>>>> НАЙДЕНО PAGINATION <<<<")
            print(json.dumps(pag, ensure_ascii=False, indent=2))

            if "total" in pag:
                print(f"\nВСЕГО РЕЛИЗОВ = {pag['total']}")


def test_get(name, url, params=None):
    print("\n" + "=" * 80)
    print(name)
    print(url)
    print("params =", params)
    print("=" * 80)

    try:
        r = S.get(url, params=params, timeout=(10, 60))
        show(r)
    except Exception as e:
        print("ERROR:", e)


def test_post(name, url, body=None):
    print("\n" + "=" * 80)
    print(name)
    print(url)
    print("=" * 80)

    try:
        r = S.post(url, json=body or {}, timeout=(10, 60))
        show(r)
    except Exception as e:
        print("ERROR:", e)


S = session()

# -------------------------------------------------------------------
# СПОСОБ 1
# -------------------------------------------------------------------

test_get(
    "1. anime/releases/list?page=1&limit=1",
    BASE + "/anime/releases/list",
    {
        "page": 1,
        "limit": 1
    }
)

# -------------------------------------------------------------------
# СПОСОБ 2
# -------------------------------------------------------------------

test_get(
    "2. anime/catalog/releases?page=1&limit=1",
    BASE + "/anime/catalog/releases",
    {
        "page": 1,
        "limit": 1
    }
)

# -------------------------------------------------------------------
# СПОСОБ 3
# -------------------------------------------------------------------

test_post(
    "3. POST anime/catalog/releases",
    BASE + "/anime/catalog/releases",
    {
        "page": 1,
        "limit": 1
    }
)

print("\nГотово.")
